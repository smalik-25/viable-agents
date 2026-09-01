"""Controller: S3 control, the resource bargain and its accountability return.

Receives every S1 accountability report (``config/topology/vsm.yaml``'s
``cmd_s1_s3_account`` rule -- already flowing since Phase 2, previously
unconsumed). On each one, checks cumulative recorded spend (``llm/budget.py``'s
shared ``BudgetGuard``, the same ledger the LLM clients enforce against) against
the run's pool and, if the pool is spent, pauses the lowest-priority still-
running S1 (``COMMAND``/``pause``) -- graceful shedding, distinct from and
proactive ahead of ``BudgetGuard``'s own hard per-instance refusal. It resumes
the highest-priority paused S1 when a running agent PERMANENTLY exits the pool
contest by hitting its own cap (cumulative spend is monotonic and never
refunds, so "headroom returns" only when guaranteed future spend from someone
else drops to zero, not from spend going down).

The allocation decision is deterministic, not LLM-decided: mirrors S2's
"code attenuates cheaper than tokens" argument (``systems/s2/__init__.py``),
extended here because budget enforcement should not be a model's call. The one
Sonnet-tier call this agent makes is the ``RunReport`` narrative -- prose, not
a decision with real consequences -- and a failure there (budget exceeded,
invalid structured output) falls back to an empty narrative rather than
degrading the agent that is supposed to be preventing degradation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.agent import Agent
from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.envelope import Envelope
from viable_agents.kernel.errors import BudgetExceededError
from viable_agents.kernel.payload import Payload
from viable_agents.llm.budget import BudgetGuard
from viable_agents.llm.client import StructuredOutputError
from viable_agents.persistence.run_reports import RunReportRecord, RunReportRecorder
from viable_agents.sources.events import AnomalyClass
from viable_agents.systems.s1.verdicts import DepSummary, FlakeAssessment, TriageVerdict
from viable_agents.systems.s3.reports import AgentBreakdown, RunReport, RunReportNarrative

_SYSTEM_PROMPT = (
    "You summarize one snapshot of a CI triage fleet's activity for an "
    "accountability report. Given per-agent report counts, costs, and anomaly "
    "counts, write ONE sentence describing what happened, calling out anything "
    "notable: a paused worker, a cost concentration, a high anomaly rate."
)

_DEFAULT_REPORT_EVERY = 50


class ControlDirective(Payload):
    """Controller -> S1 on COMMAND/pause or COMMAND/resume. ``reason`` makes a
    shed or a reprieve auditable without a second lookup."""

    kind: str = "s3.control_directive"

    reason: str


@dataclass(frozen=True, slots=True)
class RosterEntry:
    address: AgentAddress
    priority: int
    cap_usd: Decimal | None


def _is_anomaly(payload: Payload) -> bool:
    if isinstance(payload, TriageVerdict):
        return payload.predicted_class is not AnomalyClass.NONE
    if isinstance(payload, FlakeAssessment):
        return payload.is_flaky
    if isinstance(payload, DepSummary):
        return payload.risk in ("medium", "high")
    return False


class Controller(Agent):
    def __init__(
        self,
        *,
        roster: list[RosterEntry],
        pool_usd: Decimal,
        budget_guard: BudgetGuard,
        report_sink: RunReportRecorder,
        report_every: int = _DEFAULT_REPORT_EVERY,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._roster = {e.address.canonical(): e for e in roster}
        self._pool_usd = pool_usd
        self._guard = budget_guard
        self._report_sink = report_sink
        self._report_every = report_every
        self.paused: set[str] = set()
        self._exhausted: set[str] = set()
        # Cumulative spend never refunds, so "total spent >= pool" stays true
        # forever once tripped -- rechecking it unconditionally on every later
        # report would shed one more agent each time, cascading through the
        # whole roster. Shedding disarms itself until a resume (real headroom)
        # re-arms it, so at most one shed responds to each distinct overage.
        self._shed_armed = True
        self._reports_seen: dict[str, int] = {}
        self._anomalies_seen: dict[str, int] = {}
        self._reports_since_snapshot = 0
        self.seq = 0
        self.reports: list[RunReport] = []

    async def handle(self, env: Envelope) -> None:
        if env.channel is not Channel.COMMAND or env.intent is not Intent.ACCOUNTABILITY:
            return
        path = env.sender.canonical()
        if path not in self._roster:
            return
        self._reports_seen[path] = self._reports_seen.get(path, 0) + 1
        if _is_anomaly(env.payload):
            self._anomalies_seen[path] = self._anomalies_seen.get(path, 0) + 1

        await self._rebalance(causation=env)

        self._reports_since_snapshot += 1
        if self._reports_since_snapshot >= self._report_every:
            await self.emit_report(run_id=env.run_id)
            self._reports_since_snapshot = 0

    async def _rebalance(self, *, causation: Envelope) -> None:
        total_spent = sum(
            (self._guard.spent(e.address) for e in self._roster.values()), Decimal("0")
        )
        if self._shed_armed and total_spent >= self._pool_usd:
            await self._shed_lowest_priority(causation=causation)
        await self._resume_on_exhaustion(causation=causation)

    async def _shed_lowest_priority(self, *, causation: Envelope) -> None:
        running = [e for e in self._roster.values() if e.address.canonical() not in self.paused]
        if not running:
            return
        victim = min(running, key=lambda e: e.priority)
        self.paused.add(victim.address.canonical())
        self._shed_armed = False
        await self._send_control(
            victim.address, Intent.PAUSE, reason="budget_exceeded", causation=causation
        )

    async def _resume_on_exhaustion(self, *, causation: Envelope) -> None:
        """A running agent permanently exiting the pool contest (its own hard cap
        reached -- it can never spend another dollar this run) is the only
        headroom signal available when cumulative spend never refunds."""
        newly_exhausted = False
        for entry in self._roster.values():
            path = entry.address.canonical()
            if path in self._exhausted or path in self.paused or entry.cap_usd is None:
                continue
            if self._guard.spent(entry.address) >= entry.cap_usd:
                self._exhausted.add(path)
                newly_exhausted = True
        if not newly_exhausted or not self.paused:
            return
        winner = max((self._roster[p] for p in self.paused), key=lambda e: e.priority)
        self.paused.discard(winner.address.canonical())
        self._shed_armed = True
        await self._send_control(
            winner.address, Intent.RESUME, reason="headroom_freed", causation=causation
        )

    async def _send_control(
        self, target: AgentAddress, intent: Intent, *, reason: str, causation: Envelope
    ) -> None:
        await self.emit(
            Envelope(
                run_id=causation.run_id,
                correlation_id=causation.correlation_id,
                causation_id=causation.id,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
                sender=self.address,
                recipient=target,
                channel=Channel.COMMAND,
                intent=intent,
                payload=ControlDirective(reason=reason),
            )
        )

    async def emit_report(self, *, run_id: uuid.UUID) -> RunReport:
        per_agent: dict[str, AgentBreakdown] = {}
        total_cost = Decimal("0")
        total_anomalies = 0
        for entry in self._roster.values():
            path = entry.address.canonical()
            spent = self._guard.spent(entry.address)
            total_cost += spent
            anomalies = self._anomalies_seen.get(path, 0)
            total_anomalies += anomalies
            per_agent[path] = AgentBreakdown(
                agent_path=path,
                role=entry.address.role.value,
                reports_seen=self._reports_seen.get(path, 0),
                anomalies_seen=anomalies,
                cost_usd=spent,
                cap_usd=entry.cap_usd,
                state="paused" if path in self.paused else "running",
            )
        self.seq += 1
        narrative = await self._draft_narrative(per_agent, total_cost, total_anomalies)
        report = RunReport(
            seq=self.seq,
            per_agent=per_agent,
            total_cost_usd=total_cost,
            total_anomalies=total_anomalies,
            narrative=narrative,
        )
        self.reports.append(report)
        await self._report_sink.record(
            RunReportRecord(
                id=uuid.uuid4(),
                run_id=run_id,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
                report=report,
            )
        )
        return report

    async def _draft_narrative(
        self, per_agent: dict[str, AgentBreakdown], total_cost: Decimal, total_anomalies: int
    ) -> str:
        if self.llm is None or self.model_tier is None:
            return ""
        content = json.dumps(
            {
                "per_agent": {
                    path: {
                        "reports_seen": b.reports_seen,
                        "anomalies_seen": b.anomalies_seen,
                        "cost_usd": str(b.cost_usd),
                        "state": b.state,
                    }
                    for path, b in per_agent.items()
                },
                "total_cost_usd": str(total_cost),
                "total_anomalies": total_anomalies,
            }
        )
        try:
            result = await self.llm.complete(
                tier=self.model_tier,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_model=RunReportNarrative,
                max_tokens=256,
                turn_id=uuid.uuid4(),
                agent=self.address,
            )
        except (BudgetExceededError, StructuredOutputError):
            return ""
        return result.parsed.summary
