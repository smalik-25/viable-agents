"""Postgres-backed: the GitHub adapter's cache actually round-trips through rows.

Not run-scoped (see ``GitHubCacheRow`` docstring), so this test does not use the
``db_session`` SAVEPOINT fixture -- a cache entry is meant to outlive one
transaction -- and cleans up its own row instead.
"""

from __future__ import annotations

import pytest

from viable_agents.persistence import make_session_factory
from viable_agents.persistence.models import GitHubCacheRow
from viable_agents.sources import PostgresGitHubCache

pytestmark = pytest.mark.db

_URL = "https://api.github.com/test/github_cache_roundtrip"


@pytest.mark.asyncio
async def test_postgres_cache_inserts_then_updates_by_url(engine) -> None:
    factory = make_session_factory(engine)
    cache = PostgresGitHubCache(session_factory=factory)
    try:
        assert await cache.get(_URL) is None

        await cache.put(_URL, etag='"v1"', status_code=200, body={"hello": "world"})
        cached = await cache.get(_URL)
        assert cached is not None
        assert cached.etag == '"v1"'
        assert cached.status_code == 200
        assert cached.body == {"hello": "world"}

        await cache.put(_URL, etag='"v2"', status_code=200, body={"hello": "there"})
        updated = await cache.get(_URL)
        assert updated is not None
        assert updated.etag == '"v2"', "a second put for the same URL must update, not duplicate"
        assert updated.body == {"hello": "there"}
    finally:
        async with factory() as session:
            row = await session.get(GitHubCacheRow, _URL)
            if row is not None:
                await session.delete(row)
                await session.commit()
