"""S1, Operations: the units that do the object-level work.

Every S1 is in principle a viable system in its own right. That recursion is not
implemented, but no interface here may preclude it, which is why addresses are
path-shaped and the algedonic recipient is resolved rather than hardcoded.

Phase 2 adds BuildTriageAgent (classify a failing workflow run as regression,
infra, flake or dependency, citing a log excerpt), FlakeAgent (per-test pass/fail
history), and DepAgent (dependency-bump impact summaries). Haiku tier, except
FlakeAgent, which is pure code (see its module docstring for why).
"""

from viable_agents.systems.s1.base import S1Worker, s3_seat
from viable_agents.systems.s1.dep import DepAgent
from viable_agents.systems.s1.flake import FlakeAgent
from viable_agents.systems.s1.scripting import scripted_dep_responder, scripted_triage_responder
from viable_agents.systems.s1.tools import ToolCall, ToolLog, fetch_log_excerpts, test_history
from viable_agents.systems.s1.triage import BuildTriageAgent
from viable_agents.systems.s1.verdicts import (
    DepDraft,
    DepSummary,
    FlakeAssessment,
    TriageDecision,
    TriageVerdict,
)

__all__ = [
    "BuildTriageAgent",
    "DepAgent",
    "DepDraft",
    "DepSummary",
    "FlakeAgent",
    "FlakeAssessment",
    "S1Worker",
    "ToolCall",
    "ToolLog",
    "TriageDecision",
    "TriageVerdict",
    "fetch_log_excerpts",
    "s3_seat",
    "scripted_dep_responder",
    "scripted_triage_responder",
    "test_history",
]
