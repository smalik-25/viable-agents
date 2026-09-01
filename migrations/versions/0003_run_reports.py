"""run_reports table

Revision ID: 0003_run_reports
Revises: 0002_github_cache
Create Date: 2026-09-01

Phase 4's periodic S3 accountability snapshot: one row per RunReport emission,
run-scoped (unlike ``github_cache``). An explicit ``op`` revision, following
0002's pattern; only 0001 uses ``create_all``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_run_reports"
down_revision: str | None = "0002_github_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_reports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts_wall", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_sim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("per_agent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("total_anomalies", sa.Integer(), nullable=False),
        sa.Column("narrative", sa.String(length=1024), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.run_id"], name=op.f("fk_run_reports_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_reports")),
    )
    op.create_index("ix_run_reports_run_seq", "run_reports", ["run_id", "seq"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_run_reports_run_seq", table_name="run_reports")
    op.drop_table("run_reports")
