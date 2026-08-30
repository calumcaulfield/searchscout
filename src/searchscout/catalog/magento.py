"""Magento 2 REST adapter.

Reconstructed from the original integration. The endpoints, the pagination
parameter names and the `custom_attributes` shape are Magento's public REST
contract, not anything proprietary; the store URL and token are configuration.

Two behaviours differ from the original deliberately:

* it raises `CatalogError` on a failed write instead of printing and continuing,
  because "the update failed" and "the update succeeded" produced identical
  output before;
* it pages with an explicit termination condition rather than looping until an
  empty page, so a malformed response cannot spin forever.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from searchscout.catalog.base import CatalogAdapter, CatalogError, Product, ProductNotFoundError

log = logging.getLogger(__name__)

#: The custom attribute holding the editable description.
CONTENT_ATTRIBUTE = "contents"


class MagentoCatalog(CatalogAdapter):
    name = "magento"

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        page_size: int = 100,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not api_token:
            raise ValueError("base_url and api_token are required")
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self._client = client or httpx.Client(timeout=timeout)
        # Applied to the client whether it was injected or built here. Setting
        # them only on the client this constructor creates means an injected
        # one — which is how the tests exercise this class — silently sends
        # unauthenticated requests, and the tests would pass anyway.
        self._client.headers.update(
            {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        )

    @staticmethod
    def _contents(payload: dict[str, Any]) -> str:
        for attribute in payload.get("custom_attributes", []):
            if attribute.get("attribute_code") == CONTENT_ATTRIBUTE:
                return str(attribute.get("value") or "")
        return ""

    def _to_product(self, payload: dict[str, Any]) -> Product:
        price = payload.get("price")
        return Product(
            sku=str(payload["sku"]),
            name=str(payload.get("name", "")),
            contents=self._contents(payload),
            price=float(price) if isinstance(price, int | float) else None,
        )

    def iter_products(self) -> list[Product]:
        products: list[Product] = []
        page = 1
        while True:
            response = self._client.get(
                f"{self.base_url}/products",
                params={
                    "searchCriteria[pageSize]": self.page_size,
                    "searchCriteria[currentPage]": page,
                },
            )
            if response.status_code != 200:
                raise CatalogError(f"listing products failed: {response.status_code}")
            body = response.json()
            items = body.get("items", [])
            products.extend(self._to_product(item) for item in items)

            # Terminate on the reported total rather than on an empty page: an
            # unexpected response shape should stop the loop, not extend it.
            total = body.get("total_count")
            if not items or (isinstance(total, int) and len(products) >= total):
                break
            page += 1
        log.info("fetched %d products in %d pages", len(products), page)
        return products

    def get_product(self, sku: str) -> Product:
        response = self._client.get(f"{self.base_url}/products/{sku}")
        if response.status_code == 404:
            raise ProductNotFoundError(sku)
        if response.status_code != 200:
            raise CatalogError(f"fetching {sku} failed: {response.status_code}")
        return self._to_product(response.json())

    def update_contents(self, sku: str, contents: str) -> None:
        response = self._client.put(
            f"{self.base_url}/products/{sku}",
            json={
                "product": {
                    "custom_attributes": [{"attribute_code": CONTENT_ATTRIBUTE, "value": contents}]
                }
            },
        )
        if response.status_code == 404:
            raise ProductNotFoundError(sku)
        if response.status_code != 200:
            raise CatalogError(
                f"updating {sku} failed: {response.status_code} {response.text[:200]}"
            )

    def close(self) -> None:
        self._client.close()
