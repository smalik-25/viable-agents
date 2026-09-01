"""Shared plumbing for the three S1 worker types.

``S1Worker`` is not a new abstraction over ``Agent``: it is the same
causally-linked envelope construction ``demo.py``'s stubs use, lifted out once
three concrete agents need it instead of one. Every S1 report travels COMMAND
with intent ACCOUNTABILITY to the S3 seat -- the resource bargain closing, per
``config/topology/vsm.yaml``'s ``cmd_s1_s3_account`` rule -- never as an
INTERVENTION or ALLOCATION, which S1 is not authorized to send.
"""

from __future__ import annotations

from typing import Any

from viable_agents.kernel.address import RoleAddress
from viable_agents.kernel.agent import Agent
from viable_agents.kernel.channels import Channel, Intent, Role
from viable_agents.kernel.envelope import Envelope
from viable_agents.kernel.payload import Payload


def s3_seat(fleet_scope: tuple[str, ...]) -> RoleAddress:
    return RoleAddress(scope=fleet_scope, role=Role.S3)


class S1Worker(Agent):
    """Adds the one outbound move every S1 in this fleet makes: reporting to S3."""

    def __init__(self, *, fleet_scope: tuple[str, ...], **kw: Any) -> None:
        super().__init__(**kw)
        self.fleet_scope = fleet_scope

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
