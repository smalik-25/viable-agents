"""The asyncio router. Enforces the topology, records every attempt, drains in order.

The send path is: closed-check, closure-check (recursion safety), topology
authorization, the pre-send seam, resolve and fan out, enqueue under the channel's
overflow policy, then the observer taps. Three seams are named now with fixed
signatures so Phase 6 (monitors) attaches without editing this file. A refused
send is recorded with its deciding rule id before the exception is raised,
because in this project a refusal is a research output, not only an error.

Phase 4's per-agent budget cap is NOT a pre-send hook, despite an earlier version
of this comment claiming otherwise: an LLM call is ``self.llm.complete(...)``,
called directly by an agent, and never crosses ``Bus.send()`` at all, so a
bus-level seam cannot see it. Budget enforcement lives in ``llm/budget.py``'s
``BudgetGuard``, consulted by the LLM clients themselves. ``add_pre_send``
remains free for a genuine per-envelope veto if a future phase needs one.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from viable_agents.kernel.address import AgentAddress, Recipient, is_addressable
from viable_agents.kernel.channels import Channel
from viable_agents.kernel.clock import Clock
from viable_agents.kernel.envelope import Envelope
from viable_agents.kernel.errors import (
    BusClosedError,
    ChannelSaturatedError,
    ClosureViolationError,
    TopologyViolationError,
    UnknownRecipientError,
)
from viable_agents.kernel.topology import OverflowPolicy, Topology


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    REJECTED = "rejected"
    DROPPED = "dropped"


@runtime_checkable
class Sink(Protocol):
    """Persistence seam. Postgres in production, in-memory in tests. Never optional."""

    async def record_send(
        self,
        env: Envelope,
        status: DeliveryStatus,
        reason: str | None,
        rule_id: str | None,
    ) -> None: ...

    async def record_saturation(
        self, channel: Channel, blocked_seconds: float, dropped: int
    ) -> None: ...


# The three named seams. Signatures fixed now so future phases add no edits here.
PreSend = Callable[[Envelope], Awaitable[None]]  # raise to veto an envelope send
PostDeliver = Callable[[Envelope], Awaitable[None]]  # observe only
Tap = Callable[[Envelope], None]  # synchronous, must not block or raise


class Bus:
    def __init__(
        self,
        *,
        topology: Topology,
        clock: Clock,
        sink: Sink,
        run_id: uuid.UUID,
    ) -> None:
        self._topology = topology
        self._clock = clock
        self._sink = sink
        self._run_id = run_id
        self._inboxes: dict[str, asyncio.Queue[Envelope]] = {}
        self._roles: dict[tuple[tuple[str, ...], object], list[AgentAddress]] = {}
        self._pre_send: list[PreSend] = []
        self._post_deliver: list[PostDeliver] = []
        self._taps: list[tuple[Callable[[Envelope], bool], Tap]] = []
        self._closed = False

    @property
    def topology(self) -> Topology:
        return self._topology

    def register(self, address: AgentAddress, *, maxsize: int = 0) -> None:
        """Create the agent's inbox. maxsize 0 is unbounded (the asyncio default)."""
        key = address.canonical()
        if key not in self._inboxes:
            self._inboxes[key] = asyncio.Queue(maxsize=maxsize)
        self._roles.setdefault((address.parent, address.role), []).append(address)

    # seams
    def add_pre_send(self, hook: PreSend) -> None:
        """Register a veto hook. Raising here rejects the envelope before it is
        enqueued. Not where the Phase 4 budget cap lives -- see the module
        docstring; an LLM call never reaches this seam."""
        self._pre_send.append(hook)

    def add_post_deliver(self, hook: PostDeliver) -> None:
        self._post_deliver.append(hook)

    def subscribe(self, predicate: Callable[[Envelope], bool]) -> AsyncIterator[Envelope]:
        """An observer that is not an addressee. Phase 6 monitors and S3* use this,

        so detectors sit inside the topology layer rather than reading Postgres out
        of band, which is what keeps "VSM minus algedonic" a pure config change.
        """
        queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=1024)

        def _tap(env: Envelope) -> None:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(env)

        self._taps.append((predicate, _tap))

        async def _iter() -> AsyncIterator[Envelope]:
            while True:
                yield await queue.get()

        return _iter()

    def _resolve(self, recipient: Recipient) -> list[AgentAddress]:
        if isinstance(recipient, AgentAddress):
            if recipient.canonical() not in self._inboxes:
                raise UnknownRecipientError(str(recipient))
            return [recipient]
        found = self._roles.get((recipient.scope, recipient.role), [])
        if not found:
            raise UnknownRecipientError(str(recipient))
        return list(found)

    async def send(self, env: Envelope) -> None:
        if self._closed:
            raise BusClosedError(str(env.id))

        if not is_addressable(env.sender, env.recipient, env.channel):
            await self._sink.record_send(env, DeliveryStatus.REJECTED, "closure", None)
            msg = f"{env.sender} -> {env.recipient}"
            raise ClosureViolationError(msg)

        recipient_role = env.recipient.role
        decision = self._topology.decide(env.sender.role, recipient_role, env.channel, env.intent)
        if not decision.allowed:
            await self._sink.record_send(
                env, DeliveryStatus.REJECTED, decision.reason, decision.rule_id
            )
            raise TopologyViolationError(
                sender=env.sender.role,
                recipient=recipient_role,
                channel=env.channel,
                intent=env.intent,
                reason=decision.reason,
                rule_id=decision.rule_id,
                envelope_id=env.id,
            )

        for hook in self._pre_send:
            await hook(env)

        targets = self._resolve(env.recipient)
        fanout = env.model_copy(update={"fanout_id": env.id}) if len(targets) > 1 else env
        spec = self._topology.channels[env.channel]
        for target in targets:
            await self._enqueue(fanout, target, spec.overflow, spec.block_timeout_seconds)

        for predicate, tap in self._taps:
            if predicate(env):
                tap(env)

    async def _enqueue(
        self,
        env: Envelope,
        target: AgentAddress,
        policy: OverflowPolicy,
        timeout: float,  # noqa: ASYNC109 - a per-channel capacity bound, not an ambient deadline
    ) -> None:
        queue = self._inboxes[target.canonical()]
        started = self._clock.wall()

        if policy is OverflowPolicy.BLOCK:
            try:
                await asyncio.wait_for(queue.put(env), timeout=timeout)
            except TimeoutError:
                waited = (self._clock.wall() - started).total_seconds()
                await self._sink.record_send(env, DeliveryStatus.DROPPED, "saturated", None)
                await self._sink.record_saturation(env.channel, waited, 1)
                raise ChannelSaturatedError(env.channel, queue.maxsize, waited) from None
        elif policy is OverflowPolicy.REJECT:
            try:
                queue.put_nowait(env)
            except asyncio.QueueFull:
                await self._sink.record_send(env, DeliveryStatus.REJECTED, "full", None)
                raise ChannelSaturatedError(env.channel, queue.maxsize, 0.0) from None
        elif policy is OverflowPolicy.DROP_OLDEST:
            while queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    evicted = queue.get_nowait()
                    await self._sink.record_send(evicted, DeliveryStatus.DROPPED, "evicted", None)
                    await self._sink.record_saturation(env.channel, 0.0, 1)
            queue.put_nowait(env)
        else:  # DROP_NEWEST
            if queue.full():
                await self._sink.record_send(env, DeliveryStatus.DROPPED, "full", None)
                await self._sink.record_saturation(env.channel, 0.0, 1)
                return
            queue.put_nowait(env)

        blocked = (self._clock.wall() - started).total_seconds()
        await self._sink.record_send(env, DeliveryStatus.DELIVERED, None, None)
        if blocked > 0.0:
            await self._sink.record_saturation(env.channel, blocked, 0)
        for hook in self._post_deliver:
            await hook(env)

    async def receive(self, address: AgentAddress) -> Envelope:
        """FIFO per recipient. Ordering is a stated guarantee; Phase 3 depends on it."""
        return await self._inboxes[address.canonical()].get()

    def inbox_depth(self, address: AgentAddress) -> int:
        return self._inboxes[address.canonical()].qsize()

    async def drain(
        self,
        stages: list[list[AgentAddress]],
        timeout: float,  # noqa: ASYNC109 - a per-stage drain bound, not an ambient deadline
    ) -> bool:
        """Ordered drain (sources, S1, S2/S3, S4/S5), never cancel-all.

        Cancel-all produces nondeterministic tail message counts, which silently
        breaks the Phase 9 "reproducible from seeds" claim. Returns False if any
        stage failed to empty within the timeout, which the caller must treat as an
        aborted run, not a completed one.
        """
        self._closed = True
        for stage in stages:
            try:
                async with asyncio.timeout(timeout):
                    while any(not self._inboxes[a.canonical()].empty() for a in stage):
                        await self._clock.sleep(0.001)
            except TimeoutError:
                return False
        return True
