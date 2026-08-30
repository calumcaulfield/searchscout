"""An in-memory catalogue, so the tool runs with no credentials.

The generated products are synthetic: the names, SKUs and copy are drawn from a
fixed seed and describe nothing real. This is what lets the test suite, the CLI
and the web UI all run against a realistic catalogue without touching a live
store — which the original could not do at all.
"""

from __future__ import annotations

import random

from searchscout.catalog.base import CatalogAdapter, Product, ProductNotFoundError

_MATERIALS = ["cotton", "linen", "bamboo", "recycled card", "kraft paper", "jute"]
_COLOURS = ["natural", "charcoal", "sage", "ivory", "slate", "terracotta"]
_ITEMS = ["storage basket", "planter", "desk tray", "laundry bin", "shelf box", "utility caddy"]
_SIZES = ["small", "medium", "large", "extra large"]


def _description(rng: random.Random, material: str, colour: str) -> str:
    """Merchandiser-style HTML: tables, inline styles, entities, links.

    Deliberately awkward. Content this shape is exactly why a naive string
    replacement over raw markup corrupts a catalogue.
    """
    return (
        f'<div class="product-copy">'
        f"<p>Handwoven from {material} in a {colour} finish. "
        f"Dispatched in 2&ndash;3 working days.</p>"
        f'<table class="spec" data-material="{material}">'
        f"<tr><th>Material</th><td>{material.title()}</td></tr>"
        f"<tr><th>Colour</th><td>{colour.title()}</td></tr>"
        f"<tr><th>Care</th><td>Wipe clean with a damp cloth</td></tr>"
        f"</table>"
        f'<p style="font-size:0.9em">See our '
        f'<a href="https://demo-store.example.com/care-guide">care guide</a> '
        f"for {material} products.</p>"
        f"</div>"
    )


class DemoCatalog(CatalogAdapter):
    name = "demo"

    def __init__(self, product_count: int = 200, seed: int = 20260829) -> None:
        rng = random.Random(seed)  # noqa: S311 - reproducible fixtures, not crypto
        self._products: dict[str, Product] = {}
        for index in range(product_count):
            material = rng.choice(_MATERIALS)
            colour = rng.choice(_COLOURS)
            item = rng.choice(_ITEMS)
            size = rng.choice(_SIZES)
            sku = f"DEMO-{1000 + index}"
            self._products[sku] = Product(
                sku=sku,
                name=f"{colour.title()} {material.title()} {item.title()} ({size})",
                contents=_description(rng, material, colour),
                price=round(rng.uniform(6.5, 89.0), 2),
            )
        #: Every write is recorded so tests can assert what a dry run did *not* do.
        self.writes: list[tuple[str, str]] = []

    def iter_products(self) -> list[Product]:
        return list(self._products.values())

    def get_product(self, sku: str) -> Product:
        try:
            return self._products[sku]
        except KeyError as exc:
            raise ProductNotFoundError(sku) from exc

    def update_contents(self, sku: str, contents: str) -> None:
        product = self.get_product(sku)
        self._products[sku] = Product(
            sku=product.sku, name=product.name, contents=contents, price=product.price
        )
        self.writes.append((sku, contents))
