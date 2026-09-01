"""S3, Control: the here-and-now manager of the inside-and-now.

Owns the resource bargain with S1: budget in dollars and tokens flows down on
COMMAND with intent `allocation`, and keeps flowing only while accountability
flows back up. Can pause and resume workers, and emits a structured RunReport at
interval, which is the artifact S3* later audits against. Sonnet tier. Phase 4.
"""

from viable_agents.systems.s3.controller import ControlDirective, Controller, RosterEntry
from viable_agents.systems.s3.reports import AgentBreakdown, RunReport, RunReportNarrative
from viable_agents.systems.s3.scripting import scripted_narrative_responder

__all__ = [
    "AgentBreakdown",
    "ControlDirective",
    "Controller",
    "RosterEntry",
    "RunReport",
    "RunReportNarrative",
    "scripted_narrative_responder",
]
