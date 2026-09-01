"""The bus enforces the topology, records every attempt, and honours a swapped one.

CLAUDE.md hard rule 1 says the topology is "enforced". Testing the Topology object
alone would pass even if the bus hardcoded the VSM rules in Python, so these tests
drive ``Bus.send()`` and prove it follows whichever topology it was given.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from viable_agents.composition import load_topology
from viable_agents.kernel import (
    Bus,
    Channel,
    ChannelSaturatedError,
    DeliveryStatus,
    Envelope,
    Intent,
    Payload,
    RealClock,
    Role,
    RoleAddress,
    Topology,
    TopologyViolationError,
)
from viable_agents.kernel.address import AgentAddress
from viable_agents.persistence import InMemorySink

CONFIG = Path(__file__).resolve().parents[2] / "config" / "topology"

S1 = AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1)
S1B = AgentAddress(path=("fleet", "build_triage_1"), role=Role.S1)
S3 = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)
S5 = AgentAddress(path=("fleet", "policy_0"), role=Role.S5)
DISPATCH = AgentAddress(path=("fleet", "dispatcher_0"), role=Role.DISPATCHER)
WORKER = AgentAddress(path=("fleet", "worker_0"), role=Role.WORKER)


class Note(Payload):
    kind: str = "test.note"
    text: str = "x"


def _env(sender: AgentAddress, recipient: object, channel: Channel, intent: Intent) -> Envelope:
    clock = RealClock()
    return Envelope(
        run_id=uuid.uuid4(),
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=sender,
        recipient=recipient,  # type: ignore[arg-type]
        channel=channel,
        intent=intent,
        payload=Note(),
    )


def _bus(topology_name: str, sink: InMemorySink) -> Bus:
    topology = load_topology(CONFIG / f"{topology_name}.yaml")
    return Bus(topology=topology, clock=RealClock(), sink=sink, run_id=uuid.uuid4())


def _mini(*, overflow: str, maxsize: int, block_timeout: float = 0.05) -> Topology:
    return Topology.model_validate(
        {
            "name": "mini",
            "default_effect": "deny",
            "channels": {
                "command": {
                    "enabled": True,
                    "maxsize": maxsize,
                    "overflow": overflow,
                    "block_timeout_seconds": block_timeout,
                    "allowed_intents": ["allocation"],
                }
            },
            "rules": [
                {
                    "id": "deny_all",
                    "sender": "*",
                    "recipient": "*",
                    "channel": "*",
                    "effect": "deny",
                },
                {
                    "id": "allow_alloc",
                    "sender": "s3",
                    "recipient": "s1",
                    "channel": "command",
                    "intent": "allocation",
                    "effect": "allow",
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_allowed_send_is_delivered() -> None:
    sink = InMemorySink()
    bus = _bus("vsm", sink)
    bus.register(S1)
    await bus.send(_env(S3, S1, Channel.COMMAND, Intent.ALLOCATION))
    assert bus.inbox_depth(S1) == 1
    assert (Channel.COMMAND, "allocation") in {(r.channel, r.intent) for r in sink.delivered()}


@pytest.mark.asyncio
async def test_forbidden_send_raises_and_is_recorded_with_the_rule() -> None:
    sink = InMemorySink()
    bus = _bus("vsm", sink)
    bus.register(S1)
    with pytest.raises(TopologyViolationError) as exc:
        await bus.send(_env(S1, S1B, Channel.COMMAND, Intent.INTERVENTION))
    assert exc.value.rule_id == "default_deny"
    rejected = sink.by_status(DeliveryStatus.REJECTED)
    assert rejected
    assert rejected[-1].rule_id == "default_deny"


@pytest.mark.asyncio
async def test_bus_follows_the_injected_topology_not_hardcoded_rules() -> None:
    # A worker-to-dispatcher accountability send is allowed under flat, denied
    # under VSM. Same class, same call, different injected data.
    flat = _bus("flat", InMemorySink())
    flat.register(DISPATCH)
    await flat.send(_env(WORKER, DISPATCH, Channel.COMMAND, Intent.ACCOUNTABILITY))
    assert flat.inbox_depth(DISPATCH) == 1

    vsm = _bus("vsm", InMemorySink())
    vsm.register(DISPATCH)
    with pytest.raises(TopologyViolationError):
        await vsm.send(_env(WORKER, DISPATCH, Channel.COMMAND, Intent.ACCOUNTABILITY))


@pytest.mark.asyncio
async def test_algedonic_reaches_the_s5_seat() -> None:
    sink = InMemorySink()
    bus = _bus("vsm", sink)
    bus.register(S5)
    await bus.send(
        _env(S1, RoleAddress(scope=("fleet",), role=Role.S5), Channel.ALGEDONIC, Intent.PAIN)
    )
    assert bus.inbox_depth(S5) == 1


@pytest.mark.asyncio
async def test_fifo_ordering_is_a_guarantee() -> None:
    sink = InMemorySink()
    bus = _bus("vsm", sink)
    bus.register(S1)
    first = _env(S3, S1, Channel.COMMAND, Intent.ALLOCATION)
    second = _env(S3, S1, Channel.COMMAND, Intent.ALLOCATION)
    await bus.send(first)
    await bus.send(second)
    assert (await bus.receive(S1)).id == first.id
    assert (await bus.receive(S1)).id == second.id


@pytest.mark.asyncio
async def test_reject_overflow_raises_channel_saturated() -> None:
    sink = InMemorySink()
    topology = _mini(overflow="reject", maxsize=1)
    bus = Bus(topology=topology, clock=RealClock(), sink=sink, run_id=uuid.uuid4())
    bus.register(S1, maxsize=1)
    await bus.send(_env(S3, S1, Channel.COMMAND, Intent.ALLOCATION))
    with pytest.raises(ChannelSaturatedError):
        await bus.send(_env(S3, S1, Channel.COMMAND, Intent.ALLOCATION))


@pytest.mark.asyncio
async def test_drop_oldest_evicts_and_records() -> None:
    sink = InMemorySink()
    topology = _mini(overflow="drop_oldest", maxsize=1)
    bus = Bus(topology=topology, clock=RealClock(), sink=sink, run_id=uuid.uuid4())
    bus.register(S1, maxsize=1)
    await bus.send(_env(S3, S1, Channel.COMMAND, Intent.ALLOCATION))
    await bus.send(_env(S3, S1, Channel.COMMAND, Intent.ALLOCATION))
    assert bus.inbox_depth(S1) == 1
    assert any(r.status is DeliveryStatus.DROPPED for r in sink.sends)


@pytest.mark.asyncio
async def test_drain_reports_success_on_empty() -> None:
    bus = _bus("vsm", InMemorySink())
    bus.register(S1)
    assert await bus.drain([[S1]], timeout=1.0) is True
