"""Controller: the running-pool monitor (shed/resume) and RunReport cadence.

Budget spend is simulated by calling ``BudgetGuard.record()`` directly -- the
guard's own enforcement (check-before-attempt, per-retry) is covered end to end
in ``test_budget_guard.py`` -- so these tests isolate Controller's REACTION to
spend from the LLM client that normally produces it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from viable_agents.composition import load_topology
from viable_agents.kernel import Bus, Channel, Envelope, Intent, NullTracer, RealClock, Role
from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.envelope import Citation
from viable_agents.kernel.payload import Payload
from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.client import ScriptedLLMClient
from viable_agents.llm.config import load_models
from viable_agents.llm.recorder import InMemoryCallRecorder
from viable_agents.persistence import InMemorySink
from viable_agents.persistence.run_reports import InMemoryRunReportRecorder
from viable_agents.sources.events import AnomalyClass
from viable_agents.systems.s1.verdicts import DepSummary, FlakeAssessment, TriageVerdict
from viable_agents.systems.s3 import scripted_narrative_responder
from viable_agents.systems.s3.controller import ControlDirective, Controller, RosterEntry
from viable_agents.systems.s3.reports import RunReportNarrative

CONFIG = Path(__file__).resolve().parents[2] / "config"
HIGH = AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1)
LOW = AgentAddress(path=("fleet", "dep_0"), role=Role.S1)
S3_ADDR = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)
_CEILING = Decimal("50")


def _bus() -> Bus:
    topology = load_topology(CONFIG / "topology" / "vsm.yaml")
    bus = Bus(topology=topology, clock=RealClock(), sink=InMemorySink(), run_id=uuid.uuid4())
    bus.register(HIGH)
    bus.register(LOW)
    bus.register(S3_ADDR)
    return bus


def _account(sender: AgentAddress, payload: Payload, *, run_id: uuid.UUID) -> Envelope:
    clock = RealClock()
    return Envelope(
        run_id=run_id,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=sender,
        recipient=S3_ADDR,
        channel=Channel.COMMAND,
        intent=Intent.ACCOUNTABILITY,
        payload=payload,
    )


def _verdict(predicted_class: AnomalyClass = AnomalyClass.REGRESSION) -> TriageVerdict:
    return TriageVerdict(
        repo="o/r",
        run_id=1,
        run_attempt=1,
        predicted_class=predicted_class,
        confidence=0.7,
        rationale="r",
        evidence=(Citation(kind="envelope", ref="x"),),
    )


def _controller(
    bus: Bus,
    *,
    pool_usd: Decimal,
    guard: BudgetGuard,
    sink: InMemoryRunReportRecorder,
    report_every: int = 50,
    roster: list[RosterEntry] | None = None,
    llm: ScriptedLLMClient | None = None,
    model_tier: str | None = None,
) -> Controller:
    default_roster = [
        RosterEntry(address=HIGH, priority=30, cap_usd=Decimal("0.15")),
        RosterEntry(address=LOW, priority=10, cap_usd=Decimal("0.09")),
    ]
    kwargs: dict[str, object] = {
        "address": S3_ADDR,
        "bus": bus,
        "clock": RealClock(),
        "tracer": NullTracer(),
        "roster": roster if roster is not None else default_roster,
        "pool_usd": pool_usd,
        "budget_guard": guard,
        "report_sink": sink,
        "report_every": report_every,
    }
    if llm is not None:
        kwargs["llm"] = llm
        kwargs["model_tier"] = model_tier
    return Controller(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_shed_pauses_the_lowest_priority_running_agent_when_pool_is_spent() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("0.10"), guard=guard, sink=sink)

    guard.record(HIGH, Decimal("0.11"))  # pool (0.10) already exceeded
    await controller.handle(_account(HIGH, _verdict(), run_id=run_id))

    assert LOW.canonical() in controller.paused
    assert HIGH.canonical() not in controller.paused, "the higher-priority agent keeps running"

    delivered = await bus.receive(LOW)
    assert delivered.channel is Channel.COMMAND
    assert delivered.intent is Intent.PAUSE
    assert delivered.payload.narrow(ControlDirective).reason == "budget_exceeded"


@pytest.mark.asyncio
async def test_shed_is_a_pause_not_a_crash() -> None:
    """Exit criterion, directly: no exception, no DEGRADED state -- just a
    normal COMMAND envelope the target agent's own dispatch already handles."""
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("0.01"), guard=guard, sink=sink)

    guard.record(HIGH, Decimal("0.02"))
    await controller.handle(_account(HIGH, _verdict(), run_id=run_id))  # must not raise

    assert controller.state.value != "degraded"


@pytest.mark.asyncio
async def test_resume_when_a_running_agent_becomes_permanently_exhausted() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(
        per_agent_caps_usd={HIGH.canonical(): Decimal("0.15")}, project_ceiling_usd=_CEILING
    )
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("0.10"), guard=guard, sink=sink)

    guard.record(HIGH, Decimal("0.11"))
    await controller.handle(_account(HIGH, _verdict(), run_id=run_id))
    assert LOW.canonical() in controller.paused
    await bus.receive(LOW)  # drain the pause envelope

    guard.record(HIGH, Decimal("0.04"))  # cumulative 0.15 == HIGH's own cap: exhausted
    await controller.handle(_account(HIGH, _verdict(), run_id=run_id))

    assert LOW.canonical() not in controller.paused
    delivered = await bus.receive(LOW)
    assert delivered.intent is Intent.RESUME
    assert delivered.payload.narrow(ControlDirective).reason == "headroom_freed"


@pytest.mark.asyncio
async def test_report_cadence_and_anomaly_counting() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("10"), guard=guard, sink=sink, report_every=2)

    await controller.handle(_account(HIGH, _verdict(AnomalyClass.REGRESSION), run_id=run_id))
    assert sink.records == [], "not yet at the cadence threshold"
    await controller.handle(_account(HIGH, _verdict(AnomalyClass.NONE), run_id=run_id))
    assert len(sink.records) == 1

    report = sink.records[0].report
    breakdown = report.per_agent[HIGH.canonical()]
    assert breakdown.reports_seen == 2
    assert breakdown.anomalies_seen == 1, "only the REGRESSION verdict counts, not NONE"
    assert report.total_anomalies == 1
    assert report.seq == 1


@pytest.mark.asyncio
async def test_flake_and_dep_anomalies_are_counted_by_their_own_shape() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("10"), guard=guard, sink=sink)

    flaky = FlakeAssessment(test_id="t", observed_failure_rate=0.5, sample_size=4, is_flaky=True)
    not_flaky = FlakeAssessment(
        test_id="t2", observed_failure_rate=0.5, sample_size=4, is_flaky=False
    )
    risky_dep = DepSummary(repo="o/r", run_id=1, manifest_files=(), summary="s", risk="high")
    safe_dep = DepSummary(repo="o/r", run_id=2, manifest_files=(), summary="s", risk="low")

    for payload in (flaky, not_flaky, risky_dep, safe_dep):
        await controller.handle(_account(LOW, payload, run_id=run_id))

    report = await controller.emit_report(run_id=run_id)
    assert report.per_agent[LOW.canonical()].reports_seen == 4
    assert report.per_agent[LOW.canonical()].anomalies_seen == 2  # flaky + risky_dep only


@pytest.mark.asyncio
async def test_accountability_from_an_agent_outside_the_roster_is_ignored() -> None:
    bus = _bus()
    stranger = AgentAddress(path=("fleet", "someone_else"), role=Role.S1)
    bus.register(stranger)
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("10"), guard=guard, sink=sink)

    await controller.handle(_account(stranger, _verdict(), run_id=run_id))
    report = await controller.emit_report(run_id=run_id)
    assert stranger.canonical() not in report.per_agent


@pytest.mark.asyncio
async def test_emit_report_without_an_llm_client_has_an_empty_narrative() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    controller = _controller(bus, pool_usd=Decimal("10"), guard=guard, sink=sink)

    report = await controller.emit_report(run_id=run_id)
    assert report.narrative == ""
    assert report.seq == 1


@pytest.mark.asyncio
async def test_emit_report_drafts_a_narrative_via_the_scripted_responder() -> None:
    def responder(output_model, system, messages):
        assert output_model is RunReportNarrative
        return scripted_narrative_responder(output_model, system, messages)

    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(per_agent_caps_usd={}, project_ceiling_usd=_CEILING)
    sink = InMemoryRunReportRecorder()
    llm = ScriptedLLMClient(
        models=load_models(CONFIG / "models.yaml"),
        clock=RealClock(),
        recorder=InMemoryCallRecorder(),
        run_id=run_id,
        responder=responder,
    )
    controller = _controller(
        bus, pool_usd=Decimal("10"), guard=guard, sink=sink, llm=llm, model_tier="sonnet"
    )

    report = await controller.emit_report(run_id=run_id)
    assert report.narrative != ""
    assert "spent" in report.narrative


@pytest.mark.asyncio
async def test_narrative_falls_back_to_empty_string_when_controller_is_over_its_own_budget() -> (
    None
):
    def never_called(output_model, system, messages):
        del output_model, system, messages
        msg = "refused before the responder runs"
        raise AssertionError(msg)

    bus = _bus()
    run_id = uuid.uuid4()
    guard = BudgetGuard(
        per_agent_caps_usd={S3_ADDR.canonical(): Decimal("0")}, project_ceiling_usd=_CEILING
    )
    sink = InMemoryRunReportRecorder()
    llm = ScriptedLLMClient(
        models=load_models(CONFIG / "models.yaml"),
        clock=RealClock(),
        recorder=InMemoryCallRecorder(),
        run_id=run_id,
        responder=never_called,
        budget=guard,
    )
    controller = _controller(
        bus, pool_usd=Decimal("10"), guard=guard, sink=sink, llm=llm, model_tier="sonnet"
    )

    report = await controller.emit_report(run_id=run_id)
    assert report.narrative == ""
