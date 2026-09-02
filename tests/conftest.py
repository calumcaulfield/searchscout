from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# The tests directory is not a package, so `helpers` is imported as a
# plain module and needs this directory on the path.
sys.path.insert(0, str(Path(__file__).parent))

# Set before `searchscout.config` is imported anywhere: Settings is lru_cached,
# so the first read wins. A smaller catalogue keeps the web tests fast, and
# writing audit output under the repo would leave artefacts behind.
os.environ.setdefault("SCOUT_DEMO_PRODUCT_COUNT", "30")
_SCRATCH = Path(tempfile.gettempdir()) / "searchscout-tests"
os.environ.setdefault("SCOUT_AUDIT_PATH", str(_SCRATCH / "audit.csv"))
os.environ.setdefault("SCOUT_ROLLBACK_DIR", str(_SCRATCH / "rollback"))
# The demo catalogue is a file now, so tests must not share the one the web UI
# uses — a test run would otherwise rewrite the catalogue behind a live demo.
os.environ.setdefault("SCOUT_DEMO_DB_PATH", str(_SCRATCH / "catalog.db"))

import pytest  # noqa: E402 - must follow the env setup above

from searchscout.audit import AuditLog  # noqa: E402
from searchscout.catalog.demo import DemoCatalog  # noqa: E402


@pytest.fixture
def catalog(tmp_path: Path) -> DemoCatalog:
    """A throwaway catalogue file per test, so tests cannot see each other."""
    return DemoCatalog(tmp_path / "catalog.db", product_count=40)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.csv")


@pytest.fixture
def rollback_dir(tmp_path: Path) -> Path:
    return tmp_path / "rollback"
