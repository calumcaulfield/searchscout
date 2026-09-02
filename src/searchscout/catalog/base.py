"""The seam between this tool and whatever holds the catalogue.

The original called Magento's REST API directly from the module that did the
editing, so nothing could be tested and nothing could run without production
credentials. Everything downstream of this protocol now depends on the
protocol, not on Magento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class StockStatus(StrEnum):
    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Product:
    """A product, in terms every catalogue backend can express.

    Deliberately not Magento's shape. An adapter maps whatever its platform
    calls these things onto this model, so the search, planning, editing and
    audit code never learns a vendor's vocabulary.
    """

    sku: str
    name: str
    #: The editable HTML field. The only field bulk replacement may write.
    contents: str
    price: float | None = None
    #: None means the backend does not report stock, which is different from
    #: reporting zero. Only the second is an out-of-stock product.
    stock_quantity: int | None = None
    category: str | None = None
    updated_at: datetime | None = None

    def stock_status(self, low_stock_threshold: int) -> StockStatus:
        """Derived, never stored — the threshold is configuration, not data."""
        if self.stock_quantity is None:
            return StockStatus.UNKNOWN
        if self.stock_quantity <= 0:
            return StockStatus.OUT_OF_STOCK
        if self.stock_quantity <= low_stock_threshold:
            return StockStatus.LOW_STOCK
        return StockStatus.IN_STOCK


class CatalogError(Exception):
    """A catalogue operation failed in a way the caller should surface."""


class WriteVerificationError(CatalogError):
    """A write was accepted but reading the product back did not confirm it.

    The distinction matters: the backend did not refuse the write, so nothing
    raised at the time. Only a fresh read shows the stored value is not what
    was asked for, and that must never be reported as a success.
    """

    def __init__(self, sku: str, expected: str, actual: str) -> None:
        super().__init__(f"{sku}: stored content does not match what was written")
        self.sku = sku
        self.expected = expected
        self.actual = actual


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
