"""FlakeAgent: per-test pass/fail history, seed-consistent flake detection.

Pure code, no model tier used, despite ``config/models.yaml`` mapping role ``s1``
to Haiku: flakiness is a property of an observed pass/fail distribution, which a
threshold over history decides more cheaply and more legibly than a model call
would. The same "code attenuates cheaper than tokens" argument the S2 module
docstring makes for the coordinator applies here -- a Haiku call could not do this
job any better than the arithmetic already does.
"""

from __future__ import annotations

from typing import Any

from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.envelope import Citation, Envelope
from viable_agents.sources.events import CIEvent, TestOutcome
from viable_agents.systems.s1.base import S1Worker
from viable_agents.systems.s1.tools import ToolLog, test_history
from viable_agents.systems.s1.verdicts import FlakeAssessment

_MIN_SAMPLES = 3
# A failure rate strictly between these bounds is "mixed": the test sometimes
# passes and sometimes fails on what the simulator holds constant otherwise,
# which is the operational definition of flaky this agent uses.
_FLAKE_BAND = (0.05, 0.95)


class FlakeAgent(S1Worker):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.tools = ToolLog()
        self._history: dict[str, list[TestOutcome]] = {}
        self._reported: set[str] = set()
        self.assessments: list[FlakeAssessment] = []

    async def handle(self, env: Envelope) -> None:
        if env.channel is not Channel.ENVIRONMENT or env.intent is not Intent.OBSERVATION:
            return
        event = env.payload.narrow(CIEvent)
        for job in event.jobs:
            for result in job.tests:
                self._history.setdefault(result.test_id, []).append(result.outcome)
                await self._maybe_report(result.test_id, causation=env)

    async def _maybe_report(self, test_id: str, *, causation: Envelope) -> None:
        if test_id in self._reported:
            return
        history = test_history(self._history, test_id, log=self.tools, clock=self.clock)
        if len(history) < _MIN_SAMPLES:
            return
        failures = sum(1 for outcome in history if outcome is TestOutcome.FAILED)
        rate = failures / len(history)
        if not (_FLAKE_BAND[0] < rate < _FLAKE_BAND[1]):
            return
        self._reported.add(test_id)
        assessment = FlakeAssessment(
            test_id=test_id,
            observed_failure_rate=rate,
            sample_size=len(history),
            is_flaky=True,
            evidence=(Citation(kind="envelope", ref=str(causation.id)),),
        )
        self.assessments.append(assessment)
        await self.report(assessment, causation=causation)
