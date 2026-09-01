"""RunReport: S3's periodic accountability snapshot.

PLAN Phase 4: "S3 emits a structured RunReport (work done, cost, anomaly counts
per S1) at interval; this is what S3* will later audit against." Cadence is
event-count based (every K accountability reports, plus once at drain), not a
wall-clock timer -- deterministic without exercising ``Clock.call_later``, which
nothing uses yet.

``RunReport`` is a ``Payload`` (wire-ready, like ``systems/s1/verdicts.py``'s
types) so Phase 7's auditor can receive it as an envelope with no redesign, even
though Phase 4 only ever persists it directly. It deliberately does not carry
the fleet run id or timestamps itself: those belong to whatever wraps it for
persistence or transport (``RunReportRecord`` below), the same separation an
``Envelope`` already draws for every other payload.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from viable_agents.kernel.payload import Payload


class AgentBreakdown(BaseModel):
    """One agent's contribution to a snapshot: what it did, what it cost."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_path: str
    role: str
    reports_seen: int = 0
    anomalies_seen: int = 0
    cost_usd: Decimal = Decimal("0")
    cap_usd: Decimal | None = None
    state: str = "running"


class RunReport(Payload):
    """One snapshot: per-agent breakdown, run totals, and a short narrative."""

    kind: str = "s3.run_report"

    seq: int
    per_agent: dict[str, AgentBreakdown]
    total_cost_usd: Decimal
    total_anomalies: int
    narrative: str = ""


class RunReportNarrative(BaseModel):
    """The LLM's raw structured output: one sentence summarizing the snapshot.

    Mirrors ``DepDraft`` (``systems/s1/verdicts.py``): the allocation decision
    itself stays deterministic (``systems/s3/controller.py``'s module docstring
    explains why), and this is the one place a model call earns its keep --
    prose, not a decision with real consequences.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
