"""`make demo` — the whole safety story against the bundled catalogue.

Search, preview, apply, verify the markup survived, then roll back. Runs with no
credentials and touches no real store.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from searchscout.audit import AuditLog
from searchscout.catalog.demo import DemoCatalog
from searchscout.html_edit import tag_signature
from searchscout.matching import MatchMode
from searchscout.planner import apply, plan, rollback

console = Console()


def main() -> int:
    catalog = DemoCatalog(product_count=200)
    console.print(
        Panel(
            f"{len(catalog.iter_products())} synthetic products. "
            "Nothing here describes a real store.",
            title="demo catalogue",
            border_style="dim",
        )
    )

    before = {p.sku: p.contents for p in catalog.iter_products()}
    structure_before = {sku: tag_signature(html) for sku, html in before.items()}

    # 1. Plan — read-only.
    result = plan(catalog, "cotton", "organic cotton", match_mode=MatchMode.WHOLE_WORD)
    table = Table(title="Planned edits (first 5)", box=None)
    table.add_column("SKU")
    table.add_column("Edits", justify="right")
    table.add_column("Before → after", overflow="fold")
    for change in result.changes[:5]:
        b, a = change.fragments[0] if change.fragments else ("", "")
        table.add_row(change.sku, str(change.replacements), f"{b[:38]} → {a[:38]}")
    console.print(table)
    console.print(
        f"  plan: {result.product_count} products, {result.total_replacements} replacements — "
        f"[bold]{len(catalog.writes)} writes so far[/bold] ✓"
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit = AuditLog(root / "audit.csv")

        # 2. Apply.
        report = apply(
            catalog, result, audit=audit, rollback_dir=root / "rollback", rate_per_second=1000
        )
        console.print(
            f"  applied: {len(report.updated)} products updated, {len(report.failed)} failed ✓"
        )

        # 3. The property that matters: text changed, markup did not.
        corrupted = [
            p.sku
            for p in catalog.iter_products()
            if tag_signature(p.contents) != structure_before[p.sku]
        ]
        console.print(
            f"  markup: {len(corrupted)} products with altered tag structure "
            f"{'✓' if not corrupted else '✗'}"
        )

        # 4. Roll back and prove it is byte-for-byte.
        assert report.rollback_path is not None
        rollback(catalog, audit, report.rollback_path)
        restored = all(p.contents == before[p.sku] for p in catalog.iter_products())
        console.print(
            f"  rollback: every product restored byte-for-byte {'✓' if restored else '✗'}"
        )

    console.print(
        Panel(
            "Plan is read-only. Apply writes exactly what the plan showed.\n"
            "Every write is audited before the next is attempted, and the\n"
            "rollback file restores the previous content exactly.",
            title="summary",
            border_style="dim",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
