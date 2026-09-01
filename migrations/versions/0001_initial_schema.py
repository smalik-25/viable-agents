"""initial schema: runs, agents, messages, llm_calls, channel_saturation

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-21

The first migration builds its five tables directly from the declarative
metadata, so they are exactly what the models describe and the "no pending
migrations" test finds no drift. Later phases add columns and tables with
explicit ``op`` operations; only this initial revision uses ``create_all``.

``create_all`` is bound to a table list, not run unqualified: ``Base.metadata``
is a live registry of every model currently imported, not a snapshot of what
existed when this revision was written. An unqualified ``create_all()`` would
silently pick up ``github_cache`` (added to ``models.py`` for 0002) and try to
create it here too, colliding with 0002's own explicit ``create_table`` on a
fresh database. Naming the five original tables keeps this revision's output
fixed regardless of what gets added to ``models.py`` later.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from viable_agents.persistence import models  # noqa: F401  populate the metadata
from viable_agents.persistence.base import Base

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("runs", "agents", "messages", "llm_calls", "channel_saturation")


def upgrade() -> None:
    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)


def downgrade() -> None:
    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables)
