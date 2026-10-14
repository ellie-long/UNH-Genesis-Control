"""SimEngine: the clock, the value provider and the emulator host for one run.

The engine is deliberately free of EPICS so it can be hosted either by the
``run`` command directly (unattended regression) or by the Sim Control IOC
(interactive rehearsal).  It records every operator-visible sim event --
start, pause, seek, speed, faults, notes -- in ``events.csv`` with both wall
and sim time, and saves the clock checkpoints so validators can map archive
timestamps back to sim time.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .channel_map import ChannelMap
from .clock import SimClock
from .data.records import format_epoch
from .data.timeline import Timeline
from .emulators.host import EmulatorHost
from .profile import GeneratedStack
from .providers import ReplayProvider, ServedLog
from .run_dir import RunDir


class EventLog:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        if path.stat().st_size == 0:
            self._w.writerow(["wall", "sim", "sim_local", "kind", "detail"])

    def write(self, sim: float, kind: str, detail: str = "") -> None:
        with self._lock:
            self._w.writerow([f"{time.time():.3f}", f"{sim:.3f}", format_epoch(sim), kind, detail])
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class SimEngine:
    def __init__(self, run: RunDir, stack: GeneratedStack, timeline: Timeline, cmap: ChannelMap,
                 clock: SimClock, *, noise_rel: float = 2e-5, seed: int = 0,
                 supply_initial: dict | None = None, provider=None):
        self.run = run
        self.stack = stack
        self.timeline = timeline
        self.cmap = cmap
        self.clock = clock
        self.served = ServedLog(run.path / "served.csv")
        self.provider = provider or ReplayProvider(timeline, cmap, clock, noise_rel=noise_rel,
                                                   seed=seed, served=self.served)
        if provider is not None:
            provider.served = self.served
        self.host = EmulatorHost(stack, self.provider, cmap, timeline, supply_initial)
        self.events = EventLog(run.path / "events.csv")

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.host.start()
        for name, h in self.host.hosted.items():
            for note in h.notes:
                self.events.write(self.clock.now(), "instrument", f"{name}: {note}")
        self.events.write(self.clock.now(), "engine", "emulators up")

    def stop(self) -> None:
        self.host.stop()
        self.served.close()
        self.save_clock()
        self.events.write(self.clock.now(), "engine", "stopped")
        self.events.close()

    def save_clock(self) -> Path:
        path = self.run.sub("state") / "clock.json"
        path.write_text(json.dumps([asdict(c) for c in self.clock.checkpoints()], indent=2),
                        encoding="utf-8")
        return path

    # ------------------------------------------------------------- control
    def play(self):
        self.events.write(self.clock.play(), "clock", "play")

    def pause(self):
        self.events.write(self.clock.pause(), "clock", "pause")

    def set_speed(self, speed: float):
        self.events.write(self.clock.set_speed(speed), "clock", f"speed {speed:g}")

    def seek(self, sim_time: float):
        self.events.write(self.clock.seek(sim_time), "clock", f"seek {format_epoch(sim_time)}")

    def set_fault(self, instrument: str, mode: str) -> None:
        self.host.set_fault(instrument, mode)
        self.events.write(self.clock.now(), "fault", f"{instrument} {mode}")

    def note(self, text: str) -> None:
        self.events.write(self.clock.now(), "note", text)
