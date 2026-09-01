"""Shared plumbing for the three S1 worker types.

``S1Worker`` is not a new abstraction over ``Agent``: it is the same
causally-linked envelope construction ``demo.py``'s stubs use, lifted out once
three concrete agents need it instead of one. Every S1 report travels COMMAND
with intent ACCOUNTABILITY to the S3 seat -- the resource bargain closing, per
``config/topology/vsm.yaml``'s ``cmd_s1_s3_account`` rule -- never as an
INTERVENTION or ALLOCATION, which S1 is not authorized to send.

Phase 3 adds ``claim``/``resume_claim``/``release``: the S1 side of the S2
work-claim protocol (``config/topology/vsm.yaml``'s ``coord_s1_s2_claim``,
``coord_s2_s1_arbitrate`` and ``coord_s1_s2_release`` rules). A claim is
fire-and-forget -- the agent stashes the causing envelope and returns, rather than
blocking its single inbox on a reply that may be preceded by other work -- and
``resume_claim`` is how a later, unrelated ``handle()`` call (the ARBITRATE reply)
picks that stashed work back up.

Phase 4 adds ``_handle_control``: COMMAND/pause and COMMAND/resume from
``Controller`` (``cmd_s3_s1_pause``/``cmd_s3_s1_resume``, reserved since Phase 1
but unconsumed until now). Every concrete S1 ``handle()`` calls it first and
returns early on a hit, or a pause directive would silently drop -- ``Agent``
already implements ``pause()``/``resume()`` (clearing/setting the event that
gates the receive loop); nothing called them before this.
"""

from __future__ import annotations

from typing import Any

from viable_agents.kernel.address import RoleAddress
from viable_agents.kernel.agent import Agent
from viable_agents.kernel.channels import Channel, Intent, Role
from viable_agents.kernel.envelope import Envelope
from viable_agents.kernel.payload import Payload
from viable_agents.systems.s2.ledger import ClaimDecision, ClaimKey, WorkClaim, WorkRelease


def s3_seat(fleet_scope: tuple[str, ...]) -> RoleAddress:
    return RoleAddress(scope=fleet_scope, role=Role.S3)


def s2_seat(fleet_scope: tuple[str, ...]) -> RoleAddress:
    return RoleAddress(scope=fleet_scope, role=Role.S2)


class S1Worker(Agent):
    """Adds the two outbound moves every S1 in this fleet makes: reporting to S3
    and, since Phase 3, claiming work through S2 before acting on it."""

    def __init__(self, *, fleet_scope: tuple[str, ...], **kw: Any) -> None:
        super().__init__(**kw)
        self.fleet_scope = fleet_scope
        self._pending_claims: dict[tuple[ClaimKey, str], Envelope] = {}

    async def _handle_control(self, env: Envelope) -> bool:
        """COMMAND/pause and COMMAND/resume from Controller. Returns True if this
        envelope was a control directive (the caller should not process it further)."""
        if env.channel is not Channel.COMMAND:
            return False
        if env.intent is Intent.PAUSE:
            await self.pause()
            return True
        if env.intent is Intent.RESUME:
            await self.resume()
            return True
        return False

    async def report(self, payload: Payload, *, causation: Envelope) -> None:
        await self.emit(
            Envelope(
                run_id=causation.run_id,
                correlation_id=causation.correlation_id,
                causation_id=causation.id,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
                sender=self.address,
                recipient=s3_seat(self.fleet_scope),
                channel=Channel.COMMAND,
                intent=Intent.ACCOUNTABILITY,
                payload=payload,
            )
        )

    async def claim(self, key: ClaimKey, work_type: str, *, causation: Envelope) -> None:
        """Ask S2 to own ``key``. Stashes ``causation`` for ``resume_claim`` to pick
        up when the ARBITRATE reply arrives as a later, independent ``handle()`` call."""
        self._pending_claims[(key, work_type)] = causation
        await self.emit(
            Envelope(
                run_id=causation.run_id,
                correlation_id=causation.correlation_id,
                causation_id=causation.id,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
                sender=self.address,
                recipient=s2_seat(self.fleet_scope),
                channel=Channel.COORDINATION,
                intent=Intent.CLAIM,
                payload=WorkClaim(key=key, work_type=work_type),
            )
        )

    def resume_claim(self, decision: ClaimDecision) -> Envelope | None:
        """Pop the causation envelope stashed by ``claim``. ``None`` if the claim was
        denied, or if this reply does not match anything currently pending (already
        resolved, or a stale reply from a past run)."""
        causation = self._pending_claims.pop((decision.key, decision.work_type), None)
        if causation is None or not decision.granted:
            return None
        return causation

    async def release(self, key: ClaimKey, work_type: str, *, causation: Envelope) -> None:
        await self.emit(
            Envelope(
                run_id=causation.run_id,
                correlation_id=causation.correlation_id,
                causation_id=causation.id,
                ts_wall=self.clock.wall(),
                ts_sim=self.clock.now(),
                sender=self.address,
                recipient=s2_seat(self.fleet_scope),
                channel=Channel.COORDINATION,
                intent=Intent.RELEASE,
                payload=WorkRelease(key=key, work_type=work_type),
            )
        )
