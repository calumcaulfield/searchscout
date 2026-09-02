"""What was changed, when, and what it looked like before.

The original wrote a CSV with the full before-and-after content per row, which
is the right instinct — an operator who renames the wrong term needs the old
value back. It kept that CSV next to the process, appended without locking, and
never recorded the search that caused the change.

This keeps the CSV (it is what the operators actually opened) and adds the two
things that make it usable as a recovery record: the plan that produced the
change, and a rollback file that can be replayed.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

FIELDNAMES = [
    "timestamp",
    "plan_id",
    "sku",
    "product_name",
    "search_term",
    "replacement",
    "match_mode",
    "replacements",
    "contents_before",
    "contents_after",
]


@dataclass(frozen=True)
class AuditEntry:
    plan_id: str
    sku: str
    product_name: str
    search_term: str
    replacement: str
    match_mode: str
    replacements: int
    contents_before: str
    contents_after: str
    timestamp: str = ""

    def row(self) -> dict[str, str | int]:
        return {
            "timestamp": self.timestamp or datetime.now(UTC).isoformat(timespec="seconds"),
            "plan_id": self.plan_id,
            "sku": self.sku,
            "product_name": self.product_name,
            "search_term": self.search_term,
            "replacement": self.replacement,
            "match_mode": self.match_mode,
            "replacements": self.replacements,
            "contents_before": self.contents_before,
            "contents_after": self.contents_after,
        }


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entries: list[AuditEntry]) -> None:
        if not entries:
            return
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            if new_file:
                writer.writeheader()
            for entry in entries:
                writer.writerow(entry.row())

    def write_rollback(self, plan_id: str, entries: list[AuditEntry], directory: Path) -> Path:
        """Write a file that restores every product this plan touched.

        Kept separate from the CSV because a rollback has to be machine-readable
        and exact; a spreadsheet that has been opened and re-saved is neither.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{plan_id}.json"
        path.write_text(
            json.dumps(
                {
                    "plan_id": plan_id,
                    "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "restore": [
                        {"sku": entry.sku, "contents": entry.contents_before} for entry in entries
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def read_rollback(self, path: Path) -> list[tuple[str, str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(item["sku"], item["contents"]) for item in data["restore"]]


# --------------------------------------------------------------- operations
#
# The CSV records one row per product, which is what a recovery needs. It is
# the wrong shape for "what did we do last week": answering that from it means
# re-deriving operations by grouping on plan_id every time. Operations are
# therefore also appended as a summary each, one JSON object per line.


@dataclass(frozen=True)
class Operation:
    plan_id: str
    kind: str  # "bulk_update" | "rollback"
    search_term: str
    replacement: str
    match_mode: str
    requested: int
    written: int
    verified: int
    failed: int
    replacements: int
    rollback_path: str | None
    at: str

    @property
    def fully_verified(self) -> bool:
        return self.failed == 0 and self.verified == self.requested

    @property
    def can_roll_back(self) -> bool:
        return self.kind == "bulk_update" and bool(self.rollback_path) and self.verified > 0


class OperationLog:
    """Append-only history of bulk operations, newest first when read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, operation: Operation) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(operation)) + "\n")

    def read(self, limit: int = 50) -> list[Operation]:
        if not self.path.exists():
            return []
        operations: list[Operation] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                operations.append(Operation(**json.loads(line)))
            except (TypeError, ValueError):
                # A malformed line is skipped rather than breaking the page.
                # The CSV remains the authoritative record either way.
                continue
        return list(reversed(operations))[:limit]

    def get(self, plan_id: str) -> Operation | None:
        return next((op for op in self.read(limit=10_000) if op.plan_id == plan_id), None)

    def clear(self) -> None:
        """Demo only: part of resetting to a known state."""
        self.path.unlink(missing_ok=True)
