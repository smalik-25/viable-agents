"""LLM clients implementing the kernel's ``LLMClient`` protocol.

``ScriptedLLMClient`` returns deterministic, priced fake responses so cost
accounting runs end to end with no API key and the sum assertions have exact
expected values. The real Anthropic client is a thin wrapper reached only by the
``live`` test. Both record every call through a ``CallRecorder``, so no code path
reaches a model without cost accounting (hard rule 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.clock import Clock
from viable_agents.kernel.cost import Usage
from viable_agents.kernel.llm import LLMResult
from viable_agents.llm.config import ModelsConfig
from viable_agents.llm.pricing import cost_usd, price_for
from viable_agents.llm.recorder import CallRecorder, LLMCall

Responder = Callable[[type[BaseModel], str, Sequence[object]], dict[str, Any]]

_DEFAULT_USAGE = Usage(input_tokens=1200, output_tokens=200)


class ScriptedLLMClient:
    """Deterministic client: a fixed usage profile and a caller-supplied responder.

    The responder maps (output_model, system, messages) to the fields of a
    structured response, which is then validated into the model. Cost is computed
    from the real pricing table for the tier, so the recorded ledger is exact.
    """

    def __init__(
        self,
        *,
        models: ModelsConfig,
        clock: Clock,
        recorder: CallRecorder,
        run_id: uuid.UUID,
        responder: Responder,
        usage: Usage = _DEFAULT_USAGE,
    ) -> None:
        self._models = models
        self._clock = clock
        self._recorder = recorder
        self._run_id = run_id
        self._responder = responder
        self._usage = usage

    async def complete[T: BaseModel](
        self,
        *,
        tier: str,
        system: str,
        messages: Sequence[object],
        output_model: type[T],
        max_tokens: int,  # noqa: ARG002 - the scripted client ignores generation params
        turn_id: uuid.UUID,
        agent: AgentAddress,
    ) -> LLMResult[T]:
        spec = self._models.spec(tier)
        at = self._clock.now().date()
        price = price_for(spec.prices(), at)
        cost = cost_usd(self._usage, price)
        parsed = output_model.model_validate(self._responder(output_model, system, messages))
        call = LLMCall(
            id=uuid.uuid4(),
            run_id=self._run_id,
            turn_id=turn_id,
            agent_path=agent.canonical(),
            agent_role=agent.role.value,
            vsm_level=agent.level,
            model_id=spec.model_id,
            tier=tier,
            usage=self._usage,
            cost_usd=cost,
            price_effective_date=price.effective_from,
            latency_ms=0.0,
            attempt_index=0,
            succeeded=True,
            stop_reason="end_turn",
            error=None,
            request_id=None,
            trace_id=None,
            ts_wall=self._clock.wall(),
            ts_sim=self._clock.now(),
        )
        await self._recorder.record(call)
        return LLMResult(
            parsed=parsed,
            usage=self._usage,
            cost_usd=cost,
            model_id=spec.model_id,
            tier=tier,
            stop_reason="end_turn",
        )
