"""The Phase 2 exit criterion, end to end: 500 synthetic CI events, no database,
no API key. Same code path ``viable-agents run --source synthetic --events 500``
runs; this is what gates the v0.2 tag, the way ``test_demo.py`` gates v0.1.

Phase 4's exit criterion rides the same harness: ``starved_budget=True`` selects
``config/budgets.yaml``'s ``starved_run_budget_usd`` pool, forcing S3 to shed
low-priority S1 work over the course of a real run rather than in an isolated
unit test.
"""

from __future__ import annotations

import uuid

import pytest

from viable_agents.run import _run


@pytest.mark.asyncio
async def test_500_synthetic_events_meets_every_exit_check() -> None:
    result = await _run(
        run_id=uuid.uuid4(),
        source_mode="synthetic",
        events=500,
        scenario_name="normal-load",
        live_llm=False,
        use_postgres=False,
    )
    failed = [label for label, passed in result.checks.items() if not passed]
    assert not failed, f"run checks failed: {failed}"
    assert result.events_seen == 500
    assert result.triage_verdicts > 0
    assert result.flake_assessments > 0
    assert result.dep_summaries > 0
    assert result.agreement is not None
    assert result.agreement >= 0.70, "BuildTriageAgent should broadly agree with ground truth"
    assert result.llm_cost > 0
    assert result.run_reports > 0, "Controller must land at least one RunReport (at drain)"


@pytest.mark.asyncio
async def test_starved_budget_sheds_gracefully_without_degrading() -> None:
    """Phase 4 exit criterion: a deliberately starved run pauses low-priority S1
    work rather than crashing, and no agent -- shed or not -- reaches DEGRADED."""
    result = await _run(
        run_id=uuid.uuid4(),
        source_mode="synthetic",
        events=500,
        scenario_name="normal-load",
        live_llm=False,
        use_postgres=False,
        starved_budget=True,
    )
    failed = [label for label, passed in result.checks.items() if not passed]
    assert not failed, f"run checks failed: {failed}"
    assert result.paused_agents, "the starved pool must force at least one shed"
    assert result.run_reports > 0


@pytest.mark.asyncio
async def test_run_is_deterministic_given_the_same_scenario() -> None:
    kwargs = {
        "source_mode": "synthetic",
        "events": 120,
        "scenario_name": "normal-load",
        "live_llm": False,
        "use_postgres": False,
    }
    a = await _run(run_id=uuid.uuid4(), **kwargs)  # type: ignore[arg-type]
    b = await _run(run_id=uuid.uuid4(), **kwargs)  # type: ignore[arg-type]
    assert a.triage_verdicts == b.triage_verdicts
    assert a.flake_assessments == b.flake_assessments
    assert a.dep_summaries == b.dep_summaries
    assert a.agreement == b.agreement
    assert a.llm_cost == b.llm_cost
