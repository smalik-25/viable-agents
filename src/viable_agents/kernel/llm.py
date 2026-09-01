"""The model-client seam. The kernel defines the Protocol and never imports anthropic.

Implementations live in ``llm/``: a metered client that records every call to
Postgres (hard rule 3), and a ``ScriptedLLMClient`` with deterministic usage so
cost assertions have exact expected values and CI needs no API key. ``tier`` is a
string resolved from ``config/models.yaml`` by role and path, never a model id,
and the tier profile carries capability flags because the tiers are not
request-compatible (Sonnet 5 rejects non-default sampling params; Haiku 4.5
rejects ``effort``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.cost import Usage


@dataclass(frozen=True, slots=True)
class LLMResult[T]:
    """The outcome of one model invocation, ready to become an ``llm_calls`` row."""

    parsed: T
    usage: Usage
    cost_usd: Decimal
    model_id: str
    tier: str
    stop_reason: str
    request_id: str | None = None
    latency_ms: float = 0.0
    attempt_index: int = 0
    succeeded: bool = True
    error: str | None = None
    rate_limit: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """Implemented outside ``kernel/``. The only legal path from an agent to a model."""

    async def complete[T: BaseModel](
        self,
        *,
        tier: str,
        system: str,
        messages: Sequence[object],
        output_model: type[T],
        max_tokens: int,
        turn_id: uuid.UUID,
        agent: AgentAddress,
    ) -> LLMResult[T]: ...
