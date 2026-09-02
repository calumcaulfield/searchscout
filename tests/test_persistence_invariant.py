"""The invariant the application previously violated.

For any editable term A and any replacement B: after applying A → B, a
completely new search for B must find the products the apply verified.

The old demo adapter regenerated its fixture on construction, and the web layer
constructs a catalogue per request, so a bulk update wrote into an object that
was discarded when the request ended. The UI reported success and the next
search found nothing. Every test here crosses at least one request or instance
boundary, because that boundary is exactly what used to break.
"""

from __future__ import annotations

import re

import pytest

from helpers import temp_catalog
from searchscout.audit import AuditLog
from searchscout.catalog.demo import DemoCatalog
from searchscout.config import get_settings
from searchscout.matching import MatchMode
from searchscout.planner import apply, plan
from searchscout.search import SearchField, search_products
from searchscout.web.app import create_app

#: The last pair uses a value that cannot exist in the fixture. If a search for
#: it returns products, the result can only have come from newly stored data —
#: no amount of regenerated seed content could produce it.
PAIRS = [
    ("cotton", "organic cotton"),
    ("linen", "premium linen"),
    ("bamboo", "sustainable bamboo"),
    ("cotton", "zxq-searchscout-verification-123"),
    ("Handwoven", "Loom-finished"),
]


@pytest.fixture
def settings(tmp_path):  # type: ignore[no-untyped-def]
    base = get_settings()
    return base.model_copy(
        update={
            "demo_db_path": tmp_path / "catalog.db",
            "audit_path": tmp_path / "audit.csv",
            "rollback_dir": tmp_path / "rollback",
            "demo_product_count": 60,
        }
    )


def heading_count(response) -> int:  # type: ignore[no-untyped-def]
    found = re.search(r"<h1>(\d+) products?", response.data.decode())
    assert found, "results page has no count heading"
    return int(found.group(1))


class TestAdapterLevelInvariant:
    @pytest.mark.parametrize(("term", "replacement"), PAIRS)
    def test_apply_then_a_fresh_search_finds_the_products(
        self, term: str, replacement: str
    ) -> None:
        catalog = temp_catalog(product_count=60)
        audit = AuditLog(catalog.db_path.parent / "audit.csv")

        result = plan(catalog, term, replacement, match_mode=MatchMode.CASE_INSENSITIVE)
        assert result.product_count > 0, f"fixture should contain {term!r}"
        planned = {change.sku for change in result.changes}

        report = apply(
            catalog,
            result,
            audit=audit,
            rollback_dir=catalog.db_path.parent / "rb",
            rate_per_second=10_000,
        )
        assert set(report.verified) == planned
        assert report.failed == []

        # A brand new adapter instance over the same store: nothing in memory
        # from the write can help this search.
        reopened = DemoCatalog(catalog.db_path, product_count=60)
        hits = search_products(
            reopened.iter_products(),
            replacement,
            mode=MatchMode.CASE_INSENSITIVE,
            field=SearchField.DESCRIPTION,
        )
        assert {hit.product.sku for hit in hits} >= planned

    def test_the_impossible_value_proves_the_data_is_new(self) -> None:
        """A value no fixture could generate, so a hit can only be stored data."""
        token = "zxq-searchscout-verification-123"
        catalog = temp_catalog(product_count=60)
        audit = AuditLog(catalog.db_path.parent / "audit.csv")

        before = search_products(catalog.iter_products(), token)
        assert before == [], "the token must not exist before the update"

        result = plan(catalog, "cotton", token, match_mode=MatchMode.CASE_INSENSITIVE)
        apply(
            catalog,
            result,
            audit=audit,
            rollback_dir=catalog.db_path.parent / "rb",
            rate_per_second=10_000,
        )

        reopened = DemoCatalog(catalog.db_path, product_count=60)
        after = search_products(reopened.iter_products(), token)
        assert len(after) == result.product_count

    def test_each_stored_product_holds_exactly_the_planned_content(self) -> None:
        catalog = temp_catalog(product_count=40)
        audit = AuditLog(catalog.db_path.parent / "audit.csv")
        result = plan(catalog, "linen", "flax", match_mode=MatchMode.CASE_INSENSITIVE)
        expected = {c.sku: c.after for c in result.changes}

        apply(
            catalog,
            result,
            audit=audit,
            rollback_dir=catalog.db_path.parent / "rb",
            rate_per_second=10_000,
        )

        reopened = DemoCatalog(catalog.db_path, product_count=40)
        for sku, planned in expected.items():
            assert reopened.get_product(sku).contents == planned


class TestThroughIndependentHttpRequests:
    """The path the browser takes. Each step is its own request."""

    @pytest.mark.parametrize(("term", "replacement"), PAIRS)
    def test_search_after_apply_across_requests(
        self, settings, term: str, replacement: str
    ) -> None:
        app = create_app(settings)
        app.config.update(TESTING=True)

        with app.test_client() as client:
            body = client.post(
                "/plan",
                data={"term": term, "replacement": replacement, "mode": "case-insensitive"},
            ).data.decode()
        plan_id = re.search(r"Plan ([a-f0-9]+)", body).group(1)
        planned = int(re.search(r"<b>(\d+)</b> products", body).group(1))
        assert planned > 0

        # A separate client: a separate request, and previously a separate
        # catalogue.
        with app.test_client() as client:
            applied = client.post(f"/apply/{plan_id}").data.decode()
        assert "Update complete" in applied

        with app.test_client() as client:
            found = heading_count(client.get(f"/search?term={replacement}&field=description"))
        assert found >= planned

    def test_a_preview_writes_nothing(self, settings) -> None:
        app = create_app(settings)
        app.config.update(TESTING=True)
        token = "zxq-preview-must-not-write"

        with app.test_client() as client:
            client.post(
                "/plan",
                data={"term": "cotton", "replacement": token, "mode": "case-insensitive"},
            )
        with app.test_client() as client:
            assert heading_count(client.get(f"/search?term={token}&field=all")) == 0

    def test_a_change_survives_a_new_application_instance(self, settings) -> None:
        """Equivalent to restarting the server: the store is the only carrier."""
        token = "zxq-survives-restart"
        app = create_app(settings)
        app.config.update(TESTING=True)
        with app.test_client() as client:
            body = client.post(
                "/plan",
                data={"term": "bamboo", "replacement": token, "mode": "case-insensitive"},
            ).data.decode()
            plan_id = re.search(r"Plan ([a-f0-9]+)", body).group(1)
            client.post(f"/apply/{plan_id}")

        restarted = create_app(settings)
        restarted.config.update(TESTING=True)
        with restarted.test_client() as client:
            assert heading_count(client.get(f"/search?term={token}&field=all")) > 0
