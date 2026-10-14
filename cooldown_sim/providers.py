"""Value providers: what each emulated instrument channel reads *now*.

``ReplayProvider`` serves the past record through the channel map at the sim
clock's current time, plus a tiny seeded noise floor so a paused clock never
produces bit-identical readings (which the live stale-data watchdog would
treat as a dead sensor).  The noise is far below every archiver deadband
(default 2e-5 relative vs 1e-3).

Overrides let a scenario or Sim Control replace a channel with a function of
sim time (quench ramps, out-of-range values) without touching the record.

Every value served is logged to ``served.csv`` (wall, sim, instrument,
channel, value) -- the ground truth the round-trip validator compares the
Genesis PVs and archive against.
"""

from __future__ import annotations

import csv
import math
import random
import threading
import time
from pathlib import Path
from typing import Callable

from .channel_map import ChannelMap
from .clock import SimClock
from .data.timeline import Timeline

Override = Callable[[float], float]


class ServedLog:
    def __init__(self, path: Path | None):
        self._lock = threading.Lock()
        self._fh = open(path, "w", newline="", encoding="utf-8") if path else None
        self._writer = csv.writer(self._fh) if self._fh else None
        if self._writer:
            self._writer.writerow(["wall", "sim", "instrument", "channel", "value"])
        self._n = 0

    def write(self, wall, sim, instrument, channel, value) -> None:
        if not self._writer:
            return
        with self._lock:
            self._writer.writerow([f"{wall:.3f}", f"{sim:.3f}", instrument, channel, repr(float(value))])
            self._n += 1
            if self._n % 200 == 0:
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()
                self._fh.close()
                self._fh = None
                self._writer = None


class ReplayProvider:
    def __init__(self, timeline: Timeline, cmap: ChannelMap, clock: SimClock, *,
                 noise_rel: float = 2e-5, seed: int = 0, served: ServedLog | None = None):
        self.timeline = timeline
        self.cmap = cmap
        self.clock = clock
        self.noise_rel = float(noise_rel)
        self._rng = random.Random(seed)
        self._rng_lock = threading.Lock()
        self.served = served or ServedLog(None)
        self.overrides: dict[tuple[str, str], Override] = {}

    def recorded(self, instrument: str, channel: str, t: float) -> float:
        """The record (or override) at sim time ``t``, without noise."""
        fn = self.overrides.get((instrument, channel))
        if fn is not None:
            return float(fn(t))
        return self.cmap.channel(instrument, channel).recorded(self.timeline, t)

    def value(self, instrument: str, channel: str) -> float:
        t = self.clock.now()
        v = self.recorded(instrument, channel, t)
        if math.isfinite(v) and self.noise_rel and v != 0.0:
            with self._rng_lock:
                v *= 1.0 + self.noise_rel * self._rng.gauss(0.0, 1.0)
        self.served.write(time.time(), t, instrument, channel, v)
        return v

    def source_for(self, instrument: str) -> Callable[[str], float]:
        return lambda channel: self.value(instrument, channel)
