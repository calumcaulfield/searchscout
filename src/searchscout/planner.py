"""Plan, preview, apply.

The original's `bulk_update_products` searched and wrote in one pass: by the
time an operator saw any output, the catalogue had already changed. There was
no preview and no way to answer "what is this about to do" before it did it.

Splitting the operation in two is the whole design. `plan()` is read-only and
returns exactly what `apply()` will write; `apply()` performs no matching of
its own, so the preview cannot disagree with the result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from searchscout.audit import AuditEntry, AuditLog
from searchscout.catalog.base import CatalogAdapter, CatalogError
from searchscout.html_edit import EditResult, replace_in_text
from searchscout.matching import MatchMode, count_matches
from searchscout.throttle import TokenBucket


@dataclass
class PlannedChange:
    sku: str
    product_name: str
    before: str
    after: str
    replacements: int
    fragments: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Plan:
    id: str
    search_term: str
    replacement: str
    match_mode: MatchMode
    changes: list[PlannedChange] = field(default_factory=list)
    #: Products whose text matched but which produced no edit. Always empty
    #: now; the field exists because it was not empty in the original, and a
    #: non-zero value here means find and replace have diverged again.
    matched_but_unchanged: list[str] = field(default_factory=list)

    @property
    def product_count(self) -> int:
        return len(self.changes)

    @property
    def total_replacements(self) -> int:
        return sum(change.replacements for change in self.changes)


@dataclass
class ApplyReport:
    """What an apply actually achieved, at each stage.

    The four counts are separate on purpose. A write that the backend accepted
    is not the same as a write that is stored: the previous version reported
    "32 products updated" whenever `update_contents` returned without raising,
    which was true even when the change went nowhere. `verified` is the only
    number a user should be shown as a success.
    """

    plan_id: str
    #: SKUs the plan asked to change.
    requested: list[str] = field(default_factory=list)
    #: The adapter accepted the write without raising.
    written: list[str] = field(default_factory=list)
    #: Read back afterwards and confirmed to hold the planned content.
    verified: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    replacements: int = 0
    rollback_path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def updated(self) -> list[str]:
        """Kept for callers that predate the staged counts. Verified only."""
        return self.verified


def plan(
    catalog: CatalogAdapter,
    search_term: str,
    replacement: str,
    *,
    match_mode: MatchMode = MatchMode.LITERAL,
    limit: int | None = None,
) -> Plan:
    """Work out every edit without performing any of them.

    Read-only by construction: nothing here calls `update_contents`.
    """
    if not search_term:
        raise ValueError("search term must not be empty")

    result = Plan(
        id=uuid.uuid4().hex[:12],
        search_term=search_term,
        replacement=replacement,
        match_mode=match_mode,
    )

    for product in catalog.iter_products():
        if not product.contents:
            continue
        edit: EditResult = replace_in_text(product.contents, search_term, replacement, match_mode)
        if edit.changed:
            result.changes.append(
                PlannedChange(
                    sku=product.sku,
                    product_name=product.name,
                    before=product.contents,
                    after=edit.html,
                    replacements=edit.replacements,
                    fragments=edit.changed_fragments,
                )
            )
        elif count_matches(product.contents, search_term, match_mode):
            # Matched the raw markup but produced no text-node edit: the term
            # only occurs inside a tag, an attribute or a script.
            result.matched_but_unchanged.append(product.sku)

    if limit is not None:
        result.changes = result.changes[:limit]
    return result


def apply(
    catalog: CatalogAdapter,
    plan_to_apply: Plan,
    *,
    audit: AuditLog,
    rollback_dir: Path,
    rate_per_second: float = 4.0,
    max_products: int = 500,
    only_skus: set[str] | None = None,
) -> ApplyReport:
    """Write the plan. Every write is audited before the next one is attempted.

    A cap is enforced here rather than trusted to the caller: a mistyped
    one-character search term matches most of a catalogue, and the difference
    between a bad edit and an outage is whether anything stopped it.
    """
    if plan_to_apply.product_count > max_products:
        raise CatalogError(
            f"plan touches {plan_to_apply.product_count} products, above the "
            f"{max_products} cap; narrow the search or raise --max-products deliberately"
        )

    report = ApplyReport(plan_id=plan_to_apply.id)
    bucket = TokenBucket(rate_per_second=rate_per_second)
    entries: list[AuditEntry] = []

    # The operator may have deselected rows in the preview. Filtering here, from
    # the plan the server computed, means the form cannot introduce a SKU that
    # was never planned.
    changes = [
        change for change in plan_to_apply.changes if only_skus is None or change.sku in only_skus
    ]

    for change in changes:
        bucket.acquire()
        report.requested.append(change.sku)
        try:
            catalog.update_contents(change.sku, change.after)
        except CatalogError as exc:
            report.failed.append((change.sku, str(exc)))
            continue
        report.written.append(change.sku)

        # Read it back. A write the adapter accepted is not proof the store
        # holds it: the demo catalogue used to accept writes into an object
        # that was about to be discarded, and nothing raised. Only a fresh
        # read can tell the difference, and only a match counts as success.
        try:
            fresh = catalog.get_product(change.sku)
        except CatalogError as exc:
            report.failed.append((change.sku, f"could not re-read after write: {exc}"))
            continue

        if fresh.contents != change.after:
            report.failed.append(
                (
                    change.sku,
                    "write was accepted but the stored content does not match the plan",
                )
            )
            continue

        report.verified.append(change.sku)
        report.replacements += change.replacements

        entry = AuditEntry(
            plan_id=plan_to_apply.id,
            sku=change.sku,
            product_name=change.product_name,
            search_term=plan_to_apply.search_term,
            replacement=plan_to_apply.replacement,
            match_mode=str(plan_to_apply.match_mode),
            replacements=change.replacements,
            contents_before=change.before,
            contents_after=change.after,
        )
        entries.append(entry)
        # Written per product, not batched at the end: a crash halfway through
        # must still leave a record of what was already changed.
        audit.append([entry])

    if entries:
        report.rollback_path = audit.write_rollback(plan_to_apply.id, entries, rollback_dir)
    return report


def rollback(catalog: CatalogAdapter, audit: AuditLog, path: Path) -> ApplyReport:
    """Restore every product recorded in a rollback file to its previous content."""
    report = ApplyReport(plan_id=path.stem)
    bucket = TokenBucket(rate_per_second=4.0)
    for sku, contents in audit.read_rollback(path):
        bucket.acquire()
        report.requested.append(sku)
        try:
            catalog.update_contents(sku, contents)
        except CatalogError as exc:
            report.failed.append((sku, str(exc)))
            continue
        report.written.append(sku)

        # Restoring content is a write like any other, so it is verified the
        # same way. A rollback that silently did nothing would be worse than
        # the update it was undoing.
        try:
            fresh = catalog.get_product(sku)
        except CatalogError as exc:
            report.failed.append((sku, f"could not re-read after restore: {exc}"))
            continue
        if fresh.contents != contents:
            report.failed.append((sku, "restore was accepted but the stored content differs"))
            continue
        report.verified.append(sku)
    return report
