"""BuildTriageAgent: classify a failing workflow run, citing a log excerpt.

PLAN Phase 2: "classify each failing run: code regression vs infra vs flake vs
dependency, with a cited log excerpt." The LLM decides the class and writes a
one-sentence rationale; this module attaches the citation from the actual event
(``_citations``), never from the model's own claim of what it read.

Phase 3: ``coordinate`` (default ``True``) routes every event through S2's
work-claim ledger before classifying, so two ``BuildTriageAgent`` instances
racing the same workflow run -- a legitimate horizontal scale-out, since
``env_in_s1`` fans an observation out to every agent registered under the S1
role, not to one instance -- do not both report it. ``coordinate=False``
reproduces Phase 2's original, unmediated behavior; every real deployment
leaves it on, since ``config/fleet/vsm.yaml`` always registers a ``Coordinator``.
It exists so ``tests/unit/test_s2_coordination.py`` can demonstrate the failure
mode S2 fixes using this exact class, not a stand-in.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.envelope import Citation, Envelope
from viable_agents.sources.events import CIEvent, Conclusion, LogExcerpt
from viable_agents.systems.s1.base import S1Worker
from viable_agents.systems.s1.tools import ToolLog, fetch_log_excerpts
from viable_agents.systems.s1.verdicts import TriageDecision, TriageVerdict
from viable_agents.systems.s2.ledger import ClaimDecision

_WORK_TYPE = "triage"

_SYSTEM_PROMPT = (
    "You triage a failing CI workflow run for a software engineering fleet. Given "
    "the run's log excerpts, commit message, and changed files, classify the "
    "failure as exactly one of: regression, flake, infra, dependency, none. "
    "Respond with predicted_class, a confidence in [0,1], and a one-sentence rationale."
)

# Runs in these conclusions have nothing for a triage agent to classify.
_SKIP_CONCLUSIONS = (Conclusion.SUCCESS, Conclusion.SKIPPED)


def _prompt_context(event: CIEvent, excerpts: tuple[LogExcerpt, ...]) -> str:
    return json.dumps(
        {
            "repo": event.repo,
            "run_id": event.run_id,
            "conclusion": event.conclusion.value,
            "commit_message": event.commit_message,
            "changed_files": list(event.changed_files),
            "log_text": [e.text for e in excerpts],
        }
    )


def _citations(
    event: CIEvent, excerpts: tuple[LogExcerpt, ...], causation: Envelope
) -> tuple[Citation, ...]:
    if not excerpts:
        return (Citation(kind="envelope", ref=str(causation.id)),)
    return tuple(
        Citation(
            kind="log",
            ref=f"{event.repo}#{event.run_id}:{e.path}:{e.start_line}-{e.end_line}",
            excerpt=e.text[:280],
        )
        for e in excerpts
    )


class BuildTriageAgent(S1Worker):
    """Haiku-tier by default (``config/models.yaml``); see ``demo.py`` for --live-llm."""

    def __init__(self, *, coordinate: bool = True, **kw: Any) -> None:
        super().__init__(**kw)
        self.tools = ToolLog()
        self.verdicts: list[TriageVerdict] = []
        self.coordinate = coordinate

    async def handle(self, env: Envelope) -> None:
        if env.channel is Channel.COORDINATION and env.intent is Intent.ARBITRATE:
            await self._on_arbitrate(env)
            return
        if env.channel is not Channel.ENVIRONMENT or env.intent is not Intent.OBSERVATION:
            return
        event = env.payload.narrow(CIEvent)
        if event.conclusion in _SKIP_CONCLUSIONS:
            return
        if self.coordinate:
            await self.claim(event.key, _WORK_TYPE, causation=env)
        else:
            await self._classify_and_report(event, causation=env)

    async def _on_arbitrate(self, env: Envelope) -> None:
        decision = env.payload.narrow(ClaimDecision)
        if decision.work_type != _WORK_TYPE:
            return
        causation = self.resume_claim(decision)
        if causation is None:
            return  # denied, or a reply for a claim already resolved elsewhere
        event = causation.payload.narrow(CIEvent)
        await self._classify_and_report(event, causation=causation)
        await self.release(event.key, _WORK_TYPE, causation=causation)

    async def _classify_and_report(self, event: CIEvent, *, causation: Envelope) -> None:
        excerpts = fetch_log_excerpts(event, log=self.tools, clock=self.clock)
        decision = await self._classify(event, excerpts)
        verdict = TriageVerdict(
            repo=event.repo,
            run_id=event.run_id,
            run_attempt=event.run_attempt,
            predicted_class=decision.predicted_class,
            confidence=decision.confidence,
            rationale=decision.rationale,
            evidence=_citations(event, excerpts, causation),
        )
        self.verdicts.append(verdict)
        await self.report(verdict, causation=causation)

    async def _classify(self, event: CIEvent, excerpts: tuple[LogExcerpt, ...]) -> TriageDecision:
        if self.llm is None or self.model_tier is None:
            msg = "BuildTriageAgent needs an LLM client and a model tier"
            raise RuntimeError(msg)
        result = await self.llm.complete(
            tier=self.model_tier,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt_context(event, excerpts)}],
            output_model=TriageDecision,
            max_tokens=256,
            turn_id=uuid.uuid4(),
            agent=self.address,
        )
        return result.parsed
