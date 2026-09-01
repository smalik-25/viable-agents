"""The Phase 2 CI run: three S1 agents over a live or synthetic event stream.

``viable-agents run --source synthetic --events 500`` is Phase 2's version of the
Phase 1 demo: self-verifying, not a screenshot. It streams ``CIEvent``s to the S1
role seat, which the bus fans out to every registered S1 worker at once
(``config/topology/vsm.yaml``'s ``env_in_s1`` rule, a ``RoleAddress`` recipient),
waits for the fleet to drain, and prints triage verdicts, flake assessments, and
dependency summaries.

Classification is free and deterministic by default (``ScriptedLLMClient`` plus
the keyword responders in ``systems/s1/scripting.py``, which see the same JSON
context a real call would); ``--live-llm`` swaps in the real Anthropic client,
which needs ``ANTHROPIC_API_KEY`` and spends against the $50 project ceiling.
``--source live`` swaps the synthetic generator for the read-only GitHub adapter,
which needs ``GITHUB_TOKEN``. Nothing below this module's source-construction
branch can tell live from synthetic apart (CLAUDE.md hard rule 4): both satisfy
``CIEventSource`` and the fleet only ever sees a ``CIEvent``.

The fleet roster is read from ``config/fleet/vsm.yaml``, not hardcoded: adding a
fourth S1 worker type means adding it to ``_AGENT_TYPES`` and to that file, not
editing this run loop.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from viable_agents.composition import load_profile
from viable_agents.kernel import (
    AgentState,
    Bus,
    Channel,
    Envelope,
    Intent,
    LLMClient,
    RealClock,
    Role,
)
from viable_agents.kernel.address import AgentAddress, RoleAddress
from viable_agents.llm.client import ScriptedLLMClient
from viable_agents.llm.recorder import CallRecorder, InMemoryCallRecorder
from viable_agents.observability import build_tracer
from viable_agents.persistence import (
    InMemorySink,
    PostgresSink,
    RunRow,
    make_engine,
    make_session_factory,
)
from viable_agents.settings import load_settings
from viable_agents.simulator import SyntheticSource, load_scenario
from viable_agents.sources import (
    CIEventSource,
    GitHubSource,
    InMemoryGitHubCache,
    PostgresGitHubCache,
    load_watchlist,
)
from viable_agents.systems.s1 import (
    BuildTriageAgent,
    DepAgent,
    DepDraft,
    FlakeAgent,
    S1Worker,
    TriageDecision,
    scripted_dep_responder,
    scripted_triage_responder,
)
from viable_agents.systems.s2 import Coordinator

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
FLEET = ("fleet",)
ENV_ADDR = AgentAddress(path=("fleet", "env_0"), role=Role.ENVIRONMENT)

FleetAgent = BuildTriageAgent | FlakeAgent | DepAgent | Coordinator

_AGENT_TYPES: dict[str, type[FleetAgent]] = {
    "BuildTriageAgent": BuildTriageAgent,
    "FlakeAgent": FlakeAgent,
    "DepAgent": DepAgent,
    "Coordinator": Coordinator,
}

_STABLE_IDLE_ROUNDS = 3
_DRAIN_ROUNDS = 20_000
_MIN_AGREEMENT = 0.70


def _scripted_responder(output_model: type[Any], system: str, messages: Any) -> dict[str, Any]:
    if output_model is TriageDecision:
        return scripted_triage_responder(output_model, system, messages)
    if output_model is DepDraft:
        return scripted_dep_responder(output_model, system, messages)
    msg = f"no scripted responder registered for {output_model.__name__}"
    raise NotImplementedError(msg)


@dataclass
class RunResult:
    events_seen: int = 0
    triage_verdicts: int = 0
    flake_assessments: int = 0
    dep_summaries: int = 0
    agreement: float | None = None  # synthetic only: fraction matching ground truth
    llm_cost: Decimal = Decimal("0")
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())


async def _drain(bus: Bus, addresses: list[AgentAddress]) -> None:
    stable = 0
    for _ in range(_DRAIN_ROUNDS):
        await asyncio.sleep(0.001)
        if all(bus.inbox_depth(a) == 0 for a in addresses):
            stable += 1
            if stable >= _STABLE_IDLE_ROUNDS:
                return
        else:
            stable = 0


def _build_llm(
    *, live_llm: bool, models: Any, clock: RealClock, recorder: CallRecorder, run_id: uuid.UUID
) -> LLMClient:
    if not live_llm:
        return ScriptedLLMClient(
            models=models,
            clock=clock,
            recorder=recorder,
            run_id=run_id,
            responder=_scripted_responder,
        )
    from anthropic import AsyncAnthropic  # noqa: PLC0415 - lazy: only import when --live-llm

    from viable_agents.llm.anthropic_client import AnthropicLLMClient  # noqa: PLC0415

    settings = load_settings()
    if not settings.anthropic_api_key:
        msg = "ANTHROPIC_API_KEY is required for --live-llm"
        raise RuntimeError(msg)
    return AnthropicLLMClient(
        client=AsyncAnthropic(api_key=settings.anthropic_api_key),
        models=models,
        clock=clock,
        recorder=recorder,
        run_id=run_id,
    )


async def _open_sink(
    *, use_postgres: bool, run_id: uuid.UUID, events: int, source_mode: str, config: Any
) -> tuple[Any, Any]:
    """Returns (sink, session_factory). session_factory is None for the in-memory path."""
    if not use_postgres:
        return InMemorySink(), None
    settings = load_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        session.add(
            RunRow(
                run_id=run_id,
                seed=events,
                source_mode=source_mode,
                fleet_config_name=config.fleet.name,
                topology_config_name=config.topology.name,
                config_fingerprint=config.config_fingerprint,
                status="running",
            )
        )
        await session.commit()
    return PostgresSink(session_factory=session_factory, run_id=run_id), session_factory


def _build_agents(
    config: Any, *, bus: Bus, clock: RealClock, tracer: Any, llm: LLMClient
) -> tuple[list[FleetAgent], list[AgentAddress]]:
    specs = [a for a in config.fleet.agents if a.agent_type in _AGENT_TYPES]
    addresses = [AgentAddress(path=spec.path, role=spec.role) for spec in specs]
    for addr in addresses:
        bus.register(addr)

    agents: list[FleetAgent] = []
    for spec, addr in zip(specs, addresses, strict=True):
        cls = _AGENT_TYPES[spec.agent_type]
        tier = None if spec.model_tier == "none" else spec.model_tier
        kwargs: dict[str, Any] = {
            "address": addr,
            "bus": bus,
            "clock": clock,
            "tracer": tracer,
        }
        if issubclass(cls, S1Worker):
            kwargs["fleet_scope"] = FLEET
        if tier is not None:
            kwargs["llm"] = llm
            kwargs["model_tier"] = tier
        agent = cls(**kwargs)
        agents.append(agent)
        agent.start()
    return agents, addresses


def _build_source(
    *, source_mode: str, events: int, scenario_name: str, session_factory: Any
) -> CIEventSource:
    if source_mode == "synthetic":
        scenario = load_scenario(CONFIG_DIR / "simulator" / f"{scenario_name}.yaml")
        scenario = scenario.model_copy(update={"event_count": events})
        return SyntheticSource(scenario)

    settings = load_settings()
    if not settings.github_token:
        msg = "GITHUB_TOKEN is required for --source live"
        raise RuntimeError(msg)
    watchlist = load_watchlist(CONFIG_DIR / "sources" / "watchlist.yaml")
    cache = (
        PostgresGitHubCache(session_factory=session_factory)
        if session_factory is not None
        else InMemoryGitHubCache()
    )
    return GitHubSource(watchlist=watchlist, token=settings.github_token, cache=cache)


async def _stream_into(
    source: CIEventSource, *, bus: Bus, run_id: uuid.UUID, clock: RealClock
) -> int:
    s1_seat = RoleAddress(scope=FLEET, role=Role.S1)
    seen = 0
    async for event in source.stream():
        await bus.send(
            Envelope(
                run_id=run_id,
                ts_wall=clock.wall(),
                ts_sim=clock.now(),
                sender=ENV_ADDR,
                recipient=s1_seat,
                channel=Channel.ENVIRONMENT,
                intent=Intent.OBSERVATION,
                payload=event,
            )
        )
        seen += 1
    return seen


async def _run(
    *,
    run_id: uuid.UUID,
    source_mode: str,
    events: int,
    scenario_name: str,
    live_llm: bool,
    use_postgres: bool,
) -> RunResult:
    config = load_profile(CONFIG_DIR, "full-vsm")
    clock = RealClock()
    tracer = build_tracer()
    recorder: CallRecorder = InMemoryCallRecorder()

    sink, session_factory = await _open_sink(
        use_postgres=use_postgres,
        run_id=run_id,
        events=events,
        source_mode=source_mode,
        config=config,
    )
    llm = _build_llm(
        live_llm=live_llm, models=config.models, clock=clock, recorder=recorder, run_id=run_id
    )
    bus = Bus(topology=config.topology, clock=clock, sink=sink, run_id=run_id)
    agents, addresses = _build_agents(config, bus=bus, clock=clock, tracer=tracer, llm=llm)
    source = _build_source(
        source_mode=source_mode,
        events=events,
        scenario_name=scenario_name,
        session_factory=session_factory,
    )

    seen = await _stream_into(source, bus=bus, run_id=run_id, clock=clock)

    await _drain(bus, addresses)
    for agent in agents:
        await agent.stop()
    aclose = getattr(source, "aclose", None)
    if aclose is not None:
        await aclose()
    tracer.flush()

    return _summarize(agents, source, source_mode=source_mode, seen=seen, recorder=recorder)


def _summarize(
    agents: list[FleetAgent],
    source: CIEventSource,
    *,
    source_mode: str,
    seen: int,
    recorder: CallRecorder,
) -> RunResult:
    triage_agents = [a for a in agents if isinstance(a, BuildTriageAgent)]
    flake_agents = [a for a in agents if isinstance(a, FlakeAgent)]
    dep_agents = [a for a in agents if isinstance(a, DepAgent)]
    verdict_count = sum(len(a.verdicts) for a in triage_agents)
    flake_count = sum(len(a.assessments) for a in flake_agents)
    dep_count = sum(len(a.summaries) for a in dep_agents)

    agreement = None
    if source_mode == "synthetic" and isinstance(source, SyntheticSource) and triage_agents:
        labels = source.labels()
        total = agree = 0
        for a in triage_agents:
            for v in a.verdicts:
                label = labels.get((v.repo, v.run_id, v.run_attempt))
                if label is None:
                    continue
                total += 1
                if v.predicted_class == label.anomaly_class:
                    agree += 1
        agreement = (agree / total) if total else None

    degraded = [a.address.canonical() for a in agents if a.state is AgentState.DEGRADED]

    checks = {
        "the stream produced at least one event": seen > 0,
        "no S1 agent degraded": not degraded,
    }
    if source_mode == "synthetic":
        checks["triage verdicts were produced for the failing events"] = verdict_count > 0
        checks["flaky tests configured in the scenario were detected"] = flake_count > 0
        if agreement is not None:
            checks[f"triage agreement with ground truth is at least {_MIN_AGREEMENT:.0%}"] = (
                agreement >= _MIN_AGREEMENT
            )

    total_cost = (
        recorder.total_cost_usd() if isinstance(recorder, InMemoryCallRecorder) else Decimal("0")
    )
    return RunResult(
        events_seen=seen,
        triage_verdicts=verdict_count,
        flake_assessments=flake_count,
        dep_summaries=dep_count,
        agreement=agreement,
        llm_cost=total_cost,
        checks=checks,
    )


def run_fleet(
    *,
    source: str,
    events: int,
    scenario: str,
    live_llm: bool,
    postgres: bool,
    verify: bool,
) -> int:
    run_id = uuid.uuid4()
    result = asyncio.run(
        _run(
            run_id=run_id,
            source_mode=source,
            events=events,
            scenario_name=scenario,
            live_llm=live_llm,
            use_postgres=postgres,
        )
    )
    print(f"viable-agents run  (run {run_id}, source={source})")  # noqa: T201
    print(f"  events seen: {result.events_seen}")  # noqa: T201
    print(f"  triage verdicts: {result.triage_verdicts}")  # noqa: T201
    print(f"  flake assessments: {result.flake_assessments}")  # noqa: T201
    print(f"  dep summaries: {result.dep_summaries}")  # noqa: T201
    if result.agreement is not None:
        print(f"  triage agreement vs ground truth: {result.agreement:.0%}")  # noqa: T201
    print(f"  LLM cost this run: ${result.llm_cost}")  # noqa: T201
    for label, passed in result.checks.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")  # noqa: T201
    if verify and not result.ok:
        print("VERIFY FAILED")  # noqa: T201
        return 1
    print("VERIFY OK" if verify else "done")  # noqa: T201
    return 0
