"""Shared fixtures.

Database strategy, in priority order, so one ``uv run pytest`` works everywhere:
1. DATABASE_URL set     -> use it (CI service container, or local `docker compose up`).
2. Docker available     -> a throwaway postgres:16 via testcontainers.
3. Neither              -> skip every `db` test with a stated reason, never a hard fail.

Migrations run once per session through the real Alembic Python API, so
``alembic upgrade head`` from an empty database is itself under test. Each test
runs in a transaction that is rolled back, isolated with a SAVEPOINT so code under
test may call commit().
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parents[1]


def _docker_daemon_up() -> bool:
    """The `docker` binary can exist while the daemon is down; testcontainers then
    blocks. Check reachability quickly so `db` tests skip fast instead of hanging."""
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden files instead of asserting against them",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`live` tests are opt-in: `uv run pytest -m live`. CI never selects them."""
    if config.getoption("-m") != "live":
        skip_live = pytest.mark.skip(reason="live test; run explicitly with -m live")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _no_accidental_spend(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Pin a dummy key and disable tracing so a forgotten mock fails as an auth
    error instead of spending money or writing to a real Langfuse project."""
    if "live" in request.keywords:
        return
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "False")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated Postgres from whichever source is available; skips if none is."""
    url = os.environ.get("DATABASE_URL")
    if url:
        _upgrade_head(url)
        yield url
        return

    if not _docker_daemon_up():
        pytest.skip("no DATABASE_URL and no reachable Docker daemon; set one or start Docker")
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed; set DATABASE_URL instead")

    try:
        with PostgresContainer("postgres:16", driver="asyncpg") as postgres:
            url = postgres.get_connection_url()
            _upgrade_head(url)
            yield url
    except Exception as exc:
        pytest.skip(f"could not start postgres:16 container: {exc}")


def _upgrade_head(url: str) -> None:
    """Synchronous on purpose: the async env.py calls asyncio.run(), which raises
    if driven from inside a running loop."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[Any]:
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(database_url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: Any) -> AsyncIterator[Any]:
    """Per-test session on a SAVEPOINT inside an outer transaction that always rolls back."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    conn = await engine.connect()
    trans = await conn.begin()
    maker = async_sessionmaker(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with maker() as session:
        yield session
    await trans.rollback()
    await conn.close()
