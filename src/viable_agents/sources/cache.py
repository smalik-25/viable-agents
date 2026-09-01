"""The GitHub response cache seam.

Mirrors ``llm/recorder.py``'s ``CallRecorder`` split: an in-memory implementation
for tests and short-lived runs, a Postgres-backed one for real polling. Caching
is not run-scoped (see ``GitHubCacheRow``) because the point is to spend rate
limit once per URL, not once per run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from viable_agents.persistence.models import GitHubCacheRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class CachedResponse:
    etag: str | None
    status_code: int
    body: dict[str, Any]


@runtime_checkable
class GitHubCache(Protocol):
    async def get(self, url: str) -> CachedResponse | None: ...

    async def put(
        self, url: str, *, etag: str | None, status_code: int, body: dict[str, Any]
    ) -> None: ...


class InMemoryGitHubCache:
    def __init__(self) -> None:
        self._store: dict[str, CachedResponse] = {}

    async def get(self, url: str) -> CachedResponse | None:
        return self._store.get(url)

    async def put(
        self, url: str, *, etag: str | None, status_code: int, body: dict[str, Any]
    ) -> None:
        self._store[url] = CachedResponse(etag=etag, status_code=status_code, body=body)


@dataclass
class PostgresGitHubCache:
    session_factory: async_sessionmaker[AsyncSession]

    async def get(self, url: str) -> CachedResponse | None:
        async with self.session_factory() as session:
            row = await session.get(GitHubCacheRow, url)
            if row is None:
                return None
            return CachedResponse(etag=row.etag, status_code=row.status_code, body=row.body)

    async def put(
        self, url: str, *, etag: str | None, status_code: int, body: dict[str, Any]
    ) -> None:
        async with self.session_factory() as session:
            row = await session.get(GitHubCacheRow, url)
            if row is None:
                session.add(GitHubCacheRow(url=url, etag=etag, status_code=status_code, body=body))
            else:
                row.etag = etag
                row.status_code = status_code
                row.body = body
            await session.commit()
