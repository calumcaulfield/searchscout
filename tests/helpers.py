"""Test helpers."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from searchscout.catalog.demo import DemoCatalog


def temp_catalog(product_count: int = 40, seed: int = 20260829) -> DemoCatalog:
    """A catalogue in its own throwaway database file.

    Each call gets a distinct file, so a test that writes cannot be seen by any
    other test — which matters far more now the catalogue is persistent.
    """
    path = Path(tempfile.gettempdir()) / f"searchscout-test-{uuid.uuid4().hex}.db"
    return DemoCatalog(path, product_count=product_count, seed=seed)


def snapshot(catalog: DemoCatalog) -> dict[str, str]:
    """Every product's stored description, for before/after comparison."""
    return {p.sku: p.contents for p in catalog.iter_products()}


def assert_unchanged(catalog: DemoCatalog, before: dict[str, str]) -> None:
    """Nothing in the catalogue was written.

    Replaces an in-memory `writes` list on the old fixture. Comparing stored
    content is stronger: it would still catch a write that bypassed whatever
    counter the adapter kept.
    """
    after = {p.sku: p.contents for p in catalog.iter_products()}
    assert after == before, "the catalogue was modified when it should not have been"
