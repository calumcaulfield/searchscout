"""Read-after-write verification.

"The adapter did not raise" was the old definition of success, and it was the
reason a bulk update could report 32 products updated while storing nothing.
Success now requires reading the product back and finding the planned content.
"""

from __future__ import annotations

from pathlib import Path

from helpers import temp_catalog
from searchscout.audit import AuditLog
from searchscout.catalog.base import CatalogError, Product
from searchscout.matching import MatchMode
from searchscout.planner import apply, plan


class SilentlyDiscardingCatalog:
    """Accepts every write and stores none — the old demo adapter's behaviour.

    Nothing raises, so only a fresh read can tell that the update went nowhere.
    """

    name = "discarding"

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self.accepted: list[str] = []

    def iter_products(self) -> list[Product]:
        return self._inner.iter_products()

    def get_product(self, sku: str) -> Product:
        return self._inner.get_product(sku)

    def update_contents(self, sku: str, contents: str) -> None:
        self.accepted.append(sku)  # accepted, and deliberately not stored


class HalfDiscardingCatalog(SilentlyDiscardingCatalog):
    """Stores every write except one, to prove partial failure is reported."""

    def __init__(self, inner, skip_sku: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(inner)
        self.skip_sku = skip_sku

    def update_contents(self, sku: str, contents: str) -> None:
        self.accepted.append(sku)
        if sku != self.skip_sku:
            self._inner.update_contents(sku, contents)


class RefusingCatalog(SilentlyDiscardingCatalog):
    def update_contents(self, sku: str, contents: str) -> None:
        raise CatalogError("the store refused the write")


def run(catalog, tmp_path: Path):  # type: ignore[no-untyped-def]
    result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
    report = apply(
        catalog,
        result,
        audit=AuditLog(tmp_path / "audit.csv"),
        rollback_dir=tmp_path / "rb",
        rate_per_second=10_000,
    )
    return result, report


class TestSilentDiscard:
    def test_accepted_but_unstored_writes_are_reported_as_failures(self, tmp_path: Path) -> None:
        catalog = SilentlyDiscardingCatalog(temp_catalog(product_count=40))
        result, report = run(catalog, tmp_path)

        assert result.product_count > 0
        # The adapter took every write without complaint …
        assert len(catalog.accepted) == result.product_count
        assert len(report.written) == result.product_count
        # … and not one of them can be confirmed.
        assert report.verified == []
        assert len(report.failed) == result.product_count
        assert not report.ok

    def test_the_failure_says_what_actually_went_wrong(self, tmp_path: Path) -> None:
        catalog = SilentlyDiscardingCatalog(temp_catalog(product_count=10))
        _, report = run(catalog, tmp_path)
        _, reason = report.failed[0]
        assert "stored content does not match" in reason


class TestPartialFailure:
    def test_one_unstored_product_does_not_taint_the_rest(self, tmp_path: Path) -> None:
        inner = temp_catalog(product_count=40)
        first = plan(inner, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        doomed = first.changes[0].sku

        catalog = HalfDiscardingCatalog(temp_catalog(product_count=40), doomed)
        result, report = run(catalog, tmp_path)

        assert doomed in [sku for sku, _ in report.failed]
        assert doomed not in report.verified
        assert len(report.verified) == result.product_count - 1
        assert len(report.written) == result.product_count

    def test_counts_are_reported_separately(self, tmp_path: Path) -> None:
        """requested / written / verified / failed are four different questions."""
        inner = temp_catalog(product_count=40)
        doomed = plan(inner, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE).changes[0].sku
        catalog = HalfDiscardingCatalog(temp_catalog(product_count=40), doomed)
        result, report = run(catalog, tmp_path)

        assert len(report.requested) == result.product_count
        assert len(report.written) == result.product_count
        assert len(report.verified) == result.product_count - 1
        assert len(report.failed) == 1


class TestRefusedWrite:
    def test_a_refusing_adapter_writes_nothing_and_fails_loudly(self, tmp_path: Path) -> None:
        catalog = RefusingCatalog(temp_catalog(product_count=20))
        result, report = run(catalog, tmp_path)
        assert report.written == []
        assert report.verified == []
        assert len(report.failed) == result.product_count
        assert "refused" in report.failed[0][1]


class TestHonestSuccess:
    def test_a_working_catalogue_verifies_everything(self, tmp_path: Path) -> None:
        catalog = temp_catalog(product_count=40)
        result, report = run(catalog, tmp_path)
        assert len(report.verified) == result.product_count
        assert report.failed == []
        assert report.ok
        assert report.replacements == result.total_replacements

    def test_updated_reports_only_verified_products(self, tmp_path: Path) -> None:
        """The legacy accessor must not resurrect the old, looser meaning."""
        inner = temp_catalog(product_count=40)
        doomed = plan(inner, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE).changes[0].sku
        catalog = HalfDiscardingCatalog(temp_catalog(product_count=40), doomed)
        _, report = run(catalog, tmp_path)
        assert report.updated == report.verified
        assert doomed not in report.updated
