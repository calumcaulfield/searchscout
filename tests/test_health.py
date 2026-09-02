"""Catalogue health and stock rules.

Every rule is deterministic and stated in one sentence, so a test can assert
the rule rather than a threshold that happens to hold for today's fixture.
"""

from __future__ import annotations

from helpers import temp_catalog
from searchscout.catalog.base import Product, StockStatus
from searchscout.health import (
    Issue,
    assess,
    filter_by_issue,
    filter_by_stock,
    normalised_text,
)


def product(sku: str, contents: str = "<p>x</p>", stock: int | None = 50) -> Product:
    return Product(sku=sku, name=f"Product {sku}", contents=contents, stock_quantity=stock)


class TestStockStatus:
    def test_zero_is_out_of_stock(self) -> None:
        assert product("A", stock=0).stock_status(5) is StockStatus.OUT_OF_STOCK

    def test_at_the_threshold_is_low(self) -> None:
        assert product("A", stock=5).stock_status(5) is StockStatus.LOW_STOCK

    def test_above_the_threshold_is_in_stock(self) -> None:
        assert product("A", stock=6).stock_status(5) is StockStatus.IN_STOCK

    def test_unknown_is_not_out_of_stock(self) -> None:
        """No stock figure is different from a stock figure of zero."""
        assert product("A", stock=None).stock_status(5) is StockStatus.UNKNOWN

    def test_the_threshold_is_configuration(self) -> None:
        item = product("A", stock=10)
        assert item.stock_status(5) is StockStatus.IN_STOCK
        assert item.stock_status(20) is StockStatus.LOW_STOCK


class TestContentRules:
    def test_empty_content_is_missing(self) -> None:
        health = assess([product("A", contents="")])
        assert health.skus_with(Issue.MISSING_DESCRIPTION) == ["A"]

    def test_markup_with_no_text_is_also_missing(self) -> None:
        health = assess([product("A", contents="<div><p>  </p></div>")])
        assert health.skus_with(Issue.MISSING_DESCRIPTION) == ["A"]

    def test_a_missing_description_is_not_also_reported_as_short(self) -> None:
        """Otherwise one product would be counted twice on the dashboard."""
        health = assess([product("A", contents="")])
        assert health.skus_with(Issue.SHORT_DESCRIPTION) == []

    def test_short_content_is_flagged(self) -> None:
        health = assess(
            [product("A", contents="<p>Small basket.</p>")], short_description_chars=120
        )
        assert health.skus_with(Issue.SHORT_DESCRIPTION) == ["A"]

    def test_long_content_is_not_flagged(self) -> None:
        health = assess(
            [product("A", contents="<p>" + "word " * 60 + "</p>")], short_description_chars=120
        )
        assert health.skus_with(Issue.SHORT_DESCRIPTION) == []

    def test_identical_text_is_duplicated(self) -> None:
        copy = "<p>" + "identical wording here " * 8 + "</p>"
        health = assess([product("A", contents=copy), product("B", contents=copy)])
        assert set(health.skus_with(Issue.DUPLICATE_CONTENT)) == {"A", "B"}

    def test_duplication_ignores_markup_and_spacing(self) -> None:
        """Two products whose copy differs only in tags are duplicated to a reader."""
        text = "identical wording here " * 8
        health = assess(
            [
                product("A", contents=f"<p>{text}</p>"),
                product("B", contents=f"<div>  {text.upper()}  </div>"),
            ]
        )
        assert set(health.skus_with(Issue.DUPLICATE_CONTENT)) == {"A", "B"}

    def test_unique_content_is_not_duplicated(self) -> None:
        health = assess(
            [
                product("A", contents="<p>" + "first wording " * 10 + "</p>"),
                product("B", contents="<p>" + "second wording " * 10 + "</p>"),
            ]
        )
        assert health.skus_with(Issue.DUPLICATE_CONTENT) == []

    def test_empty_products_are_not_duplicates_of_each_other(self) -> None:
        """They are already reported as missing; duplicating that helps nobody."""
        health = assess([product("A", contents=""), product("B", contents="")])
        assert health.skus_with(Issue.DUPLICATE_CONTENT) == []

    def test_normalisation_strips_markup_and_case(self) -> None:
        assert normalised_text("<p>  Hello   World </p>") == "hello world"


class TestAssessment:
    def test_healthy_counts_only_products_with_no_issues(self) -> None:
        health = assess(
            [
                product("A", contents="<p>" + "good copy " * 30 + "</p>", stock=100),
                product("B", contents="", stock=0),
            ]
        )
        assert health.total == 2
        assert health.healthy == 1

    def test_the_demo_catalogue_has_something_to_act_on(self) -> None:
        """The dashboard is useless if the fixture is uniformly healthy."""
        catalog = temp_catalog(product_count=200)
        health = assess(catalog.iter_products(), low_stock_threshold=5)
        for issue in (Issue.MISSING_DESCRIPTION, Issue.LOW_STOCK, Issue.OUT_OF_STOCK):
            assert health.count(issue) > 0, f"fixture should contain {issue}"
        assert health.healthy > health.total // 2, "most products should be fine"


class TestFilters:
    def test_stock_filter_returns_only_that_status(self) -> None:
        products = [product("A", stock=0), product("B", stock=3), product("C", stock=90)]
        assert [p.sku for p in filter_by_stock(products, StockStatus.OUT_OF_STOCK, 5)] == ["A"]
        assert [p.sku for p in filter_by_stock(products, StockStatus.LOW_STOCK, 5)] == ["B"]
        assert [p.sku for p in filter_by_stock(products, StockStatus.IN_STOCK, 5)] == ["C"]

    def test_no_stock_filter_returns_everything(self) -> None:
        products = [product("A"), product("B")]
        assert filter_by_stock(products, None, 5) == products

    def test_issue_filter_returns_only_affected_products(self) -> None:
        products = [
            product("A", contents=""),
            product("B", contents="<p>" + "plenty of copy " * 20 + "</p>"),
        ]
        assert [p.sku for p in filter_by_issue(products, Issue.MISSING_DESCRIPTION)] == ["A"]

    def test_every_filtered_product_really_satisfies_the_rule(self) -> None:
        catalog = temp_catalog(product_count=200)
        products = catalog.iter_products()
        for item in filter_by_stock(products, StockStatus.OUT_OF_STOCK, 5):
            assert item.stock_quantity == 0
        for item in filter_by_stock(products, StockStatus.LOW_STOCK, 5):
            assert item.stock_quantity is not None and 0 < item.stock_quantity <= 5
        for item in filter_by_issue(products, Issue.MISSING_DESCRIPTION):
            assert not normalised_text(item.contents)
