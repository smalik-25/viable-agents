"""The run_reports ledger seam. Mirrors ``llm/recorder.py``'s ``CallRecorder``
triad exactly, so ``Controller`` never imports SQLAlchemy directly.

``RunReport`` (``systems/s3/reports.py``) is a wire-ready ``Payload`` and does
not itself carry the fleet run id or timestamps -- the same separation an
``Envelope`` draws for every other payload. ``RunReportRecord`` supplies them,
the way ``LLMCall`` wraps an ``LLMResult`` for the ``llm_calls`` ledger.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from viable_agents.persistence.models import RunReportRow

if TYPE_CHECKING:
    # Deferred: persistence is a low-level package, and importing systems.s3.reports
    # at runtime here would trigger systems/s3/__init__.py (which pulls in
    # controller.py, which imports from llm.client) while llm.client's own
    # module-level import of llm.recorder -> persistence is still mid-initialization
    # -- a real circular import, not a style preference. RunReportRecord only ever
    # holds an already-constructed RunReport, so the type is never needed at runtime.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from viable_agents.systems.s3.reports import RunReport


@dataclass(frozen=True, slots=True)
class RunReportRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    ts_wall: dt.datetime
    ts_sim: dt.datetime
    report: RunReport

    def to_row(self) -> RunReportRow:
        dumped = self.report.model_dump(mode="json")
        return RunReportRow(
            id=self.id,
            run_id=self.run_id,
            seq=self.report.seq,
            ts_wall=self.ts_wall,
            ts_sim=self.ts_sim,
            per_agent=dumped["per_agent"],
            total_cost_usd=self.report.total_cost_usd,
            total_anomalies=self.report.total_anomalies,
            narrative=self.report.narrative,
        )


@runtime_checkable
class RunReportRecorder(Protocol):
    async def record(self, record: RunReportRecord) -> None: ...


class InMemoryRunReportRecorder:
    def __init__(self) -> None:
        self.records: list[RunReportRecord] = []

    async def record(self, record: RunReportRecord) -> None:
        self.records.append(record)


@dataclass
class PostgresRunReportRecorder:
    session_factory: async_sessionmaker[AsyncSession]

    async def record(self, record: RunReportRecord) -> None:
        async with self.session_factory() as session:
            session.add(record.to_row())
            await session.commit()
