"""The Phase 1 demo: two stub agents exchange messages over the bus.

Self-verifying, not a screenshot. It runs an S1 (pure code) and an S3 (calling a
scripted, priced LLM client so cost accounting is exercised with no API key),
drives a message both ways on COMMAND, fires the algedonic bypass to the S5 seat,
and provokes a topology violation, then asserts every one of those happened and
exits non-zero if any did not. Rows land in the sink and, with ``--postgres``, in
Postgres; a trace lands in Langfuse when keys are present.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from viable_agents.composition import load_profile
from viable_agents.kernel import (
    Agent,
    AgentAddress,
    Bus,
    Channel,
    DeliveryStatus,
    Envelope,
    Intent,
    MetricRef,
    Payload,
    RealClock,
    Role,
    RoleAddress,
    TopologyViolationError,
)
from viable_agents.llm.client import ScriptedLLMClient
from viable_agents.llm.recorder import InMemoryCallRecorder
from viable_agents.observability import build_tracer
from viable_agents.persistence import InMemorySink

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"

FLEET = ("fleet",)
ENV = AgentAddress(path=("fleet", "env_0"), role=Role.ENVIRONMENT)
S1 = AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1)
S3 = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)
S5 = AgentAddress(path=("fleet", "policy_0"), role=Role.S5)
S3_SEAT = RoleAddress(scope=FLEET, role=Role.S3)
S5_SEAT = RoleAddress(scope=FLEET, role=Role.S5)


class DemoNote(Payload):
    kind: str = "demo.note"
    text: str


class AllocationDecision(BaseModel):
    tokens: int
    rationale: str


def _responder(output_model: type[BaseModel], system: str, messages: Any) -> dict[str, Any]:
    del output_model, system, messages  # fixed script; the request is ignored
    return {"tokens": 500, "rationale": "steady load, standard allocation"}


_STABLE_IDLE_ROUNDS = 3


class BuildTriageStub(Agent):
    """S1, pure code. Triages an observation, reports up, and raises pain once."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.got_allocation = False
        self._raised_pain = False

    def _envelope(
        self,
        *,
        recipient: AgentAddress | RoleAddress,
        channel: Channel,
        intent: Intent,
        payload: Payload,
        causation: Envelope,
        valence: str = "neutral",
        index: MetricRef | None = None,
    ) -> Envelope:
        return Envelope(
            run_id=causation.run_id,
            correlation_id=causation.correlation_id,
            causation_id=causation.id,
            ts_wall=self.clock.wall(),
            ts_sim=self.clock.now(),
            sender=self.address,
            recipient=recipient,
            channel=channel,
            intent=intent,
            payload=payload,
            valence=valence,  # type: ignore[arg-type]
            index=index,
        )

    async def handle(self, env: Envelope) -> None:
        if env.channel is Channel.ENVIRONMENT and env.intent is Intent.OBSERVATION:
            note = env.payload.narrow(DemoNote)
            await self.emit(
                self._envelope(
                    recipient=S3_SEAT,
                    channel=Channel.COMMAND,
                    intent=Intent.ACCOUNTABILITY,
                    payload=DemoNote(text=f"triaged: {note.text}"),
                    causation=env,
                )
            )
            if not self._raised_pain:
                self._raised_pain = True
                await self.emit(
                    self._envelope(
                        recipient=S5_SEAT,
                        channel=Channel.ALGEDONIC,
                        intent=Intent.PAIN,
                        payload=DemoNote(text="flake storm on main"),
                        causation=env,
                        valence="pain",
                        index=MetricRef(
                            name="flake_rate", observed=0.4, limit=0.1, window_seconds=60
                        ),
                    )
                )
        elif env.channel is Channel.COMMAND and env.intent is Intent.ALLOCATION:
            self.got_allocation = True


class ControllerStub(Agent):
    """S3, LLM. On an accountability report, allocates budget back down."""

    async def handle(self, env: Envelope) -> None:
        if env.channel is Channel.COMMAND and env.intent is Intent.ACCOUNTABILITY:
            if self.llm is None or self.model_tier is None:
                msg = "ControllerStub needs an LLM client and a model tier"
                raise RuntimeError(msg)
            result = await self.llm.complete(
                tier=self.model_tier,
                system="Allocate a token budget for the reporting worker.",
                messages=[{"role": "user", "content": env.payload.narrow(DemoNote).text}],
                output_model=AllocationDecision,
                max_tokens=256,
                turn_id=uuid.uuid4(),
                agent=self.address,
            )
            await self.emit(
                env.reply(
                    sender=self.address,
                    payload=DemoNote(text=f"allocated {result.parsed.tokens} tokens"),
                    channel=Channel.COMMAND,
                    intent=Intent.ALLOCATION,
                    ts_wall=self.clock.wall(),
                    ts_sim=self.clock.now(),
                )
            )


class PassiveStub(Agent):
    """A receiver with an inbox (the S5 seat), so the algedonic target resolves."""

    async def handle(self, env: Envelope) -> None:  # noqa: ARG002 - interface method
        return None


@dataclass
class DemoResult:
    checks: dict[str, bool] = field(default_factory=dict)
    llm_cost: Decimal = Decimal("0")
    trace_url: str | None = None

    @property
    def ok(self) -> bool:
        return all(self.checks.values())


async def _drain_when_idle(bus: Bus, addresses: list[AgentAddress]) -> None:
    stable = 0
    for _ in range(500):
        await asyncio.sleep(0.005)
        if all(bus.inbox_depth(a) == 0 for a in addresses):
            stable += 1
            if stable >= _STABLE_IDLE_ROUNDS:
                return
        else:
            stable = 0


async def _run(*, run_id: uuid.UUID) -> DemoResult:
    config = load_profile(CONFIG_DIR, "full-vsm")
    clock = RealClock()
    sink = InMemorySink()
    recorder = InMemoryCallRecorder()
    tracer = build_tracer()
    llm = ScriptedLLMClient(
        models=config.models,
        clock=clock,
        recorder=recorder,
        run_id=run_id,
        responder=_responder,
    )
    bus = Bus(topology=config.topology, clock=clock, sink=sink, run_id=run_id)
    for addr in (S1, S3, S5):
        bus.register(addr)

    s1 = BuildTriageStub(address=S1, bus=bus, clock=clock, tracer=tracer)
    s3 = ControllerStub(
        address=S3, bus=bus, clock=clock, tracer=tracer, llm=llm, model_tier="sonnet"
    )
    s5 = PassiveStub(address=S5, bus=bus, clock=clock, tracer=tracer)
    for agent in (s1, s3, s5):
        agent.start()

    seed = Envelope(
        run_id=run_id,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=ENV,
        recipient=S1,
        channel=Channel.ENVIRONMENT,
        intent=Intent.OBSERVATION,
        payload=DemoNote(text="workflow run 42 failed"),
    )
    await bus.send(seed)
    await _drain_when_idle(bus, [S1, S3, S5])

    # Provoke a topology violation: an S1 may not command another S1.
    violation_rule: str | None = None
    illegal = Envelope(
        run_id=run_id,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=S1,
        recipient=S1,
        channel=Channel.COMMAND,
        intent=Intent.INTERVENTION,
        payload=DemoNote(text="do as I say"),
    )
    try:
        await bus.send(illegal)
    except TopologyViolationError as exc:
        violation_rule = exc.rule_id

    for agent in (s1, s3, s5):
        await agent.stop()
    tracer.flush()

    delivered = {(r.channel, r.intent) for r in sink.delivered()}
    rejected = sink.by_status(DeliveryStatus.REJECTED)
    return DemoResult(
        checks={
            "env observation delivered to S1": (Channel.ENVIRONMENT, "observation") in delivered,
            "S1 reported accountability up to S3": (Channel.COMMAND, "accountability") in delivered,
            "S3 allocated budget down to S1": (Channel.COMMAND, "allocation") in delivered,
            "S1 received the allocation": s1.got_allocation,
            "algedonic pain reached the S5 seat": (Channel.ALGEDONIC, "pain") in delivered,
            "S1->S1 COMMAND raised a violation": violation_rule == "default_deny",
            "the violation was recorded, not just raised": any(
                r.rule_id == "default_deny" for r in rejected
            ),
            "an LLM call was cost-accounted": len(recorder.calls) >= 1,
            "the recorded cost is positive": recorder.total_cost_usd() > 0,
        },
        llm_cost=recorder.total_cost_usd(),
    )


def run_demo(*, verify: bool) -> int:
    run_id = uuid.uuid4()
    result = asyncio.run(_run(run_id=run_id))
    print(f"viable-agents demo  (run {run_id})")  # noqa: T201
    for label, passed in result.checks.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")  # noqa: T201
    print(f"  LLM cost this run: ${result.llm_cost}")  # noqa: T201
    if result.trace_url:
        print(f"  Langfuse trace: {result.trace_url}")  # noqa: T201
    if verify and not result.ok:
        print("VERIFY FAILED")  # noqa: T201
        return 1
    print("VERIFY OK" if verify else "done")  # noqa: T201
    return 0
