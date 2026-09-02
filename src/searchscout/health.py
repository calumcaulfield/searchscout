"""Catalogue health checks.

Deterministic rules, not a model. An operator needs to know *which* products
need attention and *why*, and a rule that can be stated in a sentence is both
explainable and testable — which a similarity score would not be.

Every check reads the generic `Product`, so none of this knows what backend
the catalogue came from.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from searchscout.catalog.base import Product, StockStatus
from searchscout.html_edit import extract_text


class Issue(StrEnum):
    MISSING_DESCRIPTION = "missing_description"
    SHORT_DESCRIPTION = "short_description"
    DUPLICATE_CONTENT = "duplicate_content"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"


ISSUE_LABELS: dict[Issue, str] = {
    Issue.MISSING_DESCRIPTION: "Missing description",
    Issue.SHORT_DESCRIPTION: "Short description",
    Issue.DUPLICATE_CONTENT: "Duplicate content",
    Issue.LOW_STOCK: "Low stock",
    Issue.OUT_OF_STOCK: "Out of stock",
}

#: Content issues are the ones a bulk edit can fix; stock issues need a buyer.
CONTENT_ISSUES = frozenset(
    {Issue.MISSING_DESCRIPTION, Issue.SHORT_DESCRIPTION, Issue.DUPLICATE_CONTENT}
)


def normalised_text(html: str) -> str:
    """Text with markup and whitespace removed, for comparing two descriptions.

    Two products whose copy differs only in spacing or casing are duplicated
    for an operator's purposes even though the HTML differs.
    """
    return re.sub(r"\s+", " ", extract_text(html)).strip().lower()


def content_fingerprint(html: str) -> str:
    return hashlib.sha256(normalised_text(html).encode()).hexdigest()


@dataclass
class ProductHealth:
    product: Product
    issues: list[Issue] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.issues


@dataclass
class CatalogHealth:
    total: int = 0
    healthy: int = 0
    by_issue: dict[Issue, list[str]] = field(default_factory=dict)
    products: list[ProductHealth] = field(default_factory=list)

    def skus_with(self, issue: Issue) -> list[str]:
        return self.by_issue.get(issue, [])

    def count(self, issue: Issue) -> int:
        return len(self.by_issue.get(issue, []))


def assess(
    products: list[Product],
    *,
    low_stock_threshold: int = 5,
    short_description_chars: int = 120,
) -> CatalogHealth:
    """Score every product against every rule.

    Duplicate detection needs the whole catalogue, so this takes the full list
    rather than working product by product.
    """
    fingerprints: dict[str, list[str]] = defaultdict(list)
    for product in products:
        text = normalised_text(product.contents)
        if text:
            fingerprints[content_fingerprint(product.contents)].append(product.sku)
    duplicated = {sku for skus in fingerprints.values() if len(skus) > 1 for sku in skus}

    health = CatalogHealth(total=len(products))
    by_issue: dict[Issue, list[str]] = defaultdict(list)

    for product in products:
        issues: list[Issue] = []
        text = normalised_text(product.contents)

        if not text:
            issues.append(Issue.MISSING_DESCRIPTION)
        elif len(text) < short_description_chars:
            # Only when something is present: an empty description is already
            # reported, and reporting it twice would double-count the product.
            issues.append(Issue.SHORT_DESCRIPTION)

        if product.sku in duplicated:
            issues.append(Issue.DUPLICATE_CONTENT)

        status = product.stock_status(low_stock_threshold)
        if status is StockStatus.OUT_OF_STOCK:
            issues.append(Issue.OUT_OF_STOCK)
        elif status is StockStatus.LOW_STOCK:
            issues.append(Issue.LOW_STOCK)

        for issue in issues:
            by_issue[issue].append(product.sku)
        health.products.append(ProductHealth(product=product, issues=issues))
        if not issues:
            health.healthy += 1

    health.by_issue = dict(by_issue)
    return health


def filter_by_stock(
    products: list[Product], status: StockStatus | None, low_stock_threshold: int
) -> list[Product]:
    if status is None:
        return products
    return [p for p in products if p.stock_status(low_stock_threshold) is status]


def filter_by_issue(
    products: list[Product],
    issue: Issue | None,
    *,
    low_stock_threshold: int = 5,
    short_description_chars: int = 120,
) -> list[Product]:
    if issue is None:
        return products
    health = assess(
        products,
        low_stock_threshold=low_stock_threshold,
        short_description_chars=short_description_chars,
    )
    wanted = set(health.skus_with(issue))
    return [p for p in products if p.sku in wanted]
