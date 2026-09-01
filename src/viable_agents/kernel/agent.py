"""The Agent base: receive, think, act, emit. Pure-code and LLM agents share it.

An S2 coordinator simply never touches ``self.llm``: nothing in the base assumes a
model call, so a pure-code agent pays no token cost and still gets identical
tracing, persistence and topology enforcement. The error policy is
degraded-not-fatal, because Phase 6's silence detector can only fire on a real run
if a dead agent stays dead and observable rather than taking the fleet down.
``asyncio.CancelledError`` is re-raised before the broad handler (cancellation is
never an agent fault), and ``BusClosedError`` is excluded too, so a clean drain does not
leave a spurious DEGRADED agent that would contaminate a Phase 9 metric.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
from enum import StrEnum

from viable_agents.kernel.address import AgentAddress
from viable_agents.kernel.bus import Bus
from viable_agents.kernel.clock import Clock
from viable_agents.kernel.envelope import Envelope
from viable_agents.kernel.errors import BusClosedError
from viable_agents.kernel.llm import LLMClient
from viable_agents.kernel.tracing import Tracer


class AgentState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DEGRADED = "degraded"
    STOPPED = "stopped"


class Agent(abc.ABC):
    """Subclasses implement ``handle()``. The loop, lifecycle and error policy live here."""

    def __init__(
        self,
        *,
        address: AgentAddress,
        bus: Bus,
        clock: Clock,
        tracer: Tracer,
        llm: LLMClient | None = None,
        model_tier: str | None = None,
        charter_version: str | None = None,
    ) -> None:
        self.address = address
        self.bus = bus
        self.clock = clock
        self.tracer = tracer
        self.llm = llm
        self.model_tier = model_tier  # resolved from config/models.yaml by (role, path)
        self.charter_version = charter_version
        self.state = AgentState.IDLE
        self._resume = asyncio.Event()
        self._resume.set()
        self._task: asyncio.Task[None] | None = None

    @abc.abstractmethod
    async def handle(self, env: Envelope) -> None:
        """Process one envelope. Emit via ``self.emit()``, never ``bus.send()`` directly."""

    async def emit(self, env: Envelope) -> None:
        """The only sanctioned way to send, so the tracing and topology seams hold."""
        await self.bus.send(env)

    def start(self) -> None:
        self.state = AgentState.RUNNING
        self._task = asyncio.create_task(self._run(), name=self.address.canonical())

    async def pause(self) -> None:
        self.state = AgentState.PAUSED
        self._resume.clear()

    async def resume(self) -> None:
        self.state = AgentState.RUNNING
        self._resume.set()

    async def stop(self) -> None:
        self.state = AgentState.STOPPED
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while self.state is not AgentState.STOPPED:
            await self._resume.wait()  # S3 pause/resume, without a busy loop
            # No except around receive: CancelledError is a BaseException, so it
            # propagates out of the loop on stop() without any handler here.
            env = await self.bus.receive(self.address)
            attrs = {
                "vsm_role": self.address.role.value,
                "agent": self.address.canonical(),
            }
            try:
                with self.tracer.turn(name=f"{self.address.canonical()}.turn", attrs=attrs):
                    await self.handle(env)
            except BusClosedError:
                return  # the drain closed the bus mid-handle; not a degraded state
            except Exception:
                # An agent fault is data, not a crash: degrade and keep the fleet
                # observable. CancelledError is BaseException, not Exception, so a
                # stop() during handle() still propagates rather than degrading.
                self.state = AgentState.DEGRADED
