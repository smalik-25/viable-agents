"""Structured outputs for the three S1 worker types.

Each LLM call returns a small ``BaseModel`` decision (``TriageDecision``,
``DepDraft``); the agent, not the model, attaches evidence citations built from
the actual ``CIEvent`` it read, and wraps the decision into a ``Payload`` for the
envelope (``TriageVerdict``, ``FlakeAssessment``, ``DepSummary``). Citations come
from code rather than the model's own claims, because "no uncited findings"
(CLAUDE.md hard rule 6) needs evidence that is actually traceable to the event, not
text the model asserts is a quotation.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from viable_agents.kernel.envelope import Citation
from viable_agents.kernel.payload import Payload
from viable_agents.sources.events import AnomalyClass

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class TriageDecision(BaseModel):
    """The LLM's raw structured output for one failing run."""

    model_config = ConfigDict(extra="forbid")

    predicted_class: AnomalyClass
    confidence: Confidence
    rationale: str


class TriageVerdict(Payload):
    """BuildTriageAgent's report, wire-ready for COMMAND/accountability to S3."""

    kind: str = "s1.triage_verdict"

    repo: str
    run_id: int
    run_attempt: int
    predicted_class: AnomalyClass
    confidence: Confidence
    rationale: str
    evidence: tuple[Citation, ...] = ()


class FlakeAssessment(Payload):
    """FlakeAgent's report: per-test history crossed the flaky threshold."""

    kind: str = "s1.flake_assessment"

    test_id: str
    observed_failure_rate: Confidence
    sample_size: int
    is_flaky: bool
    evidence: tuple[Citation, ...] = ()


class DepDraft(BaseModel):
    """The LLM's raw structured output for a dependency-bump commit."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    risk: Annotated[str, Field(pattern="^(low|medium|high)$")]


class DepSummary(Payload):
    """DepAgent's report: a dependency-bump commit and its CI impact."""

    kind: str = "s1.dep_summary"

    repo: str
    run_id: int
    manifest_files: tuple[str, ...]
    summary: str
    risk: str
    evidence: tuple[Citation, ...] = ()
