"""S1 tools: plain typed Python functions, every call logged.

PLAN Phase 2: "S1 tools are plain typed Python functions registered on the
agent; log every call." The log is ``ToolLog``, an in-process record an agent
carries (``self.tools``); tests assert against it directly rather than through a
side channel, and a future phase can sink it to Postgres with no change to the
call sites.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field

from viable_agents.kernel.clock import Clock
from viable_agents.sources.events import CIEvent, LogExcerpt, TestOutcome


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args_summary: str
    result_summary: str
    ts: dt.datetime


@dataclass
class ToolLog:
    calls: list[ToolCall] = field(default_factory=list)

    def record(self, name: str, *, args_summary: str, result_summary: str, ts: dt.datetime) -> None:
        self.calls.append(
            ToolCall(name=name, args_summary=args_summary, result_summary=result_summary, ts=ts)
        )


def fetch_log_excerpts(event: CIEvent, *, log: ToolLog, clock: Clock) -> tuple[LogExcerpt, ...]:
    """The only place an agent reads log text: routed here so it is always logged."""
    result = event.log_excerpts
    log.record(
        name="fetch_log_excerpts",
        args_summary=f"{event.repo}#{event.run_id}",
        result_summary=f"{len(result)} excerpt(s)",
        ts=clock.wall(),
    )
    return result


def test_history(
    history: Mapping[str, list[TestOutcome]], test_id: str, *, log: ToolLog, clock: Clock
) -> list[TestOutcome]:
    """Per-test outcome history as FlakeAgent has accumulated it so far this run."""
    result = list(history.get(test_id, []))
    log.record(
        name="test_history",
        args_summary=test_id,
        result_summary=f"{len(result)} sample(s)",
        ts=clock.wall(),
    )
    return result
