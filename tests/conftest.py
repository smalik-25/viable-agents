"""Shared fixtures.

Phase 0 needs none. Phase 1 adds the Postgres fixture (DATABASE_URL if set, else
a testcontainer, else skip with a stated reason) and the bus fixture. Async
fixtures must use @pytest_asyncio.fixture under asyncio_mode = "strict"; a plain
@pytest.fixture fails with a message that does not point at the decorator.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT
