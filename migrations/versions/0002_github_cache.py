"""github_cache table

Revision ID: 0002_github_cache
Revises: 0001_initial_schema
Create Date: 2026-08-31

Phase 2's read-only GitHub adapter cache: one row per fetched URL, not run-scoped
(see ``GitHubCacheRow`` docstring). An explicit ``op`` revision, not
``create_all``: only the initial schema uses that shortcut.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_github_cache"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_cache",
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("url", name=op.f("pk_github_cache")),
    )


def downgrade() -> None:
    op.drop_table("github_cache")
