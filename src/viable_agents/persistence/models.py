"""The four tables of record, plus channel saturation.

Postgres is the system of record: every envelope and every model call is a row,
written unconditionally, so the Phase 6 detectors and the Phase 7 auditor read
this database rather than a rate-limited SaaS API. The columns that cannot be
backfilled later are here from the start: ``runs.seed`` and
``runs.config_fingerprint`` (without them a run is useless as a Phase 9 baseline),
``messages.causation_id`` (the causal chain Phase 6 incident cards and Phase 7
trace reconstruction both need), ``messages.status`` / ``reject_reason`` /
``rule_id`` (a violation that only raises a Python exception is unmeasurable), and
the five separate token counts on ``llm_calls`` (prompt caching prices input
tokens non-uniformly).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from viable_agents.persistence.base import Base


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seed: Mapped[int]
    source_mode: Mapped[str] = mapped_column(String(16))  # live | synthetic
    llm_mode: Mapped[str] = mapped_column(String(16), default="live")  # live|record|replay
    fleet_config_name: Mapped[str] = mapped_column(String(64))
    topology_config_name: Mapped[str] = mapped_column(String(64))
    topology_version: Mapped[int] = mapped_column(default=1)
    config_fingerprint: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str | None] = mapped_column(String(64), default=None)
    scenario_id: Mapped[str | None] = mapped_column(String(64), default=None)
    git_sha: Mapped[str | None] = mapped_column(String(40), default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Dual clock: virtual time cannot compress real model latency, so Phase 9
    # reports simulated and wall-clock duration as separate metrics.
    started_at: Mapped[dt.datetime | None] = mapped_column(default=None)
    ended_at: Mapped[dt.datetime | None] = mapped_column(default=None)
    sim_started_at: Mapped[dt.datetime | None] = mapped_column(default=None)
    sim_ended_at: Mapped[dt.datetime | None] = mapped_column(default=None)


class AgentRow(Base):
    __tablename__ = "agents"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.run_id"), primary_key=True)
    agent_path: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_type: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    level: Mapped[int] = mapped_column(default=0)
    model_tier: Mapped[str | None] = mapped_column(String(16), default=None)
    charter_path: Mapped[str | None] = mapped_column(String(128), default=None)
    charter_version: Mapped[str | None] = mapped_column(String(32), default=None)
    budget_usd: Mapped[Decimal | None] = mapped_column(default=None)
    final_state: Mapped[str | None] = mapped_column(String(16), default=None)


class MessageRow(Base):
    __tablename__ = "messages"

    envelope_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.run_id"))
    # Assigned by the sink, monotonic per run, so replay ordering is deterministic
    # rather than dependent on a database sequence.
    seq: Mapped[int]
    schema_version: Mapped[int] = mapped_column(default=1)
    ts_wall: Mapped[dt.datetime]
    ts_sim: Mapped[dt.datetime]
    sender_path: Mapped[str] = mapped_column(String(128))
    sender_role: Mapped[str] = mapped_column(String(16))
    sender_level: Mapped[int] = mapped_column(default=0)
    recipient_requested: Mapped[str] = mapped_column(String(128))
    recipient_role: Mapped[str] = mapped_column(String(16))
    recipient_level: Mapped[int] = mapped_column(default=0)
    channel: Mapped[str] = mapped_column(String(16))
    intent: Mapped[str] = mapped_column(String(24))
    payload_kind: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]]
    # A refused send is a research output, not only an exception.
    status: Mapped[str] = mapped_column(String(12))  # delivered|rejected|dropped
    reject_reason: Mapped[str | None] = mapped_column(String(128), default=None)
    rule_id: Mapped[str | None] = mapped_column(String(48), default=None)
    valence: Mapped[str] = mapped_column(String(8), default="neutral")
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    fanout_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    charter_version: Mapped[str | None] = mapped_column(String(32), default=None)

    __table_args__ = (
        Index("ix_messages_run_seq", "run_id", "seq"),
        Index("ix_messages_run_channel_intent", "run_id", "channel", "intent"),
        Index("ix_messages_causation", "causation_id"),
        Index("ix_messages_run_status", "run_id", "status"),
    )


class LLMCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.run_id"))
    turn_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    agent_path: Mapped[str] = mapped_column(String(128))
    agent_role: Mapped[str] = mapped_column(String(16))
    vsm_level: Mapped[int] = mapped_column(default=0)
    provider: Mapped[str] = mapped_column(String(24), default="anthropic")
    model_id: Mapped[str] = mapped_column(String(48))
    tier: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_5m_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_1h_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    price_effective_date: Mapped[dt.date | None] = mapped_column(default=None)
    latency_ms: Mapped[float] = mapped_column(default=0.0)
    # retry-on-invalid is mandated (hard rule 6), so failed-and-retried calls are
    # guaranteed to exist and to cost money; they get rows too.
    attempt_index: Mapped[int] = mapped_column(default=0)
    succeeded: Mapped[bool] = mapped_column(default=True)
    stop_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    error: Mapped[str | None] = mapped_column(String(256), default=None)
    request_id: Mapped[str | None] = mapped_column(String(64), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    ts_wall: Mapped[dt.datetime]
    ts_sim: Mapped[dt.datetime]

    __table_args__ = (
        Index("ix_llm_calls_run_agent", "run_id", "agent_path"),
        Index("ix_llm_calls_turn", "turn_id"),
    )


class ChannelSaturationRow(Base):
    """Beer's Principle 2 made measurable: the metric a generic orchestrator omits."""

    __tablename__ = "channel_saturation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.run_id"))
    channel: Mapped[str] = mapped_column(String(16))
    ts_wall: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    blocked_seconds: Mapped[float] = mapped_column(default=0.0)
    dropped_count: Mapped[int] = mapped_column(default=0)
