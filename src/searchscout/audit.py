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
from dataclasses import dataclass
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
