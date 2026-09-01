"""The real Anthropic client. Imported only by the ``live`` test, never the demo.

Kept in its own module so ``import viable_agents.llm`` does not pull ``anthropic``
onto the default path. Retry-on-invalid shares its policy (and its exception
type) with ``ScriptedLLMClient`` -- see ``llm/client.py`` -- so both paths behave
identically to a caller. Phase 3+ can still extend this (prompt caching, the
``messages.parse`` structured-output path) without touching that contract.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.clock import Clock
from viable_agents.kernel.cost import Usage
from viable_agents.kernel.llm import LLMResult
from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.client import (
    MAX_STRUCTURED_OUTPUT_ATTEMPTS,
    StructuredOutputError,
    check_budget_or_refuse,
)
from viable_agents.llm.config import ModelsConfig
from viable_agents.llm.pricing import cost_usd, price_for
from viable_agents.llm.recorder import CallRecorder, LLMCall


def _usage_from_response(raw: Any) -> Usage:
    creation = getattr(raw, "cache_creation", None)
    write_5m = getattr(creation, "ephemeral_5m_input_tokens", 0) if creation else 0
    write_1h = getattr(creation, "ephemeral_1h_input_tokens", 0) if creation else 0
    if not creation:
        write_5m = getattr(raw, "cache_creation_input_tokens", 0) or 0
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_5m_tokens=write_5m or 0,
        cache_write_1h_tokens=write_1h or 0,
    )


class AnthropicLLMClient:
    """Structured output via a single JSON instruction, parsed into the model."""

    def __init__(
        self,
        *,
        client: AsyncAnthropic,
        models: ModelsConfig,
        clock: Clock,
        recorder: CallRecorder,
        run_id: uuid.UUID,
        budget: BudgetGuard | None = None,
    ) -> None:
        self._client = client
        self._models = models
        self._clock = clock
        self._recorder = recorder
        self._run_id = run_id
        self._budget = budget

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
    ) -> LLMResult[T]:
        spec = self._models.spec(tier)
        schema = output_model.model_json_schema()
        full_system = (
            f"{system}\n\nRespond with ONLY a JSON object matching this schema, no prose:\n{schema}"
        )

        # Price selection depends only on today's date, not on the response, so it
        # is resolved once up front and reused for the pre-attempt budget check.
        price = price_for(spec.prices(), self._clock.now().date())

        last_error: Exception | None = None
        for attempt in range(MAX_STRUCTURED_OUTPUT_ATTEMPTS):
            await check_budget_or_refuse(
                self._budget,
                agent=agent,
                recorder=self._recorder,
                run_id=self._run_id,
                turn_id=turn_id,
                spec=spec,
                tier=tier,
                price=price,
                attempt=attempt,
                clock=self._clock,
            )
            started = time.monotonic()
            raw = await self._client.messages.create(
                model=spec.model_id,
                max_tokens=max_tokens,
                system=full_system,
                messages=list(messages),  # type: ignore[arg-type]
            )
            latency_ms = (time.monotonic() - started) * 1000.0
            text = "".join(getattr(block, "text", "") for block in raw.content)
            usage = _usage_from_response(raw.usage)
            cost = cost_usd(usage, price)
            request_id = raw._request_id  # noqa: SLF001 - public despite the underscore

            try:
                parsed = output_model.model_validate_json(text)
            except ValidationError as exc:
                last_error = exc
                await self._recorder.record(
                    LLMCall(
                        id=uuid.uuid4(),
                        run_id=self._run_id,
                        turn_id=turn_id,
                        agent_path=agent.canonical(),
                        agent_role=agent.role.value,
                        vsm_level=agent.level,
                        model_id=spec.model_id,
                        tier=tier,
                        usage=usage,
                        cost_usd=cost,
                        price_effective_date=price.effective_from,
                        latency_ms=latency_ms,
                        attempt_index=attempt,
                        succeeded=False,
                        stop_reason="invalid_structured_output",
                        error=str(exc)[:256],
                        request_id=request_id,
                        trace_id=None,
                        ts_wall=self._clock.wall(),
                        ts_sim=self._clock.now(),
                    )
                )
                if self._budget is not None:
                    self._budget.record(agent, cost)
                continue

            await self._recorder.record(
                LLMCall(
                    id=uuid.uuid4(),
                    run_id=self._run_id,
                    turn_id=turn_id,
                    agent_path=agent.canonical(),
                    agent_role=agent.role.value,
                    vsm_level=agent.level,
                    model_id=spec.model_id,
                    tier=tier,
                    usage=usage,
                    cost_usd=cost,
                    price_effective_date=price.effective_from,
                    latency_ms=latency_ms,
                    attempt_index=attempt,
                    succeeded=True,
                    stop_reason=raw.stop_reason or "end_turn",
                    error=None,
                    request_id=request_id,
                    trace_id=None,
                    ts_wall=self._clock.wall(),
                    ts_sim=self._clock.now(),
                )
            )
            if self._budget is not None:
                self._budget.record(agent, cost)
            return LLMResult(
                parsed=parsed,
                usage=usage,
                cost_usd=cost,
                model_id=spec.model_id,
                tier=tier,
                stop_reason=raw.stop_reason or "end_turn",
                request_id=request_id,
                latency_ms=latency_ms,
                attempt_index=attempt,
            )

        msg = (
            f"structured output for {output_model.__name__} failed validation after "
            f"{MAX_STRUCTURED_OUTPUT_ATTEMPTS} attempt(s): {last_error}"
        )
        raise StructuredOutputError(msg)
