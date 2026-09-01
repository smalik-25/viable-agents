"""S2 anti-oscillation: the work-claim ledger, proven by a thrash test written
before ``Coordinator`` existed to fix it (CLAUDE.md hard rule 8).

PLAN Phase 3's concrete conflict: two ``BuildTriageAgent`` instances can
legitimately exist side by side (horizontal scale-out for throughput), and
``config/topology/vsm.yaml``'s ``env_in_s1`` rule fans an ENVIRONMENT observation
out to every agent registered under the S1 role by design -- so both instances
seeing the same CI event is not a bug. Both of them independently classifying it
and reporting a verdict to S3 is: two accountability envelopes land for one
workflow run, which is exactly the duplicate work this phase exists to remove.

``docs/ARCHITECTURE.md`` warns that this test "cannot tell 'S2 damped it' from
'the S1s sorted it out themselves.'" Every test below routes the S1 side's claim
through a real ``Coordinator.handle()`` call and inspects its ``decisions`` and
``ledger``-derived state directly, so a pass here is never attributable to
anything except the coordinator actually mediating.
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
from viable_agents.llm.client import ScriptedLLMClient
from viable_agents.llm.config import load_models
from viable_agents.llm.recorder import InMemoryCallRecorder
from viable_agents.persistence import InMemorySink
from viable_agents.sources.events import (
    CIEvent,
    Conclusion,
    EventKind,
    LogExcerpt,
    SourceMode,
)
from viable_agents.systems.s1 import BuildTriageAgent, TriageDecision, scripted_triage_responder
from viable_agents.systems.s2 import Coordinator, WorkClaimLedger

CONFIG = Path(__file__).resolve().parents[2] / "config"
FLEET = ("fleet",)
S1A_ADDR = AgentAddress(path=("fleet", "build_triage_a"), role=Role.S1)
S1B_ADDR = AgentAddress(path=("fleet", "build_triage_b"), role=Role.S1)
S2_ADDR = AgentAddress(path=("fleet", "coordinator_0"), role=Role.S2)
S3_ADDR = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)
ENV_ADDR = AgentAddress(path=("fleet", "env_0"), role=Role.ENVIRONMENT)
_NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _bus() -> Bus:
    topology = load_topology(CONFIG / "topology" / "vsm.yaml")
    bus = Bus(topology=topology, clock=RealClock(), sink=InMemorySink(), run_id=uuid.uuid4())
    bus.register(S1A_ADDR)
    bus.register(S1B_ADDR)
    bus.register(S2_ADDR)
    bus.register(S3_ADDR)
    return bus


def _failing_event(**overrides: object) -> CIEvent:
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


def _observation(event: CIEvent, *, run_id: uuid.UUID, recipient: AgentAddress) -> Envelope:
    clock = RealClock()
    return Envelope(
        run_id=run_id,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=ENV_ADDR,
        recipient=recipient,
        channel=Channel.ENVIRONMENT,
        intent=Intent.OBSERVATION,
        payload=event,
    )


def _scripted_responder(output_model: type[Any], system: str, messages: Any) -> dict[str, Any]:
    if output_model is TriageDecision:
        return scripted_triage_responder(output_model, system, messages)
    raise AssertionError(output_model)


def _llm(run_id: uuid.UUID) -> ScriptedLLMClient:
    models = load_models(CONFIG / "models.yaml")
    return ScriptedLLMClient(
        models=models,
        clock=RealClock(),
        recorder=InMemoryCallRecorder(),
        run_id=run_id,
        responder=_scripted_responder,
    )


def _triage_agent(
    address: AgentAddress, bus: Bus, run_id: uuid.UUID, *, coordinate: bool
) -> BuildTriageAgent:
    return BuildTriageAgent(
        address=address,
        bus=bus,
        clock=RealClock(),
        tracer=NullTracer(),
        llm=_llm(run_id),
        model_tier="haiku",
        fleet_scope=FLEET,
        coordinate=coordinate,
    )


def _coordinator(bus: Bus) -> Coordinator:
    return Coordinator(address=S2_ADDR, bus=bus, clock=RealClock(), tracer=NullTracer())


@pytest.mark.asyncio
async def test_two_build_triage_agents_thrash_without_coordination() -> None:
    """The bug this phase fixes: with ``coordinate=False`` -- Phase 2's original,
    unmediated behavior -- both instances classify and report the same run."""
    bus = _bus()
    run_id = uuid.uuid4()
    event = _failing_event()
    agent_a = _triage_agent(S1A_ADDR, bus, run_id, coordinate=False)
    agent_b = _triage_agent(S1B_ADDR, bus, run_id, coordinate=False)

    await agent_a.handle(_observation(event, run_id=run_id, recipient=S1A_ADDR))
    await agent_b.handle(_observation(event, run_id=run_id, recipient=S1B_ADDR))

    assert len(agent_a.verdicts) == 1
    assert len(agent_b.verdicts) == 1
    assert bus.inbox_depth(S3_ADDR) == 2, "both instances reported the same run: the thrash"


@pytest.mark.asyncio
async def test_coordinator_grants_exactly_one_claim_per_run() -> None:
    bus = _bus()
    run_id = uuid.uuid4()
    event = _failing_event()
    agent_a = _triage_agent(S1A_ADDR, bus, run_id, coordinate=True)
    agent_b = _triage_agent(S1B_ADDR, bus, run_id, coordinate=True)
    coordinator = _coordinator(bus)

    await agent_a.handle(_observation(event, run_id=run_id, recipient=S1A_ADDR))
    await agent_b.handle(_observation(event, run_id=run_id, recipient=S1B_ADDR))
    assert bus.inbox_depth(S2_ADDR) == 2, "both claims reached S2, unresolved so far"
    assert agent_a.verdicts == []
    assert agent_b.verdicts == []

    await coordinator.handle(await bus.receive(S2_ADDR))
    await coordinator.handle(await bus.receive(S2_ADDR))
    assert [d.granted for d in coordinator.decisions] == [True, False]

    await agent_a.handle(await bus.receive(S1A_ADDR))
    await agent_b.handle(await bus.receive(S1B_ADDR))

    assert len(agent_a.verdicts) == 1
    assert agent_b.verdicts == [], "the denied claimant never classified or reported"
    assert bus.inbox_depth(S3_ADDR) == 1, "S2 damped it: exactly one report reaches S3"
    assert bus.inbox_depth(S2_ADDR) == 1, "the winner released its claim"


@pytest.mark.asyncio
async def test_duplicate_claim_rate_is_zero_across_a_thousand_runs() -> None:
    """Phase 3 exit criterion, literally: 1,000 distinct workflow runs, two
    competing BuildTriageAgents on every one, S2 wired in -- no run is ever
    granted to more than one claimant."""
    bus = _bus()
    run_id = uuid.uuid4()
    agent_a = _triage_agent(S1A_ADDR, bus, run_id, coordinate=True)
    agent_b = _triage_agent(S1B_ADDR, bus, run_id, coordinate=True)
    coordinator = _coordinator(bus)

    total = 1_000
    for i in range(total):
        event = _failing_event(run_id=i)
        await agent_a.handle(_observation(event, run_id=run_id, recipient=S1A_ADDR))
        await agent_b.handle(_observation(event, run_id=run_id, recipient=S1B_ADDR))
        await coordinator.handle(await bus.receive(S2_ADDR))
        await coordinator.handle(await bus.receive(S2_ADDR))
        await agent_a.handle(await bus.receive(S1A_ADDR))
        await agent_b.handle(await bus.receive(S1B_ADDR))
        # The winner releases its claim once it reports; drain it, or the
        # coordination channel's bounded, blocking queue (maxsize 256) saturates
        # partway through the stream and every later release blocks for
        # ``block_timeout_seconds`` before raising ``ChannelSaturatedError``.
        await coordinator.handle(await bus.receive(S2_ADDR))

    granted = [d for d in coordinator.decisions if d.granted]
    owners_per_key: dict[tuple[Any, str], set[str]] = {}
    for d in granted:
        owners_per_key.setdefault((d.key, d.work_type), set()).add(d.owner)
    duplicate_keys = [key for key, owners in owners_per_key.items() if len(owners) > 1]

    assert not duplicate_keys, "duplicate-claim rate must be 0 across the stream"
    assert len(granted) == total, "exactly one grant per run"
    assert bus.inbox_depth(S3_ADDR) == total, "exactly one report per run reaches S3"


def test_ledger_reclaims_a_stale_claim_after_ttl() -> None:
    ledger = WorkClaimLedger(ttl_seconds=60.0)
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    first = ledger.claim(("o/r", 1, 1), "triage", claimant="a", now=t0)
    assert first.granted

    still_fresh = ledger.claim(
        ("o/r", 1, 1), "triage", claimant="b", now=t0 + dt.timedelta(seconds=30)
    )
    assert not still_fresh.granted
    assert still_fresh.owner == "a"

    reclaimed = ledger.claim(
        ("o/r", 1, 1), "triage", claimant="b", now=t0 + dt.timedelta(seconds=61)
    )
    assert reclaimed.granted
    assert reclaimed.owner == "b"


def test_ledger_release_frees_the_claim_for_a_new_owner() -> None:
    ledger = WorkClaimLedger()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    ledger.claim(("o/r", 1, 1), "triage", claimant="a", now=t0)

    assert ledger.release(("o/r", 1, 1), "triage", claimant="a")

    again = ledger.claim(("o/r", 1, 1), "triage", claimant="b", now=t0)
    assert again.granted
    assert again.owner == "b"


def test_ledger_release_by_a_non_owner_is_a_noop() -> None:
    ledger = WorkClaimLedger()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    ledger.claim(("o/r", 1, 1), "triage", claimant="a", now=t0)

    assert not ledger.release(("o/r", 1, 1), "triage", claimant="b")

    still_owned = ledger.claim(("o/r", 1, 1), "triage", claimant="c", now=t0)
    assert not still_owned.granted
    assert still_owned.owner == "a"


def test_ledger_does_not_contend_across_different_work_types() -> None:
    """A BuildTriageAgent and a FlakeAgent claiming the same run key are not
    duplicating each other's work, so they must not contend for one ledger slot."""
    ledger = WorkClaimLedger()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    triage = ledger.claim(("o/r", 1, 1), "triage", claimant="build_triage_0", now=t0)
    flake = ledger.claim(("o/r", 1, 1), "flake", claimant="flake_0", now=t0)
    assert triage.granted
    assert flake.granted
