"""Clocks that stop while the computer sleeps.

A laptop that sleeps in the middle of a request freezes Hermes and the local model server
together. Timers measured on the wall clock or on Python's ``time.monotonic`` on Windows keep
counting through the sleep, so every stall watchdog fires the moment the machine wakes and kills
work that was about to continue. Watchdogs that judge "no progress" use these clocks instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable

_SLEEP_THRESHOLD_S = 5.0


#: Seconds the machine has spent awake, from an arbitrary start.
awake_seconds: Callable[[], float] = time.monotonic


class SleepWatch:
    """Reports how long this process was stopped since the last check, for a caller that checks
    every ``interval`` seconds: the wall time past one interval. That covers a machine that slept
    and a process that Modern Standby froze, without relying on how any clock treats either.
    Gaps under five seconds (clock sync, scheduling noise) don't count.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._wall = time.time()

    def slept(self) -> float:
        """Seconds stopped since the previous call, or 0.0."""
        wall = time.time()
        stopped, self._wall = wall - self._wall - self._interval, wall
        return stopped if stopped >= _SLEEP_THRESHOLD_S else 0.0
