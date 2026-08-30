"""Adapter selection in one place."""

from __future__ import annotations

from searchscout.catalog.base import CatalogAdapter
from searchscout.catalog.demo import DemoCatalog
from searchscout.config import Settings, get_settings


def build_catalog(settings: Settings | None = None) -> CatalogAdapter:
    settings = settings or get_settings()
    if settings.adapter == "demo":
        return DemoCatalog(product_count=settings.demo_product_count)
    if settings.adapter == "magento":
        settings.require_magento()
        # Imported lazily so a missing credential degrades this adapter alone —
        # the demo path must never be blocked by Magento configuration.
        from searchscout.catalog.magento import MagentoCatalog

        return MagentoCatalog(
            settings.magento_base_url,
            settings.magento_api_token,
            page_size=settings.magento_page_size,
            timeout=settings.magento_timeout_seconds,
        )
    raise ValueError(f"unknown adapter: {settings.adapter!r}")
