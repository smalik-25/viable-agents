"""DepAgent: detect dependency-bump commits, draft a CI-impact summary.

PLAN Phase 2: "detect dependency-bump commits and draft PR-description/changelog
summaries of their CI impact." Detection is a plain string/suffix check (cheap,
deterministic); the summary prose is the one place an LLM call earns its keep in
this agent, which is why detection runs before ``self.llm`` is ever touched.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.envelope import Citation, Envelope
from viable_agents.sources.events import CIEvent
from viable_agents.systems.s1.base import S1Worker
from viable_agents.systems.s1.tools import ToolLog
from viable_agents.systems.s1.verdicts import DepDraft, DepSummary

_SYSTEM_PROMPT = (
    "You draft a one-sentence changelog note for a CI run triggered by a "
    "dependency-version bump. Given the repo, the commit message, and whether the "
    "run passed or failed, respond with a summary sentence and a risk level of "
    "low, medium, or high."
)

_MANIFEST_SUFFIXES = (".lock", "requirements.txt", "pyproject.toml", "package-lock.json")
_BUMP_COMMIT_PREFIX = "chore(deps)"
_PACKAGE_PATTERN = re.compile(r"bump (\S+) from")


def _is_dependency_bump(event: CIEvent) -> bool:
    if event.commit_message.lower().startswith(_BUMP_COMMIT_PREFIX):
        return True
    return any(f.endswith(_MANIFEST_SUFFIXES) for f in event.changed_files)


def _guess_package(commit_message: str) -> str | None:
    match = _PACKAGE_PATTERN.search(commit_message)
    return match.group(1) if match else None


class DepAgent(S1Worker):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.tools = ToolLog()
        self.summaries: list[DepSummary] = []

    async def handle(self, env: Envelope) -> None:
        if env.channel is not Channel.ENVIRONMENT or env.intent is not Intent.OBSERVATION:
            return
        event = env.payload.narrow(CIEvent)
        if not _is_dependency_bump(event):
            return
        draft = await self._draft(event)
        summary = DepSummary(
            repo=event.repo,
            run_id=event.run_id,
            manifest_files=event.changed_files,
            summary=draft.summary,
            risk=draft.risk,
            evidence=(Citation(kind="envelope", ref=str(env.id)),),
        )
        self.summaries.append(summary)
        await self.report(summary, causation=env)

    async def _draft(self, event: CIEvent) -> DepDraft:
        if self.llm is None or self.model_tier is None:
            msg = "DepAgent needs an LLM client and a model tier"
            raise RuntimeError(msg)
        content = json.dumps(
            {
                "repo": event.repo,
                "conclusion": event.conclusion.value,
                "commit_message": event.commit_message,
                "package_guess": _guess_package(event.commit_message),
            }
        )
        result = await self.llm.complete(
            tier=self.model_tier,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_model=DepDraft,
            max_tokens=256,
            turn_id=uuid.uuid4(),
            agent=self.address,
        )
        return result.parsed
