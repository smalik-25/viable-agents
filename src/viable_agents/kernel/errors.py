"""Kernel exception hierarchy.

Every failure the bus records maps to one class, and every class maps to a
distinct persisted ``reject_reason`` so Phase 6 detectors can key on it. Catching
``KernelError`` is never how an agent implements ordinary control flow.
"""

from __future__ import annotations

import uuid

from viable_agents.kernel.channels import Channel, Intent, Role


class KernelError(Exception):
    """Root of every kernel-raised error."""


class TopologyViolationError(KernelError):
    """Hard rule 1: a send the routing matrix forbids.

    Raised synchronously to the sender. The bus records the attempt with the
    deciding rule id before raising, so a refusal is data, not only an exception.
    """

    def __init__(
        self,
        *,
        sender: Role,
        recipient: Role,
        channel: Channel,
        reason: str,
        rule_id: str,
        intent: Intent | None = None,
        envelope_id: uuid.UUID | None = None,
    ) -> None:
        self.sender = sender
        self.recipient = recipient
        self.channel = channel
        self.intent = intent
        self.reason = reason
        self.rule_id = rule_id
        self.envelope_id = envelope_id
        shown = intent.value if intent is not None else "*"
        super().__init__(
            f"{sender.value} may not send {shown} to {recipient.value} "
            f"on {channel.value}: {reason} [{rule_id}]"
        )


class ClosureViolationError(KernelError):
    """Recursion closure: a send to a non-sibling, non-parent, non-child address."""


class UnknownRecipientError(KernelError):
    """A recipient address or seat resolved to no registered inbox."""


class ChannelSaturatedError(KernelError):
    """Beer's Principle 2: channel capacity exceeded. Recorded, never swallowed."""

    def __init__(self, channel: Channel, maxsize: int, waited: float) -> None:
        self.channel = channel
        self.maxsize = maxsize
        self.waited = waited
        super().__init__(f"{channel.value} inbox full (maxsize={maxsize}) after {waited:.3f}s")


class BudgetExceededError(KernelError):
    """Phase 4 raises this from the pre-send seam. Defined now so the seam is typed."""


class BusClosedError(KernelError):
    """A send attempted after the drain began. Excluded from the agent-degraded path."""
