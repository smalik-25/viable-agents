"""BudgetGuard: hard per-agent and project-wide caps.

Exercises the guard directly (pure code, no bus) and through
``ScriptedLLMClient`` (the actual enforcement point -- ``kernel/bus.py``'s
``add_pre_send`` cannot see an LLM call at all, see ``llm/budget.py``'s module
docstring). The retry-loop regression test is the one that matters most: a
guard checked only once at the top of ``complete()`` would let a validation
retry cross the cap unrefused, since each attempt records its own full-cost row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.channels import Role
from viable_agents.kernel.clock import RealClock
from viable_agents.kernel.cost import Usage
from viable_agents.kernel.errors import BudgetExceededError
from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.client import ScriptedLLMClient
from viable_agents.llm.config import load_models
from viable_agents.llm.pricing import cost_usd, price_for
from viable_agents.llm.recorder import InMemoryCallRecorder
from viable_agents.systems.s1.verdicts import TriageDecision

CONFIG = Path(__file__).resolve().parents[2] / "config"
AGENT = AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1)
OTHER = AgentAddress(path=("fleet", "dep_0"), role=Role.S1)
_PROJECT_CEILING = Decimal("50")


def _haiku_call_cost() -> Decimal:
    models = load_models(CONFIG / "models.yaml")
    spec = models.spec("haiku")
    price = price_for(spec.prices(), RealClock().now().date())
    return cost_usd(Usage(input_tokens=1200, output_tokens=200), price)  # matches _DEFAULT_USAGE


def _valid_responder(output_model: type[BaseModel], system: str, messages: Any) -> dict[str, Any]:
    del output_model, system, messages
    return {"predicted_class": "regression", "confidence": 0.7, "rationale": "r"}


def test_check_allows_under_cap_and_refuses_at_cap() -> None:
    guard = BudgetGuard(
        per_agent_caps_usd={AGENT.canonical(): _haiku_call_cost()},
        project_ceiling_usd=_PROJECT_CEILING,
    )
    guard.check(AGENT)  # under cap: no raise
    guard.record(AGENT, _haiku_call_cost())
    with pytest.raises(BudgetExceededError) as exc_info:
        guard.check(AGENT)
    assert exc_info.value.agent == AGENT
    assert exc_info.value.spent_usd == _haiku_call_cost()


def test_agents_have_independent_caps() -> None:
    guard = BudgetGuard(
        per_agent_caps_usd={AGENT.canonical(): _haiku_call_cost()},
        project_ceiling_usd=_PROJECT_CEILING,
    )
    guard.record(AGENT, _haiku_call_cost())
    with pytest.raises(BudgetExceededError):
        guard.check(AGENT)
    guard.check(OTHER)  # a different agent's cap is untouched


def test_agent_with_no_configured_cap_is_still_bound_by_the_project_ceiling() -> None:
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_haiku_call_cost())
    guard.check(AGENT)  # no per-agent cap: allowed
    guard.record(AGENT, _haiku_call_cost())
    with pytest.raises(BudgetExceededError):
        guard.check(AGENT)


def test_spent_and_cap_are_queryable_for_controller_pool_bookkeeping() -> None:
    guard = BudgetGuard(
        per_agent_caps_usd={AGENT.canonical(): _haiku_call_cost()},
        project_ceiling_usd=_PROJECT_CEILING,
    )
    assert guard.spent(AGENT) == 0
    assert guard.cap(AGENT) == _haiku_call_cost()
    assert guard.cap(OTHER) is None
    guard.record(AGENT, _haiku_call_cost())
    assert guard.spent(AGENT) == _haiku_call_cost()


@pytest.mark.asyncio
async def test_scripted_client_refuses_once_cumulative_spend_reaches_cap() -> None:
    per_call = _haiku_call_cost()
    guard = BudgetGuard(
        per_agent_caps_usd={AGENT.canonical(): per_call}, project_ceiling_usd=_PROJECT_CEILING
    )
    recorder = InMemoryCallRecorder()
    client = ScriptedLLMClient(
        models=load_models(CONFIG / "models.yaml"),
        clock=RealClock(),
        recorder=recorder,
        run_id=uuid.uuid4(),
        responder=_valid_responder,
        budget=guard,
    )

    first = await client.complete(
        tier="haiku",
        system="s",
        messages=[{"role": "user", "content": "{}"}],
        output_model=TriageDecision,
        max_tokens=64,
        turn_id=uuid.uuid4(),
        agent=AGENT,
    )
    assert first.parsed.predicted_class.value == "regression"

    with pytest.raises(BudgetExceededError):
        await client.complete(
            tier="haiku",
            system="s",
            messages=[{"role": "user", "content": "{}"}],
            output_model=TriageDecision,
            max_tokens=64,
            turn_id=uuid.uuid4(),
            agent=AGENT,
        )

    refusal_rows = [c for c in recorder.calls if c.error == "budget_exceeded"]
    assert len(refusal_rows) == 1
    assert refusal_rows[0].cost_usd == 0
    assert refusal_rows[0].succeeded is False


@pytest.mark.asyncio
async def test_retry_attempt_is_checked_against_budget_not_just_the_first_attempt() -> None:
    """The regression this file exists to guard: a check only at the top of
    ``complete()`` would let a validation retry cross the cap unrefused, since
    the failed first attempt already recorded a full-cost row."""
    per_call = _haiku_call_cost()
    calls: list[int] = []

    def flaky_then_valid(
        output_model: type[BaseModel], system: str, messages: Any
    ) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            return {"predicted_class": "not-a-real-class", "confidence": 2.0, "rationale": "bad"}
        return _valid_responder(output_model, system, messages)

    # Cap equals exactly one call's cost: the first (failed) attempt spends it
    # all, so the retry must be refused if the check runs per-attempt.
    guard = BudgetGuard(
        per_agent_caps_usd={AGENT.canonical(): per_call}, project_ceiling_usd=_PROJECT_CEILING
    )
    recorder = InMemoryCallRecorder()
    client = ScriptedLLMClient(
        models=load_models(CONFIG / "models.yaml"),
        clock=RealClock(),
        recorder=recorder,
        run_id=uuid.uuid4(),
        responder=flaky_then_valid,
        budget=guard,
    )

    with pytest.raises(BudgetExceededError):
        await client.complete(
            tier="haiku",
            system="s",
            messages=[{"role": "user", "content": "{}"}],
            output_model=TriageDecision,
            max_tokens=64,
            turn_id=uuid.uuid4(),
            agent=AGENT,
        )

    assert len(calls) == 1, "the retry must never reach the responder once over cap"
    assert guard.spent(AGENT) == per_call
