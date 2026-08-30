"""The adapter seam, and the Magento client's error handling."""

from __future__ import annotations

import httpx
import pytest

from searchscout.catalog.base import CatalogAdapter, CatalogError, ProductNotFoundError
from searchscout.catalog.demo import DemoCatalog
from searchscout.catalog.magento import MagentoCatalog


class TestDemoCatalog:
    def test_satisfies_the_adapter_protocol(self) -> None:
        assert isinstance(DemoCatalog(product_count=3), CatalogAdapter)

    def test_is_deterministic(self) -> None:
        """Screenshots and tests both depend on the same seed producing the same data."""
        a = DemoCatalog(product_count=20)
        b = DemoCatalog(product_count=20)
        assert [p.contents for p in a.iter_products()] == [p.contents for p in b.iter_products()]

    def test_an_unknown_sku_raises(self) -> None:
        with pytest.raises(ProductNotFoundError):
            DemoCatalog(product_count=3).get_product("NOPE")

    def test_updates_are_visible_and_recorded(self) -> None:
        catalog = DemoCatalog(product_count=3)
        sku = catalog.iter_products()[0].sku
        catalog.update_contents(sku, "<p>new</p>")
        assert catalog.get_product(sku).contents == "<p>new</p>"
        assert catalog.writes == [(sku, "<p>new</p>")]


def magento(handler: object) -> MagentoCatalog:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return MagentoCatalog(
        "https://demo-store.example.com/rest/V1",
        "test-token",
        client=httpx.Client(transport=transport),
    )


class TestMagentoCatalog:
    def test_requires_credentials(self) -> None:
        with pytest.raises(ValueError, match="required"):
            MagentoCatalog("", "")

    def test_reads_the_contents_custom_attribute(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "sku": "A-1",
                            "name": "Basket",
                            "price": 12.5,
                            "custom_attributes": [
                                {"attribute_code": "meta_title", "value": "ignore me"},
                                {"attribute_code": "contents", "value": "<p>cotton</p>"},
                            ],
                        }
                    ],
                    "total_count": 1,
                },
            )

        products = magento(handler).iter_products()
        assert products[0].contents == "<p>cotton</p>"
        assert products[0].price == 12.5

    def test_pagination_terminates_on_total_count(self) -> None:
        """The original looped until an empty page; a bad response spun forever."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                200,
                json={
                    "items": [{"sku": f"A-{calls['n']}", "name": "x", "custom_attributes": []}],
                    "total_count": 3,
                },
            )

        assert len(magento(handler).iter_products()) == 3
        assert calls["n"] == 3

    def test_a_failed_write_raises_rather_than_printing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        with pytest.raises(CatalogError, match="updating A-1 failed"):
            magento(handler).update_contents("A-1", "<p>hemp</p>")

    def test_a_missing_product_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(ProductNotFoundError):
            magento(handler).get_product("NOPE")

    def test_the_token_is_sent_as_a_bearer_header(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json={"items": [], "total_count": 0})

        magento(handler).iter_products()
        assert seen["authorization"] == "Bearer test-token"
