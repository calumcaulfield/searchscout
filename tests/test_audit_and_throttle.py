"""The audit trail is the recovery mechanism, so it is tested like one."""

from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest

from helpers import temp_catalog
from searchscout.audit import AuditEntry, AuditLog
from searchscout.matching import MatchMode
from searchscout.planner import apply, plan
from searchscout.throttle import TokenBucket


def entry(sku: str = "DEMO-1000") -> AuditEntry:
    return AuditEntry(
        plan_id="abc123",
        sku=sku,
        product_name="Sage Cotton Storage Basket",
        search_term="cotton",
        replacement="hemp",
        match_mode="case-insensitive",
        replacements=2,
        contents_before="<p>cotton</p>",
        contents_after="<p>hemp</p>",
    )


class TestAuditLog:
    def test_writes_a_header_once(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "a.csv")
        log.append([entry("DEMO-1")])
        log.append([entry("DEMO-2")])
        rows = list(csv.DictReader((tmp_path / "a.csv").open(encoding="utf-8")))
        assert len(rows) == 2
        assert rows[0]["sku"] == "DEMO-1"

    def test_records_the_previous_content(self, tmp_path: Path) -> None:
        """Without the before-value the log is a notification, not a recovery record."""
        log = AuditLog(tmp_path / "a.csv")
        log.append([entry()])
        row = next(csv.DictReader((tmp_path / "a.csv").open(encoding="utf-8")))
        assert row["contents_before"] == "<p>cotton</p>"
        assert row["search_term"] == "cotton"

    def test_is_written_per_product_so_a_crash_leaves_a_record(self, tmp_path: Path) -> None:
        catalog = temp_catalog(product_count=12)
        log = AuditLog(tmp_path / "a.csv")
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        apply(catalog, result, audit=log, rollback_dir=tmp_path / "r", rate_per_second=1000)
        rows = list(csv.DictReader((tmp_path / "a.csv").open(encoding="utf-8")))
        assert len(rows) == result.product_count

    def test_rollback_file_round_trips(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "a.csv")
        path = log.write_rollback("abc123", [entry("DEMO-9")], tmp_path / "r")
        assert log.read_rollback(path) == [("DEMO-9", "<p>cotton</p>")]


class TestTokenBucket:
    def test_a_burst_passes_without_waiting(self) -> None:
        bucket = TokenBucket(rate_per_second=1.0, burst=3)
        assert [bucket.acquire(sleep=False) for _ in range(3)] == [0.0, 0.0, 0.0]

    def test_the_fourth_request_has_to_wait(self) -> None:
        bucket = TokenBucket(rate_per_second=1.0, burst=3)
        for _ in range(3):
            bucket.acquire(sleep=False)
        assert bucket.acquire(sleep=False) > 0

    def test_tokens_refill_over_time(self) -> None:
        bucket = TokenBucket(rate_per_second=50.0, burst=1)
        bucket.acquire(sleep=False)
        time.sleep(0.05)
        assert bucket.acquire(sleep=False) == 0.0

    def test_a_non_positive_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            TokenBucket(rate_per_second=0)
