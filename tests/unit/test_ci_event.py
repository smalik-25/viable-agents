"""CIEvent: the contract both sources produce and every S1 agent consumes.

CLAUDE.md hard rule 4: everything downstream of ``CIEvent`` must be unable to
tell live from synthetic apart. This asserts the payload registry round-trips a
``CIEvent`` (so it survives a Postgres JSONB write/read like any other envelope
body) and that redaction actually scrubs secret-shaped log text before it could
reach a prompt or a trace.
"""

from __future__ import annotations

import datetime as dt

from viable_agents.kernel import Payload
from viable_agents.sources.events import (
    CIEvent,
    Conclusion,
    EventKind,
    LogExcerpt,
    SourceMode,
)

_NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _event(**overrides: object) -> CIEvent:
    fields: dict[str, object] = {
        "source_mode": SourceMode.SYNTHETIC,
        "event_kind": EventKind.WORKFLOW_RUN,
        "repo": "octo/widgets",
        "run_id": 1,
        "workflow_name": "ci",
        "conclusion": Conclusion.FAILURE,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "actor": "alice",
        "created_at": _NOW,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_seconds": 1.0,
    }
    fields.update(overrides)
    return CIEvent.model_validate(fields)


def test_round_trips_through_the_payload_registry() -> None:
    event = _event(log_excerpts=(LogExcerpt(path="x", start_line=1, end_line=2, text="boom"),))
    dumped = event.model_dump(mode="json")
    rehydrated = Payload.rehydrate(dumped)
    assert rehydrated == event
    assert isinstance(rehydrated, CIEvent)


def test_redaction_scrubs_secret_shaped_text_and_leaves_the_rest() -> None:
    event = _event(
        log_excerpts=(
            LogExcerpt(
                path="deploy.log",
                start_line=1,
                end_line=1,
                text="using token ghp_abcdefghijklmnopqrstuvwxyz012345 to push",
            ),
        )
    )
    redacted = event.redacted()
    text = redacted.log_excerpts[0].text
    assert "ghp_" not in text
    assert "[REDACTED_GITHUB_TOKEN]" in text
    assert "using token" in text  # surrounding context is preserved
    # redacted() never mutates the source event
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" in event.log_excerpts[0].text


def test_failed_and_failed_tests_helpers() -> None:
    from viable_agents.sources.events import JobResult, TestOutcome, TestResult

    passing = _event(conclusion=Conclusion.SUCCESS)
    assert not passing.failed

    failing = _event(
        conclusion=Conclusion.FAILURE,
        jobs=(
            JobResult(
                name="build",
                conclusion=Conclusion.FAILURE,
                started_at=_NOW,
                completed_at=_NOW,
                tests=(
                    TestResult(test_id="t1", outcome=TestOutcome.FAILED),
                    TestResult(test_id="t2", outcome=TestOutcome.PASSED),
                ),
            ),
        ),
    )
    assert failing.failed
    assert [t.test_id for t in failing.failed_tests()] == ["t1"]


def test_key_identifies_a_run_uniquely() -> None:
    a = _event(repo="octo/a", run_id=1, run_attempt=1)
    b = _event(repo="octo/a", run_id=1, run_attempt=2)
    assert a.key != b.key
    assert a.key == ("octo/a", 1, 1)
