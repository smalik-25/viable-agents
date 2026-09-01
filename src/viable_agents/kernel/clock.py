"""Time is injected, never read ambiently.

Three things downstream depend on time being an abstraction: deterministic seeded
runs (hard rule 10), Phase 6's escalation timers, and Phase 9's time-to-detection
reproducing from seeds. ``RealClock`` is the only place in ``kernel/`` allowed to
call ``datetime.now`` or ``asyncio.sleep``; ``tests/unit/test_kernel_imports.py``
greps for either outside this file.

The ``now()`` / ``wall()`` split is why every envelope and every row carries both
``ts_sim`` and ``ts_wall``: virtual time cannot compress real model latency, so
Phase 9 reports simulated and wall-clock duration as separate metrics.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import heapq
import itertools
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class Handle(Protocol):
    def cancel(self) -> None: ...

    @property
    def cancelled(self) -> bool: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> dt.datetime:
        """Simulated time. Recorded on every ``ts_sim`` field."""

    def wall(self) -> dt.datetime:
        """Real time. Recorded on every ``ts_wall`` field."""

    async def sleep(self, seconds: float) -> None: ...

    def call_later(self, seconds: float, cb: Callable[[], None]) -> Handle: ...


class _RealHandle:
    def __init__(self, th: asyncio.TimerHandle) -> None:
        self._th = th
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self._th.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class RealClock:
    """Wraps asyncio. Simulated and wall time are identical here."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def wall(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def call_later(self, seconds: float, cb: Callable[[], None]) -> Handle:
        return _RealHandle(asyncio.get_running_loop().call_later(seconds, cb))


class _VirtualHandle:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class VirtualClock:
    """Event-driven clock: jumps to the next scheduled deadline on demand.

    Phase 6 timer tests run in microseconds and Phase 9 time-to-detection is
    machine-independent. The runtime drives ``advance_to_next`` only when every
    agent is blocked on ``receive()``, which is what makes dispatch order, and
    therefore the whole run, deterministic.
    """

    def __init__(self, start: dt.datetime, *, wall: Callable[[], dt.datetime]) -> None:
        self._t = 0.0
        self._start = start
        self._wall = wall
        self._q: list[tuple[float, int, Callable[[], None], _VirtualHandle]] = []
        self._seq = itertools.count()

    def now(self) -> dt.datetime:
        return self._start + dt.timedelta(seconds=self._t)

    def wall(self) -> dt.datetime:
        return self._wall()

    def call_later(self, seconds: float, cb: Callable[[], None]) -> Handle:
        handle = _VirtualHandle()
        heapq.heappush(self._q, (self._t + seconds, next(self._seq), cb, handle))
        return handle

    async def sleep(self, seconds: float) -> None:
        done = asyncio.Event()
        self.call_later(seconds, done.set)
        await done.wait()

    def advance_to_next(self) -> bool:
        """Fire the next pending timer, advancing sim time to its deadline.

        Returns False when no timer is pending, which the run loop reads as
        "the fleet is quiescent".
        """
        while self._q:
            when, _, cb, handle = heapq.heappop(self._q)
            if handle.cancelled:
                continue
            self._t = max(self._t, when)
            cb()
            return True
        return False
