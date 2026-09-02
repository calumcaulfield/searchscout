"""Plan, apply and roll back.

The original searched and wrote in one pass, so an operator's first sight of the
change was after it had happened. These tests pin the separation: a plan must
write nothing, and applying it must produce exactly what the plan showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import assert_unchanged, snapshot, temp_catalog
from searchscout.audit import AuditLog
from searchscout.catalog.base import CatalogError
from searchscout.catalog.demo import DemoCatalog
from searchscout.html_edit import tag_signature
from searchscout.matching import MatchMode
from searchscout.planner import apply, plan, rollback


class TestPlanningIsReadOnly:
    def test_planning_writes_nothing(self, catalog: DemoCatalog) -> None:
        before = snapshot(catalog)
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        assert result.product_count > 0, "fixture should contain matching products"
        assert_unchanged(catalog, before)

    def test_an_empty_search_term_is_refused(self, catalog: DemoCatalog) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            plan(catalog, "", "hemp")

    def test_a_term_that_matches_nothing_produces_an_empty_plan(self, catalog: DemoCatalog) -> None:
        result = plan(catalog, "unobtainium", "hemp")
        assert result.product_count == 0
        assert result.total_replacements == 0


class TestApplyMatchesThePlan:
    def test_what_is_written_is_exactly_what_was_previewed(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        expected = {change.sku: change.after for change in result.changes}

        report = apply(
            catalog, result, audit=audit, rollback_dir=rollback_dir, rate_per_second=1000
        )

        assert report.ok
        assert set(report.verified) == set(expected)
        # Read back from the catalogue, not from a write log: the point is that
        # what is *stored* equals what the preview showed.
        for sku, planned in expected.items():
            assert catalog.get_product(sku).contents == planned

    def test_markup_survives_a_real_apply(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        before = {p.sku: tag_signature(p.contents) for p in catalog.iter_products()}
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        apply(catalog, result, audit=audit, rollback_dir=rollback_dir, rate_per_second=1000)

        for product in catalog.iter_products():
            assert tag_signature(product.contents) == before[product.sku]

    def test_a_failing_write_is_reported_not_swallowed(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        """The original printed the failure and carried on as though it had worked."""
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        doomed = result.changes[0].sku

        original_update = catalog.update_contents

        def failing(sku: str, contents: str) -> None:
            if sku == doomed:
                raise CatalogError("store rejected the update")
            original_update(sku, contents)

        catalog.update_contents = failing  # type: ignore[method-assign]
        report = apply(
            catalog, result, audit=audit, rollback_dir=rollback_dir, rate_per_second=1000
        )

        assert not report.ok
        assert report.failed[0][0] == doomed
        assert doomed not in report.updated

    def test_the_product_cap_stops_an_over_broad_edit(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        """A one-character term matches most of a catalogue; something must stop it."""
        result = plan(catalog, "a", "@", match_mode=MatchMode.CASE_INSENSITIVE)
        assert result.product_count > 3
        before = snapshot(catalog)
        with pytest.raises(CatalogError, match="above the"):
            apply(catalog, result, audit=audit, rollback_dir=rollback_dir, max_products=3)
        assert_unchanged(catalog, before)


class TestRollback:
    def test_rollback_restores_every_product_byte_for_byte(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        before = {p.sku: p.contents for p in catalog.iter_products()}

        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        report = apply(
            catalog, result, audit=audit, rollback_dir=rollback_dir, rate_per_second=1000
        )
        assert report.rollback_path is not None
        assert any(p.contents != before[p.sku] for p in catalog.iter_products())

        rollback(catalog, audit, report.rollback_path)

        for product in catalog.iter_products():
            assert product.contents == before[product.sku]

    def test_no_rollback_file_when_nothing_was_written(
        self, catalog: DemoCatalog, audit: AuditLog, rollback_dir: Path
    ) -> None:
        result = plan(catalog, "unobtainium", "hemp")
        report = apply(catalog, result, audit=audit, rollback_dir=rollback_dir)
        assert report.rollback_path is None


class TestSearchScopeDoesNotWidenEditScope:
    """Search can find a product by name or SKU. Editing still touches neither.

    This is the boundary that made widening search safe: `planner` operates on
    `Product.contents` and nothing else, and it does not import
    `searchscout.search`. These tests assert the property rather than trusting
    the separation to hold.
    """

    def test_a_term_in_every_field_still_only_edits_the_description(
        self, audit: AuditLog, rollback_dir: Path
    ) -> None:
        catalog = temp_catalog(product_count=60)
        # A real product whose name *and* description both mention the term —
        # the fixture generates these, so nothing has to be fabricated.
        target = next(
            p
            for p in catalog.iter_products()
            if "cotton" in p.name.lower() and "cotton" in p.contents.lower()
        )
        original_name = target.name

        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        apply(catalog, result, audit=audit, rollback_dir=rollback_dir, rate_per_second=1000)

        edited = catalog.get_product(target.sku)
        assert edited.name == original_name, "the product name must not be rewritten"
        assert edited.sku == target.sku, "the SKU must not be rewritten"
        assert "hemp" in edited.contents, "the description should have been edited"

    def test_planning_never_reports_a_name_only_match(self) -> None:
        """A product matching only by name produces no edit, because nothing is editable there."""
        catalog = temp_catalog(product_count=60)
        # Name mentions the term; description deliberately does not.
        target = next(p for p in catalog.iter_products() if "cotton" in p.name.lower())
        sku = target.sku
        catalog.update_contents(sku, "<p>Made from linen throughout.</p>")
        result = plan(catalog, "cotton", "hemp", match_mode=MatchMode.CASE_INSENSITIVE)
        assert sku not in {change.sku for change in result.changes}

    def test_the_planner_does_not_depend_on_the_search_module(self) -> None:
        """Enforced structurally: the edit path cannot inherit search's wider scope."""
        import searchscout.planner as planner_module

        source = Path(planner_module.__file__).read_text(encoding="utf-8")
        assert "searchscout.search" not in source
        assert "SearchField" not in source
