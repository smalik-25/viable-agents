"""Postgres-backed tests. Marked ``db``; skipped (never failed) when no database exists.

This is where Phase 1's "rows in Postgres" exit criterion is verified: the
migration applies from an empty database, an envelope is written and read back
with its payload intact, and the metadata has no pending migration drift.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text

from viable_agents.kernel import Channel, DeliveryStatus, Envelope, Intent, Payload, Role
from viable_agents.kernel.address import AgentAddress
from viable_agents.persistence import RunRow, envelope_to_row
from viable_agents.persistence.models import MessageRow

pytestmark = pytest.mark.db


class Note(Payload):
    kind: str = "test.persist_note"
    text: str


@pytest.mark.asyncio
async def test_migration_creates_every_table(db_session) -> None:
    rows = await db_session.execute(
        text("select tablename from pg_tables where schemaname='public' order by 1")
    )
    tables = {r[0] for r in rows}
    assert {"runs", "agents", "messages", "llm_calls", "channel_saturation"} <= tables


@pytest.mark.asyncio
async def test_envelope_row_persists_with_payload(db_session) -> None:
    run_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    db_session.add(
        RunRow(
            run_id=run_id,
            seed=7,
            source_mode="synthetic",
            fleet_config_name="vsm",
            topology_config_name="vsm",
            config_fingerprint="deadbeef",
            status="running",
        )
    )
    env = Envelope(
        run_id=run_id,
        ts_wall=now,
        ts_sim=now,
        sender=AgentAddress(path=("fleet", "controller_0"), role=Role.S3),
        recipient=AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1),
        channel=Channel.COMMAND,
        intent=Intent.ALLOCATION,
        payload=Note(text="allocated 500"),
    )
    db_session.add(
        envelope_to_row(
            env, seq=1, status=DeliveryStatus.DELIVERED, reason=None, rule_id="cmd_s3_s1_alloc"
        )
    )
    await db_session.commit()

    stored = (
        await db_session.execute(select(MessageRow).where(MessageRow.envelope_id == env.id))
    ).scalar_one()
    assert stored.channel == "command"
    assert stored.intent == "allocation"
    assert stored.rule_id == "cmd_s3_s1_alloc"
    assert stored.payload["text"] == "allocated 500"
    assert stored.payload_kind == "test.persist_note"


@pytest.mark.asyncio
async def test_rollback_isolates_tests(db_session) -> None:
    await db_session.execute(text("create table if not exists _probe (id int)"))
    await db_session.execute(text("insert into _probe values (1)"))
    await db_session.commit()  # lands on a SAVEPOINT, not the outer transaction
    count = (await db_session.execute(text("select count(*) from _probe"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_no_pending_migrations(engine) -> None:
    """Editing a model without a matching revision fails CI here."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from viable_agents.persistence.base import Base

    def _diff(sync_conn: object) -> list[object]:
        ctx = MigrationContext.configure(
            sync_conn,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(_diff) == []
