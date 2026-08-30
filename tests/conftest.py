from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Set before `searchscout.config` is imported anywhere: Settings is lru_cached,
# so the first read wins. A smaller catalogue keeps the web tests fast, and
# writing audit output under the repo would leave artefacts behind.
os.environ.setdefault("SCOUT_DEMO_PRODUCT_COUNT", "30")
_SCRATCH = Path(tempfile.gettempdir()) / "searchscout-tests"
os.environ.setdefault("SCOUT_AUDIT_PATH", str(_SCRATCH / "audit.csv"))
os.environ.setdefault("SCOUT_ROLLBACK_DIR", str(_SCRATCH / "rollback"))

import pytest  # noqa: E402 - must follow the env setup above

from searchscout.audit import AuditLog  # noqa: E402
from searchscout.catalog.demo import DemoCatalog  # noqa: E402


@pytest.fixture
def catalog() -> DemoCatalog:
    return DemoCatalog(product_count=40)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.csv")


@pytest.fixture
def rollback_dir(tmp_path: Path) -> Path:
    return tmp_path / "rollback"
