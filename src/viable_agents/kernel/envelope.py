"""The Envelope: the only thing that crosses a channel boundary.

Note what is not here: ``cost``. Cost is a property of a model invocation, and one
turn makes zero, one, or many invocations while emitting zero, one, or many
envelopes, so cost lives on an ``llm_calls`` row, not here. What is here that a
generic message bus would omit is the cybernetic content: ``valence`` (Beer's
algedonic carries pleasure as well as pain), ``index`` (the algedonic fires when a
metric leaves limits, not when an event happens), ``escalation`` (always None in
Phase 1; Phase 6 fills it in with no kernel change), and ``causation_id`` (what
proves a signal is algedonic rather than an alert: the normal channel was tried
and did not resolve it).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from viable_agents.kernel.address import AgentAddress, Recipient
from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.payload import Payload

SCHEMA_VERSION = 1


class Citation(BaseModel):
    """Hard rule 6: no uncited findings. One shape, shared by Phase 6 and Phase 7."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["trace", "envelope", "log", "metric", "charter"]
    ref: str
    excerpt: str | None = None


class MetricRef(BaseModel):
    """Beer: the algedonic fires when an index leaves acceptable limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    observed: float
    limit: float
    window_seconds: float


class EscalationState(BaseModel):
    """Always None in Phase 1. Phase 6's escalation ladder fills it in unchanged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: int = 0
    attempt: int = 0
    first_detected_at: dt.datetime
    deadline_at: dt.datetime | None = None
    owner: AgentAddress | None = None
    acknowledged_at: dt.datetime | None = None
    resolved_at: dt.datetime | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # identity and lineage
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    schema_version: int = SCHEMA_VERSION
    run_id: uuid.UUID
    turn_id: uuid.UUID | None = None
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    causation_id: uuid.UUID | None = None
    fanout_id: uuid.UUID | None = None

    # time: dual clock (hard rule 10)
    ts_wall: dt.datetime
    ts_sim: dt.datetime
    deadline_at: dt.datetime | None = None

    # routing
    sender: AgentAddress
    recipient: Recipient
    channel: Channel
    intent: Intent

    # body. SerializeAsAny: without it Pydantic serializes against the DECLARED
    # type (Payload) and silently drops every subclass field on the way to the
    # Postgres JSONB column.
    payload: SerializeAsAny[Payload]

    # cybernetic content
    valence: Literal["pain", "pleasure", "neutral"] = "neutral"
    index: MetricRef | None = None
    escalation: EscalationState | None = None
    immediate: bool = False
    evidence: tuple[Citation, ...] = ()

    # governance and observability
    charter_version: str | None = None
    trace_id: str | None = None
    requires_human: bool = False
    blocking: bool = False
    priority: Annotated[int, Field(ge=0, le=9)] = 5

    @model_validator(mode="before")
    @classmethod
    def _rehydrate_payload(cls, data: Any) -> Any:
        """Rebuild the concrete payload subclass on the way in from a JSONB dict.

        The symmetric half of SerializeAsAny: dump writes the subclass fields,
        this reads them back through the registry. The kernel still never names a
        concrete payload type.
        """
        if isinstance(data, dict):
            raw = data.get("payload")
            if isinstance(raw, dict):
                data = {**data, "payload": Payload.rehydrate(raw)}
        return data

    def reply(
        self,
        *,
        sender: AgentAddress,
        payload: Payload,
        channel: Channel,
        intent: Intent,
        ts_wall: dt.datetime,
        ts_sim: dt.datetime,
    ) -> Envelope:
        """Build a response that preserves the causal chain.

        The only sanctioned way to answer an envelope, so ``causation_id`` and
        ``correlation_id`` cannot be broken by hand.
        """
        return Envelope(
            run_id=self.run_id,
            correlation_id=self.correlation_id,
            causation_id=self.id,
            ts_wall=ts_wall,
            ts_sim=ts_sim,
            sender=sender,
            recipient=self.sender,
            channel=channel,
            intent=intent,
            payload=payload,
            charter_version=self.charter_version,
        )
