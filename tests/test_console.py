"""The console: dashboard, filters, activity, rollback and demo reset."""

from __future__ import annotations

import re

import pytest

from searchscout.config import get_settings
from searchscout.health import normalised_text
from searchscout.web.app import create_app


@pytest.fixture
def settings(tmp_path):  # type: ignore[no-untyped-def]
    base = get_settings()
    return base.model_copy(
        update={
            "demo_db_path": tmp_path / "catalog.db",
            "audit_path": tmp_path / "audit.csv",
            "rollback_dir": tmp_path / "rollback",
            "demo_product_count": 80,
        }
    )


@pytest.fixture
def app(settings):  # type: ignore[no-untyped-def]
    application = create_app(settings)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):  # type: ignore[no-untyped-def]
    with app.test_client() as test_client:
        yield test_client


def skus(response) -> list[str]:  # type: ignore[no-untyped-def]
    return re.findall(r"/product/(DEMO-\d+)", response.data.decode())


def apply_change(client, term: str, replacement: str) -> str:  # type: ignore[no-untyped-def]
    body = client.post(
        "/plan", data={"term": term, "replacement": replacement, "mode": "case-insensitive"}
    ).data.decode()
    plan_id = re.search(r"Plan ([a-f0-9]+)", body).group(1)
    client.post(f"/apply/{plan_id}")
    return plan_id


class TestDashboard:
    def test_it_reports_the_catalogue_size(self, client) -> None:
        body = client.get("/").data.decode()
        assert "Catalogue operations" in body
        assert "80" in body

    def test_it_lists_health_issues(self, client) -> None:
        body = client.get("/").data.decode()
        assert "Catalogue health" in body
        assert "Missing description" in body


class TestStockViews:
    def test_out_of_stock_view_shows_only_zero_quantity(self, client, settings) -> None:
        from searchscout.catalog.demo import DemoCatalog

        catalog = DemoCatalog(settings.demo_db_path, product_count=80)
        listed = set(skus(client.get("/search?stock=out_of_stock")))
        assert listed
        for sku in listed:
            assert catalog.get_product(sku).stock_quantity == 0

    def test_low_stock_view_respects_the_threshold(self, client, settings) -> None:
        from searchscout.catalog.demo import DemoCatalog

        catalog = DemoCatalog(settings.demo_db_path, product_count=80)
        listed = set(skus(client.get("/search?stock=low_stock")))
        assert listed
        for sku in listed:
            quantity = catalog.get_product(sku).stock_quantity
            assert quantity is not None
            assert 0 < quantity <= settings.low_stock_threshold


class TestContentViews:
    def test_missing_description_view_shows_only_empty_products(self, client, settings) -> None:
        from searchscout.catalog.demo import DemoCatalog

        catalog = DemoCatalog(settings.demo_db_path, product_count=80)
        listed = set(skus(client.get("/search?issue=missing_description")))
        assert listed
        for sku in listed:
            assert not normalised_text(catalog.get_product(sku).contents)


class TestProductDetail:
    def test_it_shows_a_product(self, client) -> None:
        sku = skus(client.get("/search"))[0]
        body = client.get(f"/product/{sku}").data.decode()
        assert sku in body
        assert "Description" in body

    def test_an_unknown_sku_is_404(self, client) -> None:
        assert client.get("/product/DEMO-999999").status_code == 404


class TestActivityAndRollback:
    def test_an_apply_is_recorded(self, client) -> None:
        apply_change(client, "cotton", "zxq-activity-check")
        body = client.get("/activity").data.decode()
        assert "zxq-activity-check" in body
        assert "Bulk update" in body

    def test_rollback_restores_the_original_content(self, client, settings) -> None:
        from searchscout.catalog.demo import DemoCatalog

        catalog = DemoCatalog(settings.demo_db_path, product_count=80)
        token = "zxq-rollback-check"
        before = {p.sku: p.contents for p in catalog.iter_products()}

        plan_id = apply_change(client, "cotton", token)
        assert (
            int(
                re.search(
                    r"<h1>(\d+) products?", client.get(f"/search?term={token}").data.decode()
                ).group(1)
            )
            > 0
        )

        body = client.post(f"/rollback/{plan_id}").data.decode()
        assert "Rollback complete" in body

        # The token is gone and the original text is back, byte for byte.
        after = client.get(f"/search?term={token}").data.decode()
        assert re.search(r"<h1>(\d+) products?", after).group(1) == "0"
        reopened = DemoCatalog(settings.demo_db_path, product_count=80)
        for product in reopened.iter_products():
            assert product.contents == before[product.sku]

    def test_rollback_is_verified_like_a_forward_write(self, client) -> None:
        plan_id = apply_change(client, "linen", "zxq-rollback-verified")
        body = client.post(f"/rollback/{plan_id}").data.decode()
        assert "Verified" in body
        assert "Failed" in body

    def test_rollback_is_itself_recorded(self, client) -> None:
        plan_id = apply_change(client, "bamboo", "zxq-recorded")
        client.post(f"/rollback/{plan_id}")
        assert "Rollback" in client.get("/activity").data.decode()


class TestDemoReset:
    def test_reset_restores_the_fixture_and_clears_activity(self, client) -> None:
        token = "zxq-reset-check"
        apply_change(client, "cotton", token)
        assert token in client.get("/activity").data.decode()

        client.post("/reset-demo")

        after = client.get(f"/search?term={token}").data.decode()
        assert re.search(r"<h1>(\d+) products?", after).group(1) == "0"
        assert token not in client.get("/activity").data.decode()


class TestSelectiveApply:
    def test_only_selected_skus_are_written(self, client, settings) -> None:
        from searchscout.catalog.demo import DemoCatalog

        body = client.post(
            "/plan",
            data={"term": "cotton", "replacement": "zxq-selected", "mode": "case-insensitive"},
        ).data.decode()
        plan_id = re.search(r"Plan ([a-f0-9]+)", body).group(1)
        offered = re.findall(r'name="sku" value="(DEMO-\d+)"', body)
        assert len(offered) > 1

        chosen = offered[0]
        client.post(f"/apply/{plan_id}", data={"sku": chosen})

        catalog = DemoCatalog(settings.demo_db_path, product_count=80)
        assert "zxq-selected" in catalog.get_product(chosen).contents
        for sku in offered[1:]:
            assert "zxq-selected" not in catalog.get_product(sku).contents
