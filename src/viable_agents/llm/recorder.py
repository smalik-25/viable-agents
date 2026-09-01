"""The llm_calls ledger seam.

Every model invocation, including failed and retried ones, becomes an ``LLMCall``
and is recorded here. ``InMemoryCallRecorder`` keeps them in a list for tests and
the demo; ``PostgresCallRecorder`` writes ``llm_calls`` rows. Denormalizing
``vsm_level`` onto the row is deliberate: the ablation aggregates spend by VSM
level while Phase 4 budgets are agent-keyed, so both are needed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from viable_agents.kernel.cost import Usage
from viable_agents.persistence.models import LLMCallRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class LLMCall:
    id: uuid.UUID
    run_id: uuid.UUID
    turn_id: uuid.UUID | None
    agent_path: str
    agent_role: str
    vsm_level: int
    model_id: str
    tier: str
    usage: Usage
    cost_usd: Decimal
    price_effective_date: dt.date
    latency_ms: float
    attempt_index: int
    succeeded: bool
    stop_reason: str | None
    error: str | None
    request_id: str | None
    trace_id: str | None
    ts_wall: dt.datetime
    ts_sim: dt.datetime
    provider: str = "anthropic"

    def to_row(self) -> LLMCallRow:
        return LLMCallRow(
            id=self.id,
            run_id=self.run_id,
            turn_id=self.turn_id,
            agent_path=self.agent_path,
            agent_role=self.agent_role,
            vsm_level=self.vsm_level,
            provider=self.provider,
            model_id=self.model_id,
            tier=self.tier,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            cache_read_tokens=self.usage.cache_read_tokens,
            cache_write_5m_tokens=self.usage.cache_write_5m_tokens,
            cache_write_1h_tokens=self.usage.cache_write_1h_tokens,
            cost_usd=self.cost_usd,
            price_effective_date=self.price_effective_date,
            latency_ms=self.latency_ms,
            attempt_index=self.attempt_index,
            succeeded=self.succeeded,
            stop_reason=self.stop_reason,
            error=self.error,
            request_id=self.request_id,
            trace_id=self.trace_id,
            ts_wall=self.ts_wall,
            ts_sim=self.ts_sim,
        )


@runtime_checkable
class CallRecorder(Protocol):
    async def record(self, call: LLMCall) -> None: ...


class InMemoryCallRecorder:
    def __init__(self) -> None:
        self.calls: list[LLMCall] = []

    async def record(self, call: LLMCall) -> None:
        self.calls.append(call)

    def total_cost_usd(self) -> Decimal:
        return sum((c.cost_usd for c in self.calls), Decimal("0"))


@dataclass
class PostgresCallRecorder:
    session_factory: async_sessionmaker[AsyncSession]

    async def record(self, call: LLMCall) -> None:
        async with self.session_factory() as session:
            session.add(call.to_row())
            await session.commit()
