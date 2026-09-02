"""Finding products.

Deliberately separate from `planner.py`, and the separation is the point.

Searching and editing are different operations with different scopes. A product
can be *found* by its name or its SKU, because that is how a merchandiser
refers to it. Neither is *editable* here: the bulk replacement in `planner.py`
operates on the description HTML and nothing else, and it does not import this
module. Widening what can be searched must not widen what can be written.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from searchscout.catalog.base import Product
from searchscout.html_edit import extract_text
from searchscout.matching import MatchMode, count_matches


class SearchField(StrEnum):
    ALL = "all"
    """Name, SKU and description."""

    NAME = "name"
    SKU = "sku"
    DESCRIPTION = "description"
    """The editable HTML field — the only one a bulk edit can touch."""


#: Which fields each scope reads. `ALL` is ordered so that a name match, which
#: is the most recognisable context to show a user, is preferred over a
#: description match when a term appears in both.
FIELDS_FOR: dict[SearchField, tuple[SearchField, ...]] = {
    SearchField.ALL: (SearchField.NAME, SearchField.SKU, SearchField.DESCRIPTION),
    SearchField.NAME: (SearchField.NAME,),
    SearchField.SKU: (SearchField.SKU,),
    SearchField.DESCRIPTION: (SearchField.DESCRIPTION,),
}


@dataclass(frozen=True)
class FieldHit:
    field: SearchField
    matches: int
    #: The text a reader should see to understand *why* this row matched: the
    #: name, the SKU, or the description line the term appears in.
    context: str


@dataclass(frozen=True)
class SearchHit:
    product: Product
    hits: tuple[FieldHit, ...]

    @property
    def matches(self) -> int:
        return sum(hit.matches for hit in self.hits)

    @property
    def primary(self) -> FieldHit | None:
        """The hit whose context best explains the match, if the term matched.

        None for a plain listing, where no term was searched and there is
        therefore nothing to explain.
        """
        return self.hits[0] if self.hits else None

    @property
    def fields(self) -> tuple[SearchField, ...]:
        return tuple(hit.field for hit in self.hits)


def _description_context(text: str, term: str, mode: MatchMode) -> str:
    """The first description line containing the term, using the same rule as the match.

    Falling back to a substring check would disagree with `MatchMode.WHOLE_WORD`
    and show a line that did not actually match.
    """
    for line in text.splitlines():
        if count_matches(line, term, mode):
            return line.strip()
    return ""


def search_field(
    product: Product, term: str, mode: MatchMode, field: SearchField
) -> FieldHit | None:
    if field is SearchField.NAME:
        matches = count_matches(product.name, term, mode)
        return FieldHit(field, matches, product.name) if matches else None

    if field is SearchField.SKU:
        matches = count_matches(product.sku, term, mode)
        return FieldHit(field, matches, product.sku) if matches else None

    if field is SearchField.DESCRIPTION:
        text = extract_text(product.contents)
        matches = count_matches(text, term, mode)
        if not matches:
            return None
        return FieldHit(field, matches, _description_context(text, term, mode))

    raise ValueError(f"cannot search field {field!r}")


def listing(products: list[Product]) -> list[SearchHit]:
    """Every product as a hit with no matched field.

    A filter with no search term is a legitimate question — "show me
    everything out of stock" — and answering it should not require inventing a
    term. Returning the same shape as a search keeps the results view single.
    """
    return [SearchHit(product=product, hits=()) for product in products]


def search_products(
    products: list[Product],
    term: str,
    *,
    mode: MatchMode = MatchMode.CASE_INSENSITIVE,
    field: SearchField = SearchField.ALL,
    limit: int | None = None,
) -> list[SearchHit]:
    """Products matching `term` in the requested scope. Read-only."""
    if not term:
        return []

    results: list[SearchHit] = []
    for product in products:
        hits = tuple(
            hit
            for hit in (
                search_field(product, term, mode, candidate) for candidate in FIELDS_FOR[field]
            )
            if hit is not None
        )
        if hits:
            results.append(SearchHit(product=product, hits=hits))
        if limit is not None and len(results) >= limit:
            break
    return results
