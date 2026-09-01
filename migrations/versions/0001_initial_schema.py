"""initial schema: runs, agents, messages, llm_calls, channel_saturation

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-21

The first migration builds the whole schema directly from the declarative
metadata, so the tables are exactly what the models describe and the
"no pending migrations" test finds no drift. Later phases add columns and tables
with explicit ``op`` operations; only this initial revision uses ``create_all``.
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


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
