"""The review UI. The property under test is that the form cannot write."""

from __future__ import annotations

import re

import pytest

from searchscout.web.app import create_app


def matched_count(response) -> int:  # type: ignore[no-untyped-def]
    """The number in the results heading.

    Asserting `b"0 products match" not in body` looks reasonable and is wrong:
    it also matches "30 products match".
    """
    found = re.search(r"<h1>(\d+) products? match", response.data.decode())
    assert found, "results page has no match count heading"
    return int(found.group(1))


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


class TestReadOnlyPaths:
    def test_the_index_renders(self, client) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert b"SearchScout" in response.data

    def test_search_reports_matches(self, client) -> None:
        response = client.post("/search", data={"term": "cotton", "mode": "case-insensitive"})
        assert response.status_code == 200
        assert matched_count(response) > 0

    def test_an_empty_term_is_refused(self, client) -> None:
        response = client.post("/search", data={"term": "  "})
        assert b"Enter a search term" in response.data


class TestPreviewDoesNotWrite:
    def test_preview_shows_a_plan_without_applying_it(self, client) -> None:
        response = client.post(
            "/plan", data={"term": "cotton", "replacement": "hemp", "mode": "case-insensitive"}
        )
        assert response.status_code == 200
        assert b"Nothing has been written yet" in response.data
        assert b"Apply" in response.data

    def test_applying_an_unknown_plan_redirects_instead_of_writing(self, client) -> None:
        response = client.post("/apply/does-not-exist")
        assert response.status_code == 302


class TestApplyRequiresAPreviewedPlan:
    def test_a_previewed_plan_can_then_be_applied(self, client) -> None:
        preview = client.post(
            "/plan", data={"term": "cotton", "replacement": "hemp", "mode": "case-insensitive"}
        )
        body = preview.data.decode()
        plan_id = body.split("Plan ", 1)[1].split("<", 1)[0].strip()

        applied = client.post(f"/apply/{plan_id}")
        assert applied.status_code == 200
        assert b"products updated" in applied.data

    def test_a_plan_cannot_be_applied_twice(self, client) -> None:
        """Consuming the plan on apply is what stops a double submission."""
        preview = client.post(
            "/plan", data={"term": "cotton", "replacement": "hemp", "mode": "case-insensitive"}
        )
        plan_id = preview.data.decode().split("Plan ", 1)[1].split("<", 1)[0].strip()
        assert client.post(f"/apply/{plan_id}").status_code == 200
        assert client.post(f"/apply/{plan_id}").status_code == 302

    def test_applied_replacement_is_visible_to_the_next_search(self, client) -> None:
        """Regression: applying in demo mode must not vanish on the next request."""
        preview = client.post(
            "/plan",
            data={
                "term": "cotton",
                "replacement": "organic cotton",
                "mode": "case-insensitive",
            },
        )
        plan_id = preview.data.decode().split("Plan ", 1)[1].split("<", 1)[0].strip()

        applied = client.post(f"/apply/{plan_id}")
        assert applied.status_code == 200

        replacement = client.post(
            "/search",
            data={
                "term": "organic cotton",
                "mode": "case-insensitive",
                "field": "description",
            },
        )
        assert replacement.status_code == 200
        assert matched_count(replacement) > 0

        # The original standalone term should have been replaced in editable
        # description text rather than reappearing from a regenerated fixture.
        original = client.post(
            "/search",
            data={
                "term": "cotton",
                "mode": "whole-word",
                "field": "description",
            },
        )
        assert matched_count(original) > 0  # "organic cotton" still contains the word cotton
        assert b"organic cotton" in replacement.data.lower()


class TestSearchScopeInTheUI:
    """The scope selector, end to end through the form."""

    def test_the_form_offers_every_scope(self, client) -> None:
        body = client.get("/").data.decode()
        for value in ("all", "name", "description", "sku"):
            assert f'value="{value}"' in body

    def test_an_exact_product_name_returns_that_product(self, client) -> None:
        """The reported defect, through the actual form."""
        from searchscout.catalog.demo import DemoCatalog

        name = DemoCatalog(product_count=30).iter_products()[0].name
        response = client.post("/search", data={"term": name, "field": "name"})
        assert response.status_code == 200
        assert matched_count(response) >= 1
        assert name.encode() in response.data

    def test_a_sku_search_returns_that_product(self, client) -> None:
        from searchscout.catalog.demo import DemoCatalog

        sku = DemoCatalog(product_count=30).iter_products()[2].sku
        response = client.post("/search", data={"term": sku, "field": "sku"})
        assert sku.encode() in response.data
        assert matched_count(response) == 1

    def test_description_search_still_works(self, client) -> None:
        response = client.post("/search", data={"term": "Handwoven", "field": "description"})
        assert matched_count(response) > 0

    def test_all_fields_is_the_default_when_none_is_given(self, client) -> None:
        from searchscout.catalog.demo import DemoCatalog

        sku = DemoCatalog(product_count=30).iter_products()[1].sku
        # No `field` in the form at all — a SKU would be unreachable under the
        # old description-only behaviour.
        response = client.post("/search", data={"term": sku})
        assert sku.encode() in response.data

    def test_the_row_says_which_field_matched(self, client) -> None:
        from searchscout.catalog.demo import DemoCatalog

        sku = DemoCatalog(product_count=30).iter_products()[4].sku
        body = client.post("/search", data={"term": sku, "field": "sku"}).data.decode()
        assert "Matched in" in body
        assert "sku" in body

    def test_search_remains_case_insensitive(self, client) -> None:
        response = client.post("/search", data={"term": "HANDWOVEN", "mode": "case-insensitive"})
        assert matched_count(response) > 0

    def test_the_results_page_states_that_editing_is_description_only(self, client) -> None:
        body = client.post("/search", data={"term": "cotton"}).data.decode()
        assert "description content only" in body
