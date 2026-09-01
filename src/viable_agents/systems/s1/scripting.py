"""The default, free, deterministic classification path.

Real Haiku calls cost money and are not reproducible run to run, which breaks the
synthetic arm's determinism (CLAUDE.md hard rule 10) and spends against the $50
project ceiling on every test. These responders are the default; ``AnthropicLLMClient``
is opt-in via ``--live-llm`` (``demo.py``). They implement ``llm.client.Responder``:
``(output_model, system, messages) -> dict``, reading the same JSON context the
agent puts in its prompt so the free path and the real path see identical input.

The keyword heuristics below are deliberately naive pattern matches against the
log text the simulator itself generates (``simulator/generator.py``): a cheap
model and this scripted stand-in are expected to agree on the obvious cases, which
is what the Phase 2 agreement spot-check measures.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

_DEP_MANIFEST_SUFFIXES = (".lock", "requirements.txt", "pyproject.toml", "package-lock.json")
_INFRA_HINTS = ("runner", "communication", "provision")
_FLAKE_HINTS = ("transient", "timeout", "reset")
_REGRESSION_HINTS = ("assertionerror", "traceback")


def _context(messages: Sequence[object]) -> dict[str, Any]:
    last = messages[-1]
    content = last["content"] if isinstance(last, dict) else str(last)
    result: dict[str, Any] = json.loads(str(content))
    return result


def scripted_triage_responder(
    output_model: type[BaseModel], system: str, messages: Sequence[object]
) -> dict[str, Any]:
    del output_model, system
    ctx = _context(messages)
    text = " ".join(ctx.get("log_text", [])).lower()
    commit = str(ctx.get("commit_message", "")).lower()
    changed_files = ctx.get("changed_files", [])

    if commit.startswith("chore(deps)") or any(
        str(f).endswith(_DEP_MANIFEST_SUFFIXES) for f in changed_files
    ):
        return {
            "predicted_class": "dependency",
            "confidence": 0.8,
            "rationale": "dependency manifest changed or a bump-style commit message",
        }
    if any(hint in text for hint in _INFRA_HINTS):
        return {
            "predicted_class": "infra",
            "confidence": 0.75,
            "rationale": "runner/infra failure signature in the log excerpt",
        }
    if any(hint in text for hint in _FLAKE_HINTS):
        return {
            "predicted_class": "flake",
            "confidence": 0.6,
            "rationale": "transient-looking failure signature in the log excerpt",
        }
    if any(hint in text for hint in _REGRESSION_HINTS):
        return {
            "predicted_class": "regression",
            "confidence": 0.7,
            "rationale": "assertion failure signature in the log excerpt",
        }
    return {
        "predicted_class": "none",
        "confidence": 0.4,
        "rationale": "no systemic failure signature found",
    }


def scripted_dep_responder(
    output_model: type[BaseModel], system: str, messages: Sequence[object]
) -> dict[str, Any]:
    del output_model, system
    ctx = _context(messages)
    package = ctx.get("package_guess") or "a dependency"
    conclusion = ctx.get("conclusion", "success")
    risk = "high" if conclusion == "failure" else "low"
    verb = "broke" if conclusion == "failure" else "passed cleanly against"
    return {
        "summary": f"Bumping {package} {verb} CI on {ctx.get('repo', 'the repo')}.",
        "risk": risk,
    }
