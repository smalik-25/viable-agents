"""The three S1 worker agents: schema-valid, cited verdicts; retry-on-invalid.

Agents are exercised by calling ``handle()`` directly against a real ``Bus``
(registered addresses, real topology) rather than running the async loop, so a
test asserts exactly one causally-linked ``COMMAND``/``accountability`` envelope
landed on the S3 seat without needing to drain a task. ``BuildTriageAgent`` is the
one PLAN Phase 2 asks to be spot-checked against ground truth; that check lives in
``test_run.py`` where the full synthetic stream is available.

Phase 3 makes ``BuildTriageAgent`` coordinate through S2 by default
(``coordinate=True``), so its tests now drive the real claim/arbitrate round trip
via ``_process`` rather than a single ``handle()`` call -- this exercises the exact
path ``run.py`` uses, not a stand-in. ``tests/unit/test_s2_coordination.py`` covers
the coordination mechanism itself (contention, denial, the ledger); these tests
stay about what they were about in Phase 2: verdict shape, evidence, retry.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest

from viable_agents.composition import load_topology
from viable_agents.kernel import Bus, Channel, Envelope, Intent, NullTracer, RealClock, Role
from viable_agents.kernel.address import AgentAddress
from viable_agents.llm.client import (
    MAX_STRUCTURED_OUTPUT_ATTEMPTS,
    ScriptedLLMClient,
    StructuredOutputError,
)
from viable_agents.llm.config import load_models
from viable_agents.llm.recorder import InMemoryCallRecorder
from viable_agents.persistence import InMemorySink
from viable_agents.sources.events import (
    CIEvent,
    Conclusion,
    EventKind,
    JobResult,
    LogExcerpt,
    SourceMode,
)
from viable_agents.sources.events import (
    TestOutcome as CIOutcome,
)
from viable_agents.sources.events import (
    TestResult as CITestResult,
)
from viable_agents.systems.s1 import (
    BuildTriageAgent,
    DepAgent,
    DepDraft,
    FlakeAgent,
    TriageDecision,
    scripted_dep_responder,
    scripted_triage_responder,
)
from viable_agents.systems.s2 import Coordinator

CONFIG = Path(__file__).resolve().parents[2] / "config"
FLEET = ("fleet",)
S1_ADDR = AgentAddress(path=("fleet", "s1_under_test"), role=Role.S1)
S2_ADDR = AgentAddress(path=("fleet", "coordinator_0"), role=Role.S2)
S3_ADDR = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)
ENV_ADDR = AgentAddress(path=("fleet", "env_0"), role=Role.ENVIRONMENT)
_NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _bus() -> Bus:
    topology = load_topology(CONFIG / "topology" / "vsm.yaml")
    bus = Bus(topology=topology, clock=RealClock(), sink=InMemorySink(), run_id=uuid.uuid4())
    bus.register(S1_ADDR)
    bus.register(S2_ADDR)
    bus.register(S3_ADDR)
    return bus


async def _process(agent: BuildTriageAgent, bus: Bus, observation: Envelope) -> None:
    """Drive one event through ``BuildTriageAgent``'s real, default
    (``coordinate=True``) protocol: claim, a real ``Coordinator`` arbitrates, then
    the agent resumes and reports. Mirrors what ``run.py`` does with its own
    running ``Coordinator`` agent, just stepped by hand."""
    await agent.handle(observation)
    coordinator = Coordinator(address=S2_ADDR, bus=bus, clock=RealClock(), tracer=NullTracer())
    await coordinator.handle(await bus.receive(S2_ADDR))
    await agent.handle(await bus.receive(S1_ADDR))


def _observation(event: CIEvent, *, run_id: uuid.UUID) -> Envelope:
    clock = RealClock()
    return Envelope(
        run_id=run_id,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=ENV_ADDR,
        recipient=S1_ADDR,
        channel=Channel.ENVIRONMENT,
        intent=Intent.OBSERVATION,
        payload=event,
    )


def _regression_event(**overrides: object) -> CIEvent:
    fields: dict[str, object] = {
        "source_mode": SourceMode.SYNTHETIC,
        "event_kind": EventKind.WORKFLOW_RUN,
        "repo": "octo/widgets",
        "run_id": 42,
        "workflow_name": "ci",
        "conclusion": Conclusion.FAILURE,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "actor": "alice",
        "commit_message": "fix: handle edge case",
        "created_at": _NOW,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 1.0,
        "log_excerpts": (
            LogExcerpt(
                path="tests/test_ci.py",
                start_line=10,
                end_line=14,
                text="AssertionError: expected 42, got 41\n  at tests/test_ci.py::test_case_1",
            ),
        ),
    }
    fields.update(overrides)
    return CIEvent.model_validate(fields)


def _scripted_responder(output_model: type[Any], system: str, messages: Any) -> dict[str, Any]:
    if output_model is TriageDecision:
        return scripted_triage_responder(output_model, system, messages)
    if output_model is DepDraft:
        return scripted_dep_responder(output_model, system, messages)
    raise AssertionError(output_model)


def _llm(run_id: uuid.UUID, *, responder: Any = None) -> ScriptedLLMClient:
    models = load_models(CONFIG / "models.yaml")
    return ScriptedLLMClient(
        models=models,
        clock=RealClock(),
        recorder=InMemoryCallRecorder(),
        run_id=run_id,
        responder=responder or _scripted_responder,
    )


@pytest.mark.asyncio
async def test_build_triage_agent_reports_a_cited_regression_verdict() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    agent = BuildTriageAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id),
        model_tier="haiku",
        fleet_scope=FLEET,
    )
    event = _regression_event()
    observation = _observation(event, run_id=run_id)
    await _process(agent, bus, observation)

    assert len(agent.verdicts) == 1
    verdict = agent.verdicts[0]
    assert verdict.predicted_class.value == "regression"
    assert verdict.evidence, "a triage verdict must cite evidence (hard rule 6)"
    assert verdict.evidence[0].kind == "log"
    assert "AssertionError" in (verdict.evidence[0].excerpt or "")

    delivered = await bus.receive(S3_ADDR)
    assert delivered.channel is Channel.COMMAND
    assert delivered.intent is Intent.ACCOUNTABILITY
    assert delivered.causation_id == observation.id, "the report must be causally linked to it"
    assert delivered.payload.narrow(type(verdict)) == verdict


@pytest.mark.asyncio
async def test_build_triage_agent_skips_successful_runs() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    agent = BuildTriageAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id),
        model_tier="haiku",
        fleet_scope=FLEET,
    )
    event = _regression_event(conclusion=Conclusion.SUCCESS, log_excerpts=())
    await agent.handle(_observation(event, run_id=run_id))
    assert agent.verdicts == []
    assert bus.inbox_depth(S3_ADDR) == 0


@pytest.mark.asyncio
async def test_retry_on_invalid_structured_output_then_succeeds() -> None:
    calls: list[int] = []

    def flaky_responder(output_model: type[Any], system: str, messages: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            return {"predicted_class": "not-a-real-class", "confidence": 2.0, "rationale": "bad"}
        return scripted_triage_responder(output_model, system, messages)

    bus = _bus()
    run_id = uuid.uuid4()
    agent = BuildTriageAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id, responder=flaky_responder),
        model_tier="haiku",
        fleet_scope=FLEET,
    )
    await _process(agent, bus, _observation(_regression_event(), run_id=run_id))
    assert len(calls) == 2, "the first attempt should have failed validation and been retried"
    assert len(agent.verdicts) == 1


@pytest.mark.asyncio
async def test_retry_exhausted_raises_structured_output_error() -> None:
    def always_invalid(output_model: type[Any], system: str, messages: Any) -> dict[str, Any]:
        del output_model, system, messages
        return {"predicted_class": "not-a-real-class"}

    llm = _llm(uuid.uuid4(), responder=always_invalid)
    with pytest.raises(StructuredOutputError):
        await llm.complete(
            tier="haiku",
            system="s",
            messages=[{"role": "user", "content": "{}"}],
            output_model=TriageDecision,
            max_tokens=64,
            turn_id=uuid.uuid4(),
            agent=S1_ADDR,
        )


@pytest.mark.asyncio
async def test_flake_agent_flags_a_mixed_history_test() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    agent = FlakeAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        fleet_scope=FLEET,
    )
    outcomes = [CIOutcome.FAILED, CIOutcome.PASSED, CIOutcome.PASSED, CIOutcome.FAILED]
    for i, outcome in enumerate(outcomes):
        job = JobResult(
            name="ci",
            conclusion=Conclusion.FAILURE,
            started_at=_NOW,
            completed_at=_NOW,
            tests=(CITestResult(test_id="tests/test_x.py::test_flaky", outcome=outcome),),
        )
        event = _regression_event(run_id=100 + i, jobs=(job,), log_excerpts=())
        await agent.handle(_observation(event, run_id=run_id))

    assert len(agent.assessments) == 1
    assessment = agent.assessments[0]
    assert assessment.test_id == "tests/test_x.py::test_flaky"
    assert assessment.is_flaky
    assert 0.0 < assessment.observed_failure_rate < 1.0
    # Reported once: the 4th observation crossed the threshold again but must not
    # re-emit, or a long-running test would flood S3 with duplicate assessments.
    assert bus.inbox_depth(S3_ADDR) == 1


@pytest.mark.asyncio
async def test_flake_agent_ignores_a_uniformly_failing_test() -> None:
    """Always-fails is a regression signature, not flakiness (hard rule: the label
    is a real class in its own right, see AnomalyClass.NONE docstring)."""
    bus = _bus()
    run_id = uuid.uuid4()
    agent = FlakeAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        fleet_scope=FLEET,
    )
    for i in range(5):
        job = JobResult(
            name="ci",
            conclusion=Conclusion.FAILURE,
            started_at=_NOW,
            completed_at=_NOW,
            tests=(CITestResult(test_id="tests/test_x.py::test_broken", outcome=CIOutcome.FAILED),),
        )
        event = _regression_event(run_id=200 + i, jobs=(job,), log_excerpts=())
        await agent.handle(_observation(event, run_id=run_id))
    assert agent.assessments == []


@pytest.mark.asyncio
async def test_dep_agent_detects_a_bump_commit_and_drafts_a_summary() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    agent = DepAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id),
        model_tier="haiku",
        fleet_scope=FLEET,
    )
    event = _regression_event(
        commit_message="chore(deps): bump requests from 2.0.0 to 3.0.0",
        changed_files=("requirements.txt",),
        conclusion=Conclusion.FAILURE,
    )
    await agent.handle(_observation(event, run_id=run_id))
    assert len(agent.summaries) == 1
    summary = agent.summaries[0]
    assert summary.risk in ("low", "medium", "high")
    assert summary.manifest_files == ("requirements.txt",)


@pytest.mark.asyncio
async def test_dep_agent_ignores_a_non_dependency_commit() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    agent = DepAgent(
        address=S1_ADDR,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id),
        model_tier="haiku",
        fleet_scope=FLEET,
    )
    event = _regression_event(commit_message="fix: unrelated bug", changed_files=("src/x.py",))
    await agent.handle(_observation(event, run_id=run_id))
    assert agent.summaries == []


def test_max_structured_output_attempts_is_at_least_a_real_retry() -> None:
    assert MAX_STRUCTURED_OUTPUT_ATTEMPTS >= 2
