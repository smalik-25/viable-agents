"""LLM clients implementing the kernel's ``LLMClient`` protocol.

``ScriptedLLMClient`` returns deterministic, priced fake responses so cost
accounting runs end to end with no API key and the sum assertions have exact
expected values. The real Anthropic client is a thin wrapper reached only by the
``live`` test. Both record every call through a ``CallRecorder``, so no code path
reaches a model without cost accounting (hard rule 3), including failed and
retried attempts (``attempt_index``, ``succeeded=False`` rows): a triage verdict
that fails schema validation once and succeeds on retry still cost a call.

Retry-on-invalid (hard rule 6) lives here rather than in the kernel, because it is
a model-client concern, not a routing one: ``LLMClient.complete`` promises a
valid ``T`` or an exception, never a caller-visible partial result.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.clock import Clock
from viable_agents.kernel.cost import Usage
from viable_agents.kernel.errors import BudgetExceededError
from viable_agents.kernel.llm import LLMResult
from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.config import ModelsConfig, TierSpec
from viable_agents.llm.pricing import Price, cost_usd, price_for
from viable_agents.llm.recorder import CallRecorder, LLMCall

Responder = Callable[[type[BaseModel], str, Sequence[object]], dict[str, Any]]

_DEFAULT_USAGE = Usage(input_tokens=1200, output_tokens=200)

# Initial attempt plus one retry. A structured-output failure is almost always a
# formatting slip a second attempt fixes; more than one retry mostly buys extra
# cost against the $50 project ceiling for the same eventual failure.
MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2


class StructuredOutputError(RuntimeError):
    """Every retry attempt failed schema validation. Callers see this, never a partial T."""


async def check_budget_or_refuse(
    guard: BudgetGuard | None,
    *,
    agent: AgentAddress,
    recorder: CallRecorder,
    run_id: uuid.UUID,
    turn_id: uuid.UUID,
    spec: TierSpec,
    tier: str,
    price: Price,
    attempt: int,
    clock: Clock,
) -> None:
    """Shared by both ``LLMClient`` implementations: raise ``BudgetExceededError``
    (after recording a $0 refusal row, so a refusal is data and not only an
    exception -- hard rule 3) if ``guard`` says this agent or the project is
    already at cap. A no-op when ``guard`` is ``None``, since not every caller
    (most tests, the scripted default path outside ``run.py``) wires one in.

    Called once per retry ATTEMPT, not once per ``complete()``: a validation
    retry records its own full-cost row, so a check only at the top of
    ``complete()`` would let a retried attempt cross the cap unrefused.
    """
    if guard is None:
        return
    try:
        guard.check(agent)
    except BudgetExceededError:
        await recorder.record(
            LLMCall(
                id=uuid.uuid4(),
                run_id=run_id,
                turn_id=turn_id,
                agent_path=agent.canonical(),
                agent_role=agent.role.value,
                vsm_level=agent.level,
                model_id=spec.model_id,
                tier=tier,
                usage=Usage(),
                cost_usd=Decimal("0"),
                price_effective_date=price.effective_from,
                latency_ms=0.0,
                attempt_index=attempt,
                succeeded=False,
                stop_reason="budget_exceeded",
                error="budget_exceeded",
                request_id=None,
                trace_id=None,
                ts_wall=clock.wall(),
                ts_sim=clock.now(),
            )
        )
        raise


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
        budget: BudgetGuard | None = None,
    ) -> None:
        self._models = models
        self._clock = clock
        self._recorder = recorder
        self._run_id = run_id
        self._responder = responder
        self._usage = usage
        self._budget = budget

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

        last_error: ValidationError | None = None
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
            raw = self._responder(output_model, system, messages)
            try:
                parsed = output_model.model_validate(raw)
            except ValidationError as exc:
                last_error = exc
                await self._recorder.record(
                    self._call_row(
                        turn_id=turn_id,
                        agent=agent,
                        spec=spec,
                        tier=tier,
                        price=price,
                        cost=cost,
                        attempt=attempt,
                        succeeded=False,
                        stop_reason="invalid_structured_output",
                        error=str(exc)[:256],
                    )
                )
                if self._budget is not None:
                    self._budget.record(agent, cost)
                continue
            await self._recorder.record(
                self._call_row(
                    turn_id=turn_id,
                    agent=agent,
                    spec=spec,
                    tier=tier,
                    price=price,
                    cost=cost,
                    attempt=attempt,
                    succeeded=True,
                    stop_reason="end_turn",
                    error=None,
                )
            )
            if self._budget is not None:
                self._budget.record(agent, cost)
            return LLMResult(
                parsed=parsed,
                usage=self._usage,
                cost_usd=cost,
                model_id=spec.model_id,
                tier=tier,
                stop_reason="end_turn",
                attempt_index=attempt,
            )

        msg = (
            f"structured output for {output_model.__name__} failed validation after "
            f"{MAX_STRUCTURED_OUTPUT_ATTEMPTS} attempt(s): {last_error}"
        )
        raise StructuredOutputError(msg)

    def _call_row(
        self,
        *,
        turn_id: uuid.UUID,
        agent: AgentAddress,
        spec: TierSpec,
        tier: str,
        price: Price,
        cost: Decimal,
        attempt: int,
        succeeded: bool,
        stop_reason: str,
        error: str | None,
    ) -> LLMCall:
        return LLMCall(
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
            attempt_index=attempt,
            succeeded=succeeded,
            stop_reason=stop_reason,
            error=error,
            request_id=None,
            trace_id=None,
            ts_wall=self._clock.wall(),
            ts_sim=self._clock.now(),
        )
