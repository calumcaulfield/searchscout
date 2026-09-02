"""Configuration.

The original hard-coded the client's store URL in a module constant and read the
API key from the environment beside it. The URL is the part that identified the
customer, so it is the part that must come from configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCOUT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    #: `demo` needs no credentials and is the default, so the tool runs from a
    #: clean checkout. `magento` requires base_url and api_token.
    adapter: str = "demo"

    magento_base_url: str = ""
    magento_api_token: str = ""
    #: Magento rejects very large pages; 100 is the documented maximum.
    magento_page_size: int = Field(default=100, ge=1, le=100)
    magento_timeout_seconds: float = 30.0

    #: Writes per second against the store.
    rate_per_second: float = 4.0
    #: Refuse to apply a plan that touches more products than this without an
    #: explicit override. A one-character search term matches almost everything.
    max_products_per_plan: int = 500

    audit_path: Path = Path("var/audit/content_updates.csv")
    rollback_dir: Path = Path("var/rollback")

    demo_product_count: int = 200
    #: Where the demo catalogue lives. It is a real file so that search and
    #: update reference the same state across HTTP requests.
    demo_db_path: Path = Path("data/searchscout-demo.db")

    #: Stock at or below this is "low". Configuration, not data — the same
    #: quantity is a crisis for one product line and normal for another.
    low_stock_threshold: int = 5
    #: Descriptions shorter than this many characters of text are flagged.
    short_description_chars: int = 120
    #: Minutes a person would spend editing one product by hand. Only used to
    #: label an explicitly estimated figure; never presented as measured.
    manual_minutes_per_product: float = 2.0

    def require_magento(self) -> None:
        missing = [
            name
            for name, value in (
                ("SCOUT_MAGENTO_BASE_URL", self.magento_base_url),
                ("SCOUT_MAGENTO_API_TOKEN", self.magento_api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "adapter='magento' needs " + " and ".join(missing) + ". "
                "Use SCOUT_ADAPTER=demo to run without a store."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
