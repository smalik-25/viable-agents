"""Alembic environment, async.

Three edits over the stock template that it gets wrong for this project: the
target metadata is wired to the app's ``Base`` (stock is None), the URL is read
from ``DATABASE_URL`` rather than ``alembic.ini`` (ConfigParser eats ``%`` in a
URL, and creds do not belong in a tracked file), and ``compare_type`` /
``compare_server_default`` are turned on so autogenerate and the "no pending
migrations" test actually detect column changes.

This module is driven programmatically via ``alembic.command`` from the test
fixture, not the ``alembic`` CLI.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import the models so Base.metadata is fully populated before autogenerate runs.
from viable_agents.persistence import models  # noqa: F401
from viable_agents.persistence.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        msg = "DATABASE_URL is not set"
        raise RuntimeError(msg)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
