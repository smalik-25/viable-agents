"""The clock is the determinism contract.

The VirtualClock fires timers in simulated-time order regardless of registration
order and advances sim time exactly to each deadline, which is what makes Phase 6
timer tests fast and Phase 9 time-to-detection machine-independent.
"""

from __future__ import annotations

import datetime as dt

from viable_agents.kernel import VirtualClock

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _clock() -> VirtualClock:
    return VirtualClock(START, wall=lambda: START)


def test_timers_fire_in_sim_order_not_registration_order() -> None:
    clock = _clock()
    fired: list[str] = []
    clock.call_later(3.0, lambda: fired.append("c"))
    clock.call_later(1.0, lambda: fired.append("a"))
    clock.call_later(2.0, lambda: fired.append("b"))
    while clock.advance_to_next():
        pass
    assert fired == ["a", "b", "c"]


def test_sim_time_advances_to_each_deadline() -> None:
    clock = _clock()
    seen: list[float] = []
    clock.call_later(5.0, lambda: seen.append((clock.now() - START).total_seconds()))
    clock.call_later(2.5, lambda: seen.append((clock.now() - START).total_seconds()))
    while clock.advance_to_next():
        pass
    assert seen == [2.5, 5.0]


def test_cancelled_timers_do_not_fire() -> None:
    clock = _clock()
    fired: list[str] = []
    handle = clock.call_later(1.0, lambda: fired.append("x"))
    clock.call_later(2.0, lambda: fired.append("y"))
    handle.cancel()
    while clock.advance_to_next():
        pass
    assert fired == ["y"]


def test_advance_returns_false_when_idle() -> None:
    clock = _clock()
    assert clock.advance_to_next() is False
