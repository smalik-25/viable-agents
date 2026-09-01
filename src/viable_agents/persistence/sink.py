"""Sink implementations: the bus records every send here.

``InMemorySink`` is the default for the demo and the unit tests, so the whole
kernel exercises end to end without a database. ``PostgresSink`` writes the same
records to the tables of record. Both assign a per-run monotonic ``seq`` so replay
ordering is deterministic rather than dependent on a database sequence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.bus import DeliveryStatus
from viable_agents.kernel.channels import Channel
from viable_agents.kernel.envelope import Envelope
from viable_agents.persistence.models import ChannelSaturationRow, MessageRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _recipient_level(env: Envelope) -> int:
    recipient = env.recipient
    if isinstance(recipient, AgentAddress):
        return recipient.level
    return len(recipient.scope) - 1


def envelope_to_row(
    env: Envelope,
    *,
    seq: int,
    status: DeliveryStatus,
    reason: str | None,
    rule_id: str | None,
) -> MessageRow:
    return MessageRow(
        envelope_id=env.id,
        run_id=env.run_id,
        seq=seq,
        schema_version=env.schema_version,
        ts_wall=env.ts_wall,
        ts_sim=env.ts_sim,
        sender_path=env.sender.canonical(),
        sender_role=env.sender.role.value,
        sender_level=env.sender.level,
        recipient_requested=str(env.recipient),
        recipient_role=env.recipient.role.value,
        recipient_level=_recipient_level(env),
        channel=env.channel.value,
        intent=env.intent.value,
        payload_kind=env.payload.kind,
        payload=env.payload.model_dump(mode="json"),
        status=status.value,
        reject_reason=reason,
        rule_id=rule_id,
        valence=env.valence,
        correlation_id=env.correlation_id,
        causation_id=env.causation_id,
        fanout_id=env.fanout_id,
        turn_id=env.turn_id,
        trace_id=env.trace_id,
        charter_version=env.charter_version,
    )


@dataclass(frozen=True, slots=True)
class SendRecord:
    envelope_id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    sender: str
    recipient_role: str
    channel: Channel
    intent: str
    status: DeliveryStatus
    reason: str | None
    rule_id: str | None


class InMemorySink:
    """Keeps records in lists so tests can assert on them without a database."""

    def __init__(self) -> None:
        self.sends: list[SendRecord] = []
        self.saturations: list[tuple[Channel, float, int]] = []
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def record_send(
        self,
        env: Envelope,
        status: DeliveryStatus,
        reason: str | None,
        rule_id: str | None,
    ) -> None:
        self.sends.append(
            SendRecord(
                envelope_id=env.id,
                run_id=env.run_id,
                seq=self._next_seq(),
                sender=env.sender.canonical(),
                recipient_role=env.recipient.role.value,
                channel=env.channel,
                intent=env.intent.value,
                status=status,
                reason=reason,
                rule_id=rule_id,
            )
        )

    async def record_saturation(
        self, channel: Channel, blocked_seconds: float, dropped: int
    ) -> None:
        self.saturations.append((channel, blocked_seconds, dropped))

    def by_status(self, status: DeliveryStatus) -> list[SendRecord]:
        return [r for r in self.sends if r.status is status]

    def delivered(self) -> list[SendRecord]:
        return self.by_status(DeliveryStatus.DELIVERED)


@dataclass
class PostgresSink:
    """Writes the same records to Postgres. One session per record; never shared."""

    session_factory: async_sessionmaker[AsyncSession]
    run_id: uuid.UUID
    _seq: int = field(default=0, init=False)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def record_send(
        self,
        env: Envelope,
        status: DeliveryStatus,
        reason: str | None,
        rule_id: str | None,
    ) -> None:
        row = envelope_to_row(
            env, seq=self._next_seq(), status=status, reason=reason, rule_id=rule_id
        )
        async with self.session_factory() as session:
            session.add(row)
            await session.commit()

    async def record_saturation(
        self, channel: Channel, blocked_seconds: float, dropped: int
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                ChannelSaturationRow(
                    id=uuid.uuid4(),
                    run_id=self.run_id,
                    channel=channel.value,
                    blocked_seconds=blocked_seconds,
                    dropped_count=dropped,
                )
            )
            await session.commit()
