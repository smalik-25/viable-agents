"""Coordinator: S2 anti-oscillation, wired to S1 over COORDINATION.

Beer: S2 damps oscillation between S1 units. The work-claim ledger is the
attenuator, not a model call, so this agent never touches ``self.llm`` (see the
module docstring in ``systems/s2/__init__.py``). PLAN Phase 3 reserves an LLM for
"tie-breaks that rules can't express"; first-claim-wins is not that case, so no
tie-break path exists yet rather than one wired to a decision nothing calls.
"""

from __future__ import annotations

from typing import Any

from viable_agents.kernel.agent import Agent
from viable_agents.kernel.channels import Channel, Intent
from viable_agents.kernel.envelope import Envelope
from viable_agents.systems.s2.ledger import ClaimDecision, WorkClaim, WorkClaimLedger, WorkRelease


class Coordinator(Agent):
    def __init__(self, *, ttl_seconds: float = 300.0, **kw: Any) -> None:
        super().__init__(**kw)
        self.ledger = WorkClaimLedger(ttl_seconds=ttl_seconds)
        self.decisions: list[ClaimDecision] = []

    async def handle(self, env: Envelope) -> None:
        if env.channel is not Channel.COORDINATION:
            return
        if env.intent is Intent.CLAIM:
            await self._handle_claim(env)
        elif env.intent is Intent.RELEASE:
            self._handle_release(env)

    async def _handle_claim(self, env: Envelope) -> None:
        claim = env.payload.narrow(WorkClaim)
        decision = self.ledger.claim(
            claim.key, claim.work_type, claimant=env.sender.canonical(), now=self.clock.now()
        )
        self.decisions.append(decision)
        await self.emit(
            env.reply(
                sender=self.address,
                payload=decision,
                channel=Channel.COORDINATION,
                intent=Intent.ARBITRATE,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
            )
        )

    def _handle_release(self, env: Envelope) -> None:
        release = env.payload.narrow(WorkRelease)
        self.ledger.release(release.key, release.work_type, claimant=env.sender.canonical())
