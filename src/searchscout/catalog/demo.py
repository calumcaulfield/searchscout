"""A SQLite-backed demo catalogue.

Replaces an in-memory fixture that was rebuilt on every construction. Because
the web layer builds a catalogue per request, that meant every request saw a
freshly generated catalogue and writes were discarded with the object that
made them — the application reported a successful bulk update and the next
search found nothing. See docs/BUGFIX_PERSISTENCE.md.

SQLite is the right size for this. It needs no server, the file is the whole
state, it survives process restarts, and read and write unavoidably reference
the same store — which is the property that was missing.

The generated data is synthetic. Names, SKUs, categories, prices and stock
levels are drawn from a fixed seed and describe nothing real.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from searchscout.catalog.base import CatalogAdapter, Product, ProductNotFoundError

_MATERIALS = ["cotton", "linen", "bamboo", "recycled card", "kraft paper", "jute"]
_COLOURS = ["natural", "charcoal", "sage", "ivory", "slate", "terracotta"]
_ITEMS = ["storage basket", "planter", "desk tray", "laundry bin", "shelf box", "utility caddy"]
_SIZES = ["small", "medium", "large", "extra large"]
_CATEGORIES = ["Storage", "Garden", "Office", "Laundry", "Kitchen", "Gifting"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku            TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    contents       TEXT NOT NULL,
    price          REAL,
    stock_quantity INTEGER,
    category       TEXT,
    updated_at     TEXT
);
"""


#: Sentences that vary per product. Without this the descriptions collapse
#: onto a handful of material/colour combinations, and duplicate-content
#: detection flags almost the whole catalogue — technically correct and
#: operationally useless.
_USES = [
    "Sized for a hallway or a small utility room.",
    "Stacks flat when not in use.",
    "Reinforced base for heavier contents.",
    "Rope handles on both sides.",
    "Designed to sit under a standard shelf.",
    "Finished by hand, so no two are identical.",
    "Lined to protect delicate contents.",
    "Ventilated sides for airflow.",
]

_CARE = [
    "Wipe clean with a damp cloth",
    "Spot clean only",
    "Brush gently; do not soak",
    "Air dry away from direct sunlight",
]


def _description(material: str, colour: str, item: str, size: str, note: str, care: str) -> str:
    """Merchandiser-style HTML: tables, inline styles, entities, links.

    Deliberately awkward — content this shape is why a naive string
    replacement over raw markup corrupts a catalogue.
    """
    return (
        f'<div class="product-copy">'
        f"<p>Handwoven from {material} in a {colour} finish. "
        f"A {size} {item} for everyday use. {note} "
        f"Dispatched in 2&ndash;3 working days.</p>"
        f'<table class="spec" data-material="{material}">'
        f"<tr><th>Material</th><td>{material.title()}</td></tr>"
        f"<tr><th>Colour</th><td>{colour.title()}</td></tr>"
        f"<tr><th>Care</th><td>{care}</td></tr>"
        f"</table>"
        f'<p style="font-size:0.9em">See our '
        f'<a href="https://demo-store.example.com/care-guide">care guide</a> '
        f"for {material} products.</p>"
        f"</div>"
    )


def _short_description(material: str) -> str:
    """Too thin to be useful — the catalogue-health check should flag it."""
    return f"<p>{material.title()} item.</p>"


class DemoCatalog(CatalogAdapter):
    """Persistent synthetic catalogue. Implements the same protocol as Magento."""

    name = "demo"

    def __init__(
        self,
        db_path: Path | str,
        product_count: int = 200,
        seed: int = 20260829,
    ) -> None:
        self.db_path = Path(db_path)
        self.product_count = product_count
        self.seed = seed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        # Seeded only when empty, so a restart does not discard an operator's
        # work — which is the whole point of moving off the in-memory fixture.
        if self._count() == 0:
            self.seed_products()

    # ------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])

    @staticmethod
    def _to_product(row: sqlite3.Row) -> Product:
        updated = row["updated_at"]
        return Product(
            sku=row["sku"],
            name=row["name"],
            contents=row["contents"],
            price=row["price"],
            stock_quantity=row["stock_quantity"],
            category=row["category"],
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )

    # -------------------------------------------------------------- seeding

    def seed_products(self) -> int:
        """Generate the deterministic catalogue. Existing rows are replaced.

        The spread is deliberate: an operator opening the dashboard should
        immediately have something to act on, so a known slice of the
        catalogue is out of stock, low on stock, missing a description, too
        short, or duplicated.
        """
        rng = random.Random(self.seed)  # noqa: S311 - reproducible fixtures, not crypto
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = []

        # One description reused across several SKUs, so duplicate-content
        # detection has something real to find.
        duplicate_copy = _description(
            "jute",
            "natural",
            "storage basket",
            "medium",
            "Stacks flat when not in use.",
            "Spot clean only",
        )

        for index in range(self.product_count):
            material = rng.choice(_MATERIALS)
            colour = rng.choice(_COLOURS)
            item = rng.choice(_ITEMS)
            size = rng.choice(_SIZES)
            sku = f"DEMO-{1000 + index}"

            if index % 25 == 7:
                contents = ""  # missing description
            elif index % 25 == 11:
                contents = _short_description(material)  # too short
            elif index % 25 in (17, 18, 19):
                contents = duplicate_copy  # duplicated
            else:
                contents = _description(
                    material,
                    colour,
                    item,
                    size,
                    _USES[index % len(_USES)],
                    _CARE[index % len(_CARE)],
                )

            if index % 33 == 5:
                stock = 0  # out of stock
            elif index % 17 == 3:
                stock = rng.randint(1, 5)  # low stock
            else:
                stock = rng.randint(12, 240)

            rows.append(
                (
                    sku,
                    f"{colour.title()} {material.title()} {item.title()} ({size})",
                    contents,
                    round(rng.uniform(6.5, 89.0), 2),
                    stock,
                    rng.choice(_CATEGORIES),
                    now,
                )
            )

        with self._connect() as connection:
            connection.execute("DELETE FROM products")
            connection.executemany(
                "INSERT INTO products (sku, name, contents, price, stock_quantity,"
                " category, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def reset(self) -> int:
        """Return the demo catalogue to its known state. Demo adapter only."""
        return self.seed_products()

    # ------------------------------------------------------- CatalogAdapter

    def iter_products(self) -> list[Product]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM products ORDER BY sku").fetchall()
        return [self._to_product(row) for row in rows]

    def get_product(self, sku: str) -> Product:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise ProductNotFoundError(sku)
        return self._to_product(row)

    def update_contents(self, sku: str, contents: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE products SET contents = ?, updated_at = ? WHERE sku = ?",
                (contents, now, sku),
            )
            if cursor.rowcount == 0:
                raise ProductNotFoundError(sku)
