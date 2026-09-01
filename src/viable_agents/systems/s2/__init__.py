"""S2, Coordination: damping oscillation between S1 units.

Beer's most under-appreciated system, and the cheapest. S2 is a variety
attenuator, and code attenuates more cheaply than tokens, so the work-claim
ledger, backoff schedules and locks are pure Python with no model tier at all.
An LLM appears only for tie-breaks that rules cannot express.

That S2 costs zero dollars while removing duplicate work is itself a finding
worth reporting in Phase 9, not an implementation shortcut. Phase 3.
"""

from viable_agents.systems.s2.coordinator import Coordinator
from viable_agents.systems.s2.ledger import (
    ClaimDecision,
    ClaimKey,
    WorkClaim,
    WorkClaimLedger,
    WorkRelease,
)

__all__ = [
    "ClaimDecision",
    "ClaimKey",
    "Coordinator",
    "WorkClaim",
    "WorkClaimLedger",
    "WorkRelease",
]
