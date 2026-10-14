"""Simulation clock: sim time advancing at ``speed`` x wall time.

Sim time is expressed as Unix epoch seconds *of the simulated moment*, so a
replay of 2026-06-17 13:00 local reads ``SimClock.now()`` close to that date
and the data layer can index the historical record directly.

The clock supports pause, play, speed changes and seek.  Every change appends
a :class:`Checkpoint` (wall, sim, speed, running) so a wall timestamp -- the
archiver stamps rows with ``datetime.now()`` -- can be mapped back to the sim
moment it belonged to (:meth:`SimClock.wall_to_sim`).

Pattern after jlab_pynmr ``nmr_sim/clock.py``: the wall source is injectable so
tests are deterministic.
"""

from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass
from typing import Callable

#: Largest allowed speed factor.  Above this the IOC poll intervals (0.5-2 s
#: wall) skip so much sim time that interlock/alarm behaviour stops being
#: representative.
MAX_SPEED = 600.0
MIN_SPEED = 0.1


@dataclass(frozen=True)
class Checkpoint:
    """State of the clock from ``wall`` onward (until the next checkpoint)."""

    wall: float
    sim: float
    speed: float
    running: bool
    reason: str


class SimClock:
    """Thread-safe sim clock.

    Parameters
    ----------
    sim0:
        Sim time (epoch seconds) at construction.
    speed:
        Sim seconds per wall second, in ``[MIN_SPEED, MAX_SPEED]``.
    wall:
        Wall-time source returning epoch seconds; ``time.time`` by default so
        checkpoints line up with archiver timestamps.
    running:
        Start running (True) or paused.
    """

    def __init__(self, sim0: float, speed: float = 1.0, *,
                 wall: Callable[[], float] = time.time, running: bool = True):
        self._wall = wall
        self._lock = threading.Lock()
        self._speed = _checked_speed(speed)
        self._running = bool(running)
        self._ref_wall = wall()
        self._ref_sim = float(sim0)
        self._checkpoints = [Checkpoint(self._ref_wall, self._ref_sim, self._speed,
                                        self._running, "start")]

    # ------------------------------------------------------------------ read
    def now(self) -> float:
        """Current sim time (epoch seconds)."""
        with self._lock:
            return self._now_locked(self._wall())

    def _now_locked(self, wall_now: float) -> float:
        if not self._running:
            return self._ref_sim
        return self._ref_sim + self._speed * (wall_now - self._ref_wall)

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def running(self) -> bool:
        return self._running

    def checkpoints(self) -> list[Checkpoint]:
        with self._lock:
            return list(self._checkpoints)

    # --------------------------------------------------------------- control
    def _rebase(self, reason: str, *, sim: float | None = None,
                speed: float | None = None, running: bool | None = None) -> float:
        with self._lock:
            wall_now = self._wall()
            current = self._now_locked(wall_now)
            self._ref_wall = wall_now
            self._ref_sim = current if sim is None else float(sim)
            if speed is not None:
                self._speed = _checked_speed(speed)
            if running is not None:
                self._running = bool(running)
            self._checkpoints.append(Checkpoint(wall_now, self._ref_sim, self._speed,
                                                self._running, reason))
            return self._ref_sim

    def pause(self) -> float:
        return self._rebase("pause", running=False)

    def play(self) -> float:
        return self._rebase("play", running=True)

    def set_speed(self, speed: float) -> float:
        return self._rebase(f"speed {float(speed):g}", speed=speed)

    def seek(self, sim_time: float) -> float:
        """Jump to ``sim_time``.  Recorded as a discontinuity checkpoint."""
        return self._rebase("seek", sim=sim_time)

    # ---------------------------------------------------------------- mapping
    def wall_to_sim(self, wall_time: float) -> float:
        """Sim time that was current at ``wall_time`` (piecewise from checkpoints).

        Times before the first checkpoint map through the first segment.
        """
        cps = self.checkpoints()
        return wall_to_sim(cps, wall_time)


def wall_to_sim(checkpoints: list[Checkpoint], wall_time: float) -> float:
    """Piecewise mapping used by both the live clock and offline validators."""
    if not checkpoints:
        raise ValueError("no checkpoints")
    walls = [cp.wall for cp in checkpoints]
    i = max(bisect.bisect_right(walls, wall_time) - 1, 0)
    cp = checkpoints[i]
    if not cp.running:
        return cp.sim
    return cp.sim + cp.speed * (wall_time - cp.wall)


def discontinuities(checkpoints: list[Checkpoint]) -> list[Checkpoint]:
    """Checkpoints where sim time jumped (seeks).  Validators exclude a settle
    window after each, because the target model was re-initialised there."""
    return [cp for cp in checkpoints if cp.reason == "seek"]


def _checked_speed(speed: float) -> float:
    speed = float(speed)
    if not (MIN_SPEED <= speed <= MAX_SPEED):
        raise ValueError(f"speed {speed:g} outside [{MIN_SPEED:g}, {MAX_SPEED:g}]")
    return speed
