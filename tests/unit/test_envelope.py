"""Envelope round-trip and the payload registry.

The subclass field survival test is the one that catches the SerializeAsAny
regression: an envelope declared ``payload: Payload`` would serialize against the
declared type and silently drop subclass fields on the way to the Postgres JSONB
column, while import and dump both still succeed. The payload subclass is defined
here in the test module on purpose, exactly as a Phase 2 message type would be.
"""

from __future__ import annotations

import uuid

import pytest

from viable_agents.kernel import (
    Channel,
    Envelope,
    Intent,
    Payload,
    PayloadKindError,
    RealClock,
    Role,
    resolve_metasystem,
)
from viable_agents.kernel.address import AgentAddress, RoleAddress

S1 = AgentAddress(path=("fleet", "build_triage_0"), role=Role.S1)
S3 = AgentAddress(path=("fleet", "controller_0"), role=Role.S3)


class TriageVerdict(Payload):
    kind: str = "test.triage_verdict"
    classification: str
    confidence: float


def _env(payload: Payload, *, channel: Channel, intent: Intent) -> Envelope:
    clock = RealClock()
    return Envelope(
        run_id=uuid.uuid4(),
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
        sender=S1,
        recipient=S3,
        channel=channel,
        intent=intent,
        payload=payload,
    )


def test_subclass_payload_fields_survive_json_round_trip() -> None:
    env = _env(
        TriageVerdict(classification="flake", confidence=0.9),
        channel=Channel.COMMAND,
        intent=Intent.ACCOUNTABILITY,
    )
    restored = Envelope.model_validate_json(env.model_dump_json())
    verdict = restored.payload.narrow(TriageVerdict)
    assert verdict.classification == "flake"
    assert verdict.confidence == 0.9


def test_narrow_raises_on_the_wrong_type() -> None:
    class Other(Payload):
        kind: str = "test.other"

    env = _env(Other(), channel=Channel.COMMAND, intent=Intent.ACCOUNTABILITY)
    with pytest.raises(PayloadKindError):
        env.payload.narrow(TriageVerdict)


def test_reply_preserves_the_causal_chain() -> None:
    original = _env(
        TriageVerdict(classification="regression", confidence=0.8),
        channel=Channel.COMMAND,
        intent=Intent.ACCOUNTABILITY,
    )
    clock = RealClock()
    reply = original.reply(
        sender=S3,
        payload=TriageVerdict(classification="ack", confidence=1.0),
        channel=Channel.COMMAND,
        intent=Intent.ALLOCATION,
        ts_wall=clock.wall(),
        ts_sim=clock.now(),
    )
    assert reply.causation_id == original.id
    assert reply.correlation_id == original.correlation_id
    assert reply.recipient == original.sender


def test_algedonic_target_resolves_to_the_metasystem_seat() -> None:
    seat = resolve_metasystem(S1)
    assert seat == RoleAddress(scope=("fleet",), role=Role.S5)
