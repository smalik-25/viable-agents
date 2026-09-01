"""Async engine and session factory.

One ``AsyncSession`` is not safe across concurrent tasks: the bus fans agent turns
out with ``asyncio.gather``, and each turn must open its own session from the
factory, never share one, or asyncpg raises "another operation is in progress"
intermittently under load. ``expire_on_commit=False`` is required so attribute
access after commit does not trigger a lazy reload.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
