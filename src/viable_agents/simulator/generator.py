"""The seeded synthetic CI event generator: this repo's eval ground truth.

``SyntheticSource`` yields a deterministic ``CIEvent`` stream (same seed, same
scenario -> byte-identical events and labels) and keeps the answer key,
``TruthLabel``, in a side manifest an agent never sees. A flaky test is not forced
to fail every time it is "the" anomaly for a run: each appearance rolls its own
``flake_rate`` independently, so the same test shows up passing most of the time
and failing at roughly its configured rate across the whole stream, which is what
makes flakiness a pattern ``FlakeAgent`` can discover in per-test history rather
than a label handed to it.

Only ``random.Random`` instances are used, never the module-level ``random``
(CLAUDE.md hard rule 10): a shared global generator would make two sources
constructed in the same process context-dependent instead of seed-dependent.
Not cryptographic: this is a deterministic label generator, not a security
control (``# noqa: S311`` at each call site).
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from viable_agents.simulator.config import ScenarioConfig
from viable_agents.sources.events import (
    AnomalyClass,
    CIEvent,
    Conclusion,
    EventKind,
    JobResult,
    LogExcerpt,
    SourceMode,
    TestOutcome,
    TestResult,
    TruthLabel,
)

_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
_EVENT_SPACING = dt.timedelta(minutes=5)
_ACTORS = ("alice", "bob", "carol", "dave", "eve")
_DEP_PACKAGES = ("requests", "pydantic", "sqlalchemy", "pytest", "httpx")
_COMMIT_TEMPLATES = (
    "fix: handle edge case in {mod}",
    "feat: add retry to {mod}",
    "refactor: simplify {mod}",
    "test: cover {mod} boundary conditions",
)
_INFRA_TIMEOUT_PROB = 0.7
_INFRA_DURATION_RANGE = (300.0, 900.0)
_DEFAULT_DURATION_RANGE = (20.0, 400.0)


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one anomaly branch decided, before it becomes a ``CIEvent``."""

    anomaly_class: AnomalyClass
    conclusion: Conclusion
    failed_step: str | None
    tests: tuple[TestResult, ...] = ()
    excerpts: tuple[LogExcerpt, ...] = ()
    commit_message: str | None = None
    changed_files: tuple[str, ...] | None = None
    dependency_bump: bool = False
    flaky_test_ids: tuple[str, ...] = ()
    duration: float | None = None


@dataclass(frozen=True, slots=True)
class _Draw:
    """Per-event context shared by every anomaly branch."""

    workflow_name: str
    index: int
    mod: str
    default_duration: float
    dependency_manifests: tuple[str, ...] = field(default_factory=tuple)


def _hex_sha(rng: random.Random) -> str:
    return "".join(rng.choices("0123456789abcdef", k=40))


def _pick_anomaly_class(rng: random.Random, rates: Mapping[str, float]) -> AnomalyClass:
    roll = rng.random()
    cumulative = 0.0
    for name, rate in rates.items():
        cumulative += rate
        if roll < cumulative:
            return AnomalyClass(name)
    return AnomalyClass.NONE


def _regression(rng: random.Random, draw: _Draw) -> _Outcome:
    del rng  # deterministic given the index; no roll needed
    test_id = f"tests/test_{draw.workflow_name}.py::test_case_{draw.index % 7}"
    return _Outcome(
        anomaly_class=AnomalyClass.REGRESSION,
        conclusion=Conclusion.FAILURE,
        failed_step="run tests",
        tests=(TestResult(test_id=test_id, outcome=TestOutcome.FAILED, duration_ms=42.0),),
        excerpts=(
            LogExcerpt(
                path=f"tests/test_{draw.workflow_name}.py",
                start_line=10,
                end_line=14,
                text=f"AssertionError: expected 42, got 41\n  at {test_id}",
            ),
        ),
    )


def _flake(rng: random.Random, scenario: ScenarioConfig) -> _Outcome:
    spec = rng.choice(scenario.flaky_tests)
    if rng.random() < spec.flake_rate:
        return _Outcome(
            anomaly_class=AnomalyClass.FLAKE,
            conclusion=Conclusion.FAILURE,
            failed_step="run tests",
            tests=(
                TestResult(test_id=spec.test_id, outcome=TestOutcome.FAILED, duration_ms=800.0),
            ),
            excerpts=(
                LogExcerpt(
                    path="pytest.log",
                    start_line=1,
                    end_line=3,
                    text=f"TimeoutError: connection reset (transient)\n  at {spec.test_id}",
                ),
            ),
            flaky_test_ids=(spec.test_id,),
        )
    # This appearance passed: nothing anomalous actually happened this run, but
    # the pass/fail history still records the flaky test's outcome.
    return _Outcome(
        anomaly_class=AnomalyClass.NONE,
        conclusion=Conclusion.SUCCESS,
        failed_step=None,
        tests=(TestResult(test_id=spec.test_id, outcome=TestOutcome.PASSED, duration_ms=120.0),),
        flaky_test_ids=(spec.test_id,),
    )


def _infra(rng: random.Random) -> _Outcome:
    timed_out = rng.random() < _INFRA_TIMEOUT_PROB
    conclusion = Conclusion.TIMED_OUT if timed_out else Conclusion.CANCELLED
    return _Outcome(
        anomaly_class=AnomalyClass.INFRA,
        conclusion=conclusion,
        failed_step="provision runner",
        excerpts=(
            LogExcerpt(
                path="runner.log",
                start_line=1,
                end_line=2,
                text="Error: The runner lost communication with the server.",
            ),
        ),
        duration=rng.uniform(*_INFRA_DURATION_RANGE),
    )


def _dependency(rng: random.Random, draw: _Draw) -> _Outcome:
    package = rng.choice(_DEP_PACKAGES)
    old_v = f"{rng.randint(1, 3)}.{rng.randint(0, 9)}.0"
    new_v = f"{rng.randint(4, 6)}.0.0"
    manifest = rng.choice(draw.dependency_manifests)
    return _Outcome(
        anomaly_class=AnomalyClass.DEPENDENCY,
        conclusion=Conclusion.FAILURE,
        failed_step="install dependencies",
        excerpts=(
            LogExcerpt(
                path="install.log",
                start_line=1,
                end_line=2,
                text=f"ERROR: {package}=={new_v} is incompatible with the pinned toolchain",
            ),
        ),
        commit_message=f"chore(deps): bump {package} from {old_v} to {new_v}",
        changed_files=(manifest,),
        dependency_bump=True,
    )


def _plain(rng: random.Random, scenario: ScenarioConfig) -> _Outcome:
    failed = rng.random() < scenario.base_failure_rate
    if not failed:
        return _Outcome(
            anomaly_class=AnomalyClass.NONE, conclusion=Conclusion.SUCCESS, failed_step=None
        )
    return _Outcome(
        anomaly_class=AnomalyClass.NONE,
        conclusion=Conclusion.FAILURE,
        failed_step="run tests",
        excerpts=(
            LogExcerpt(
                path="pytest.log",
                start_line=1,
                end_line=1,
                text="one-off failure, no systemic cause identified",
            ),
        ),
    )


class SyntheticSource:
    """Implements ``CIEventSource``. See ``sources.events.CIEventSource``."""

    mode = SourceMode.SYNTHETIC

    def __init__(self, scenario: ScenarioConfig) -> None:
        self._scenario = scenario
        self._rng = random.Random(scenario.seed)  # noqa: S311 - deterministic labels, not crypto
        self._labels: dict[tuple[str, int, int], TruthLabel] = {}

    def labels(self) -> Mapping[tuple[str, int, int], TruthLabel]:
        """Populated as ``stream()`` is consumed; empty before, complete after."""
        return MappingProxyType(self._labels)

    def label_for(self, key: tuple[str, int, int]) -> TruthLabel | None:
        return self._labels.get(key)

    async def stream(self) -> AsyncIterator[CIEvent]:
        for i in range(self._scenario.event_count):
            event, label = self._synthesize(i)
            self._labels[event.key] = label
            yield event

    def _draw_outcome(self, rng: random.Random, draw: _Draw) -> _Outcome:
        sc = self._scenario
        anomaly_class = _pick_anomaly_class(rng, sc.anomaly_rates)
        match anomaly_class:
            case AnomalyClass.REGRESSION:
                return _regression(rng, draw)
            case AnomalyClass.FLAKE:
                return _flake(rng, sc)
            case AnomalyClass.INFRA:
                return _infra(rng)
            case AnomalyClass.DEPENDENCY:
                return _dependency(rng, draw)
            case AnomalyClass.NONE:
                return _plain(rng, sc)

    def _synthesize(self, i: int) -> tuple[CIEvent, TruthLabel]:
        sc = self._scenario
        rng = self._rng
        repo = rng.choice(sc.repos)
        workflow_name = rng.choice(sc.workflow_names)
        branch = rng.choice(sc.branches)
        actor = rng.choice(_ACTORS)
        mod = f"{workflow_name}_module_{i % 5}"
        draw = _Draw(
            workflow_name=workflow_name,
            index=i,
            mod=mod,
            default_duration=rng.uniform(*_DEFAULT_DURATION_RANGE),
            dependency_manifests=tuple(sc.dependency_manifests),
        )
        outcome = self._draw_outcome(rng, draw)

        created_at = _EPOCH + _EVENT_SPACING * i
        started_at = created_at + dt.timedelta(seconds=5)
        duration = outcome.duration if outcome.duration is not None else draw.default_duration
        completed_at = started_at + dt.timedelta(seconds=duration)
        commit_message = outcome.commit_message or rng.choice(_COMMIT_TEMPLATES).format(mod=mod)
        changed_files = outcome.changed_files or (f"src/{mod}.py",)

        job = JobResult(
            name=workflow_name,
            conclusion=outcome.conclusion,
            started_at=started_at,
            completed_at=completed_at,
            failed_step=outcome.failed_step,
            tests=outcome.tests,
        )
        event = CIEvent(
            source_mode=SourceMode.SYNTHETIC,
            event_kind=EventKind.WORKFLOW_RUN,
            repo=repo,
            run_id=100_000 + i,
            workflow_name=workflow_name,
            conclusion=outcome.conclusion,
            head_branch=branch,
            head_sha=_hex_sha(rng),
            actor=actor,
            commit_message=commit_message,
            changed_files=changed_files,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            jobs=(job,),
            log_excerpts=outcome.excerpts,
        )
        label = TruthLabel(
            key=event.key,
            anomaly_class=outcome.anomaly_class,
            flaky_test_ids=outcome.flaky_test_ids,
            dependency_bump=outcome.dependency_bump,
        )
        return event, label
