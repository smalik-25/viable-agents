"""BudgetGuard: hard per-agent and project-wide spend caps.

This is NOT a bus seam, despite ``kernel/bus.py``'s ``add_pre_send`` docstring
once claiming Phase 4 would attach here. An LLM call is
``self.llm.complete(...)``, called directly by an agent on its ``LLMClient``,
and never crosses ``Bus.send()`` -- a bus-level hook cannot see it. Cost
arithmetic already lives outside the kernel (``llm/pricing.py``, since it
depends on ``config/models.yaml``); a spend cap is the same kind of
config-dependent, non-kernel concern, so it lives alongside it and reuses the
kernel's already-defined ``BudgetExceededError``.

``check`` and ``record`` are separate calls, not one, because retry-on-invalid
(hard rule 6) means a single ``complete()`` invocation can attempt more than
one call, each recorded as its own full-cost ``llm_calls`` row. A guard checked
only once at the top of ``complete()`` would let a retried attempt cross the
cap unrefused; the LLM clients call ``check`` before every attempt and
``record`` after every recorded row, successful or not.

Caps are keyed by agent path (``AgentAddress.canonical()``), sourced from each
agent's own ``budget_usd`` in ``config/fleet/vsm.yaml`` -- a hard, per-instance
stop, independent of and stricter than ``Controller``'s run-pool scheduling
(``systems/s3/controller.py``), which proactively pauses low-priority workers
to avoid ever tripping this guard in the first place. The project ceiling is
enforced here too, run-local for Phase 4: true cross-run enforcement needs a
Postgres query at run start and is parked (see ``config/budgets.yaml``).
"""

from __future__ import annotations

from decimal import Decimal

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.errors import BudgetExceededError


class BudgetGuard:
    """Per-agent-path caps plus one run-local project ceiling. Pure code, no I/O."""

    def __init__(
        self, *, per_agent_caps_usd: dict[str, Decimal], project_ceiling_usd: Decimal
    ) -> None:
        self._caps = dict(per_agent_caps_usd)
        self._project_ceiling = project_ceiling_usd
        self._spent: dict[str, Decimal] = {}
        self._project_spent = Decimal("0")

    def check(self, agent: AgentAddress) -> None:
        """Raise if ``agent``'s own cap, or the project ceiling, is already met."""
        path = agent.canonical()
        cap = self._caps.get(path)
        spent = self._spent.get(path, Decimal("0"))
        if cap is not None and spent >= cap:
            raise BudgetExceededError(agent=agent, cap_usd=cap, spent_usd=spent)
        if self._project_spent >= self._project_ceiling:
            raise BudgetExceededError(
                agent=agent, cap_usd=self._project_ceiling, spent_usd=self._project_spent
            )

    def record(self, agent: AgentAddress, cost_usd: Decimal) -> None:
        """Add a completed call's cost (successful or failed) to both ledgers."""
        path = agent.canonical()
        self._spent[path] = self._spent.get(path, Decimal("0")) + cost_usd
        self._project_spent += cost_usd

    def spent(self, agent: AgentAddress) -> Decimal:
        """Cumulative recorded spend for ``agent``. Read by ``Controller`` to
        compute pool headroom without a second, divergent ledger."""
        return self._spent.get(agent.canonical(), Decimal("0"))

    def cap(self, agent: AgentAddress) -> Decimal | None:
        return self._caps.get(agent.canonical())
