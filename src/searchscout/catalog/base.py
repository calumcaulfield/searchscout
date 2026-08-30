"""The seam between this tool and whatever holds the catalogue.

The original called Magento's REST API directly from the module that did the
editing, so nothing could be tested and nothing could run without production
credentials. Everything downstream of this protocol now depends on the
protocol, not on Magento.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    #: The editable HTML field. Named after the custom attribute the original
    #: operated on.
    contents: str
    price: float | None = None


class CatalogError(Exception):
    """A catalogue operation failed in a way the caller should surface."""


class ProductNotFoundError(CatalogError):
    pass


@runtime_checkable
class CatalogAdapter(Protocol):
    """Read and write product content.

    Deliberately small. A larger surface would be harder to implement for a
    second backend, and this tool only ever needed these three operations.
    """

    name: str

    def iter_products(self) -> list[Product]:
        """Every product whose content is editable."""
        ...

    def get_product(self, sku: str) -> Product: ...

    def update_contents(self, sku: str, contents: str) -> None:
        """Write new content for one product. Raises `CatalogError` on failure."""
        ...
