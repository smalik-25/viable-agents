"""Postgres-backed: RunReports actually land in the database.

Uses ``engine``/``make_session_factory`` directly rather than the ``db_session``
SAVEPOINT fixture, the same way ``test_github_cache.py`` does: the real
``PostgresRunReportRecorder`` opens and commits its own session per call, so a
row inserted through the ``db_session`` fixture's uncommitted savepoint would
not be visible to it (the FK to ``runs.run_id`` would fail).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from viable_agents.persistence import RunRow, make_session_factory
from viable_agents.persistence.models import RunReportRow
from viable_agents.persistence.run_reports import PostgresRunReportRecorder, RunReportRecord
from viable_agents.systems.s3.reports import AgentBreakdown, RunReport

pytestmark = pytest.mark.db

_NOW = dt.datetime.now(dt.UTC)


@pytest.mark.asyncio
async def test_postgres_recorder_persists_a_run_report_with_its_breakdown(engine) -> None:
    factory = make_session_factory(engine)
    run_id = uuid.uuid4()
    recorder = PostgresRunReportRecorder(session_factory=factory)

    async with factory() as session:
        session.add(
            RunRow(
                run_id=run_id,
                seed=1,
                source_mode="synthetic",
                fleet_config_name="vsm",
                topology_config_name="vsm",
                config_fingerprint="deadbeef",
                status="running",
            )
        )
        await session.commit()

    try:
        report = RunReport(
            seq=1,
            per_agent={
                "fleet/build_triage_0": AgentBreakdown(
                    agent_path="fleet/build_triage_0",
                    role="s1",
                    reports_seen=3,
                    anomalies_seen=1,
                    cost_usd=Decimal("0.05"),
                    cap_usd=Decimal("0.15"),
                    state="running",
                )
            },
            total_cost_usd=Decimal("0.05"),
            total_anomalies=1,
            narrative="one sentence",
        )
        await recorder.record(
            RunReportRecord(
                id=uuid.uuid4(), run_id=run_id, ts_wall=_NOW, ts_sim=_NOW, report=report
            )
        )

        async with factory() as session:
            stored = (
                await session.execute(select(RunReportRow).where(RunReportRow.run_id == run_id))
            ).scalar_one()
            assert stored.seq == 1
            assert stored.total_anomalies == 1
            assert stored.total_cost_usd == Decimal("0.05000000")
            assert stored.narrative == "one sentence"
            breakdown = stored.per_agent["fleet/build_triage_0"]
            assert breakdown["reports_seen"] == 3
            assert breakdown["anomalies_seen"] == 1
            assert breakdown["state"] == "running"
    finally:
        async with factory() as session:
            rows = (
                await session.execute(select(RunReportRow).where(RunReportRow.run_id == run_id))
            ).scalars()
            for row in rows:
                await session.delete(row)
            run_row = await session.get(RunRow, run_id)
            if run_row is not None:
                await session.delete(run_row)
            await session.commit()


@pytest.mark.asyncio
async def test_seq_is_monotonic_per_run(engine) -> None:
    factory = make_session_factory(engine)
    run_id = uuid.uuid4()
    recorder = PostgresRunReportRecorder(session_factory=factory)

    async with factory() as session:
        session.add(
            RunRow(
                run_id=run_id,
                seed=1,
                source_mode="synthetic",
                fleet_config_name="vsm",
                topology_config_name="vsm",
                config_fingerprint="deadbeef",
                status="running",
            )
        )
        await session.commit()

    try:
        for seq in (1, 2):
            await recorder.record(
                RunReportRecord(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    ts_wall=_NOW,
                    ts_sim=_NOW,
                    report=RunReport(
                        seq=seq, per_agent={}, total_cost_usd=Decimal("0"), total_anomalies=0
                    ),
                )
            )

        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(RunReportRow)
                        .where(RunReportRow.run_id == run_id)
                        .order_by(RunReportRow.seq)
                    )
                )
                .scalars()
                .all()
            )
            assert [r.seq for r in rows] == [1, 2]
    finally:
        async with factory() as session:
            leftover = (
                await session.execute(select(RunReportRow).where(RunReportRow.run_id == run_id))
            ).scalars()
            for row in leftover:
                await session.delete(row)
            run_row = await session.get(RunRow, run_id)
            if run_row is not None:
                await session.delete(run_row)
            await session.commit()
