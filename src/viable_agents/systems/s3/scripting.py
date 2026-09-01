"""The default, free, deterministic path for Controller's one LLM call.

Mirrors ``systems/s1/scripting.py``'s rationale exactly: a real Sonnet call
costs money and is not reproducible run to run, which breaks the synthetic
arm's determinism (hard rule 10). This responder implements
``llm.client.Responder`` and reads the same JSON context
``Controller._draft_narrative`` puts in its prompt.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel


def scripted_narrative_responder(
    output_model: type[BaseModel], system: str, messages: Sequence[object]
) -> dict[str, Any]:
    del output_model, system
    last = messages[-1]
    content = last["content"] if isinstance(last, dict) else str(last)
    ctx: dict[str, Any] = json.loads(str(content))

    per_agent: dict[str, Any] = ctx.get("per_agent", {})
    total_cost = ctx.get("total_cost_usd", "0")
    total_anomalies = ctx.get("total_anomalies", 0)
    paused = sorted(path for path, info in per_agent.items() if info.get("state") == "paused")

    tail = f"paused: {', '.join(paused)}." if paused else "no workers paused."
    summary = (
        f"${total_cost} spent across {len(per_agent)} agent(s), "
        f"{total_anomalies} anomaly signal(s); {tail}"
    )
    return {"summary": summary}
