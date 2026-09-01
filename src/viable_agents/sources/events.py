"""The unified CI event contract. Live and synthetic both produce these, and
nothing downstream may tell them apart (CLAUDE.md hard rule 4).

``CIEvent`` is a ``Payload`` so it rides an envelope on the ENVIRONMENT channel and
rehydrates through the kernel registry with no kernel change. The ground-truth
anomaly label lives in a SEPARATE ``TruthLabel`` object the synthetic source keeps
in a side manifest, never on the event: if an S1 could read the answer off the
event it was handed, the Phase 9 ablation would be measuring a leak, not triage.

``redacted()`` overrides the base no-op so secret-shaped text in a log excerpt is
scrubbed before it can reach a prompt or a trace.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict

from viable_agents.kernel.payload import Payload


class SourceMode(StrEnum):
    LIVE = "live"
    SYNTHETIC = "synthetic"


class Conclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class EventKind(StrEnum):
    WORKFLOW_RUN = "workflow_run"
    JOB = "job"
    CHECK = "check"


class TestOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AnomalyClass(StrEnum):
    """The ground-truth taxonomy the simulator injects and BuildTriage predicts.

    ``NONE`` is a real class, not a null: a failing run can be a genuine one-off with
    no systemic cause, and a triage agent that never says "nothing systemic here" is
    as broken as one that never flags a regression.
    """

    NONE = "none"
    REGRESSION = "regression"
    FLAKE = "flake"
    INFRA = "infra"
    DEPENDENCY = "dependency"


# Secret-shaped patterns scrubbed from log text before it reaches a prompt or trace.
# Ordered specific-first; the generic high-entropy rule is last so a known token
# shape is labelled rather than caught by the catch-all.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_PAT]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), "[REDACTED_BEARER]"),
    (
        re.compile(r"(?i)(api[_-]?key|secret|token)([\"'\s:=]+)[A-Za-z0-9._\-]{16,}"),
        r"\1\2[REDACTED]",
    ),
)


def _scrub(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class LogExcerpt(BaseModel):
    """A cited slice of a job log. Evidence for a triage verdict (hard rule 6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    start_line: int
    end_line: int
    text: str

    def redacted(self) -> LogExcerpt:
        return self.model_copy(update={"text": _scrub(self.text)})


class TestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    test_id: str  # pytest-style nodeid, stable across runs
    outcome: TestOutcome
    duration_ms: float = 0.0


class JobResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    conclusion: Conclusion
    started_at: dt.datetime
    completed_at: dt.datetime
    failed_step: str | None = None
    tests: tuple[TestResult, ...] = ()


class CIEvent(Payload):
    """One workflow run (or job/check) as every downstream agent sees it.

    Carries exactly what live GitHub can supply, so the synthetic generator cannot
    accidentally hand S1 a field the live adapter lacks. The anomaly answer is NOT
    here; it lives in ``TruthLabel``.
    """

    kind: str = "ci.event"

    source_mode: SourceMode
    event_kind: EventKind = EventKind.WORKFLOW_RUN
    repo: str  # "owner/name"
    run_id: int
    run_attempt: int = 1
    workflow_name: str
    conclusion: Conclusion
    head_branch: str
    head_sha: str
    actor: str
    commit_message: str = ""
    changed_files: tuple[str, ...] = ()
    created_at: dt.datetime
    started_at: dt.datetime
    completed_at: dt.datetime
    duration_seconds: float
    jobs: tuple[JobResult, ...] = ()
    log_excerpts: tuple[LogExcerpt, ...] = ()

    @property
    def key(self) -> tuple[str, int, int]:
        """Stable identity for claim ledgers and truth lookups: (repo, run, attempt)."""
        return (self.repo, self.run_id, self.run_attempt)

    @property
    def failed(self) -> bool:
        return self.conclusion in (
            Conclusion.FAILURE,
            Conclusion.TIMED_OUT,
        )

    def failed_tests(self) -> tuple[TestResult, ...]:
        return tuple(t for job in self.jobs for t in job.tests if t.outcome is TestOutcome.FAILED)

    def redacted(self) -> Self:
        return self.model_copy(
            update={"log_excerpts": tuple(e.redacted() for e in self.log_excerpts)}
        )


class TruthLabel(BaseModel):
    """The ground-truth answer for one synthetic event. Never handed to an agent.

    The synthetic source keeps these in a side manifest keyed by ``CIEvent.key``;
    the Phase 2 spot-check and the Phase 9 ablation read them, S1 never does.
    ``onset`` is the injected-failure start (Phase 6 time-to-detection); None for
    steady-state anomalies.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: tuple[str, int, int]
    anomaly_class: AnomalyClass
    flaky_test_ids: tuple[str, ...] = ()
    dependency_bump: bool = False
    onset: dt.datetime | None = None
    notes: str = ""


@runtime_checkable
class CIEventSource(Protocol):
    """The seam ``--source live|synthetic`` swaps. Yields events, nothing else.

    Truth labels are deliberately absent from this interface: a live source has
    none, and putting them here would tempt an agent to reach for the answer.
    """

    mode: SourceMode

    def stream(self) -> AsyncIterator[CIEvent]: ...
