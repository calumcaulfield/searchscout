"""Search scope.

The tool could originally only find a product by words in its description, so
searching the exact name shown in the results table returned nothing. These
tests pin the wider scope — and, just as importantly, pin that widening what can
be *found* did not widen what can be *edited*.
"""

from __future__ import annotations

import pytest

from helpers import assert_unchanged, snapshot, temp_catalog
from searchscout.catalog.base import Product
from searchscout.matching import MatchMode
from searchscout.search import SearchField, search_products


@pytest.fixture
def products() -> list[Product]:
    return temp_catalog(product_count=40).iter_products()


def skus(hits) -> set[str]:  # type: ignore[no-untyped-def]
    return {hit.product.sku for hit in hits}


class TestProductNameSearch:
    def test_the_exact_visible_product_name_finds_that_product(
        self, products: list[Product]
    ) -> None:
        """The defect this fixes: an exact name from the results table found nothing."""
        target = products[0]
        hits = search_products(products, target.name, field=SearchField.NAME)
        assert target.sku in skus(hits)
        assert hits[0].primary.field is SearchField.NAME
        # A catalogue can hold two products with the same name — same item in
        # the same colour, different stock line — so every hit must carry that
        # name, but the count is not necessarily one.
        for hit in hits:
            assert hit.product.name == target.name

    def test_the_exact_name_also_works_in_the_default_all_scope(
        self, products: list[Product]
    ) -> None:
        target = products[0]
        hits = search_products(products, target.name)
        assert target.sku in skus(hits)

    def test_a_partial_name_matches(self, products: list[Product]) -> None:
        hits = search_products(products, "Utility Caddy", field=SearchField.NAME)
        assert hits
        for hit in hits:
            assert "utility caddy" in hit.product.name.lower()

    def test_name_scope_ignores_a_description_only_term(self, products: list[Product]) -> None:
        # "Handwoven" appears in every description and in no name.
        assert search_products(products, "Handwoven", field=SearchField.NAME) == []
        assert search_products(products, "Handwoven", field=SearchField.DESCRIPTION)

    def test_the_context_is_the_name_when_the_name_matched(self, products: list[Product]) -> None:
        target = products[0]
        hit = search_products(products, target.name, field=SearchField.NAME)[0]
        assert hit.primary.context == target.name


class TestSkuSearch:
    def test_an_exact_sku_finds_exactly_one_product(self, products: list[Product]) -> None:
        target = products[3]
        hits = search_products(products, target.sku, field=SearchField.SKU)
        assert skus(hits) == {target.sku}
        assert hits[0].primary.context == target.sku

    def test_a_sku_prefix_matches_many(self, products: list[Product]) -> None:
        hits = search_products(products, "DEMO-", field=SearchField.SKU)
        assert len(hits) == len(products)

    def test_sku_scope_ignores_names_and_descriptions(self, products: list[Product]) -> None:
        assert search_products(products, "cotton", field=SearchField.SKU) == []


class TestDescriptionSearch:
    def test_description_search_still_works(self, products: list[Product]) -> None:
        """The original behaviour, unchanged."""
        hits = search_products(products, "Handwoven", field=SearchField.DESCRIPTION)
        assert hits
        assert hits[0].primary.field is SearchField.DESCRIPTION

    def test_the_context_is_the_matching_description_line(self, products: list[Product]) -> None:
        hit = search_products(products, "Handwoven", field=SearchField.DESCRIPTION)[0]
        assert "handwoven" in hit.primary.context.lower()
        # A line of prose, not the whole document.
        assert "\n" not in hit.primary.context

    def test_context_respects_the_match_mode(self) -> None:
        """A substring fallback would show a line that did not actually match."""
        product = Product(
            sku="X-1",
            name="Example",
            contents="<p>cottonseed oil</p><p>pure cotton throughout</p>",
        )
        hit = search_products(
            [product], "cotton", mode=MatchMode.WHOLE_WORD, field=SearchField.DESCRIPTION
        )[0]
        assert "pure cotton throughout" in hit.primary.context


class TestAllFieldsScope:
    def test_it_finds_a_match_from_each_field(self, products: list[Product]) -> None:
        target = products[0]
        assert target.sku in skus(search_products(products, target.name))
        assert target.sku in skus(search_products(products, target.sku))
        assert skus(search_products(products, "Handwoven"))

    def test_a_term_in_several_fields_reports_all_of_them(self) -> None:
        product = Product(sku="COTTON-1", name="Cotton Basket", contents="<p>Made from cotton.</p>")
        hit = search_products([product], "cotton")[0]
        assert set(hit.fields) == {SearchField.NAME, SearchField.SKU, SearchField.DESCRIPTION}
        # Name first: it is the most recognisable context to show.
        assert hit.primary.field is SearchField.NAME
        assert hit.matches == 3

    def test_it_is_the_default_scope(self, products: list[Product]) -> None:
        target = products[0]
        assert search_products(products, target.sku) == search_products(
            products, target.sku, field=SearchField.ALL
        )


class TestMatchModes:
    def test_search_is_case_insensitive_by_default(self, products: list[Product]) -> None:
        target = products[0]
        assert skus(search_products(products, target.name.upper())) >= {target.sku}
        assert skus(search_products(products, target.name.lower())) >= {target.sku}

    def test_literal_mode_is_case_sensitive(self, products: list[Product]) -> None:
        assert search_products(products, "HANDWOVEN", mode=MatchMode.LITERAL) == []
        assert search_products(products, "Handwoven", mode=MatchMode.LITERAL)

    def test_whole_word_does_not_match_inside_a_longer_word(self) -> None:
        product = Product(sku="X-1", name="Cottonseed Tray", contents="<p>none</p>")
        assert search_products([product], "cotton", mode=MatchMode.WHOLE_WORD) == []
        assert search_products([product], "cottonseed", mode=MatchMode.WHOLE_WORD)

    def test_an_empty_term_matches_nothing(self, products: list[Product]) -> None:
        assert search_products(products, "") == []


class TestSearchIsReadOnly:
    def test_searching_writes_nothing(self, products: list[Product]) -> None:
        catalog = temp_catalog(product_count=10)
        before = snapshot(catalog)
        for field in SearchField:
            search_products(catalog.iter_products(), "cotton", field=field)
        assert_unchanged(catalog, before)
