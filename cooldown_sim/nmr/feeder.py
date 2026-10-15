"""Feed live sweeps to the real online fitter over its unchanged loopback protocol.

Lock-step like LabVIEW's NMRPythonFit.vi: send one sweep, wait for the
fitter's reply, then the next.  Cadence is one sweep per ``SWEEP_SIM_S`` of
sim time (the DAQ's ~5 s), but never faster than one per ``MIN_WALL_S`` wall
seconds -- at high speed the fitter sees fewer sweeps per sim hour, not a
flood.  Nothing is sent while the sim clock is paused.

Every exchange is logged to ``nmr.csv`` (wall, sim, true P/Q, the fitter's
P_fit/P_area/Q_fit/fit_type/warning), and the latest P_fit is handed to the
model provider so Sim Control can show fitter-minus-truth.
"""

from __future__ import annotations

import csv
import json
import logging
import socket
import threading
import time
from pathlib import Path

from online_fitter import protocol

from ..data.records import format_epoch

log = logging.getLogger(__name__)

SWEEP_SIM_S = 5.0
MIN_WALL_S = 0.5


class NMRFeeder:
    def __init__(self, source, clock, port: int, path: Path, provider=None, events=None):
        self.source = source
        self.clock = clock
        self.port = port
        self.provider = provider
        self.events = events
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(["wall", "sim", "true_P", "true_Q", "fit_type", "P_fit", "P_area",
                          "Q_fit", "Q_eq", "warning", "baseline_note"])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nmr-feeder", daemon=True)
        self.sent = 0
        self.errors = 0
        self._last_note = ""

    def start(self) -> "NMRFeeder":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        self._fh.close()

    def _run(self) -> None:
        sock = rf = None
        last_sim = None
        while not self._stop.is_set():
            if not self.clock.running:
                time.sleep(0.2)
                continue
            t = self.clock.now()
            if last_sim is not None and 0 <= t - last_sim < SWEEP_SIM_S:
                time.sleep(0.05)
                continue
            if sock is None:
                try:
                    sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                    sock.settimeout(120)
                    rf = sock.makefile("rb")
                except OSError:
                    time.sleep(1.0)
                    continue
            wall0 = time.time()
            sw = self.source.sweep(t)
            try:
                sock.sendall(protocol.encode_sweep(sw["centfreq_mhz"], sw["span_mhz"], sw["raw"],
                                                   sw["baseline_sub"], ts=format_epoch(t)))
                line = rf.readline()
                if not line:
                    raise OSError("fitter closed the connection")
                reply = protocol.decode_message(line)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.warning("fitter exchange failed: %s", exc)
                self.errors += 1
                try:
                    sock.close()
                except OSError:
                    pass
                sock = rf = None
                time.sleep(1.0)
                continue
            self.sent += 1
            last_sim = t
            p_fit = reply.get("P_fit")
            if self.provider is not None and p_fit is not None:
                self.provider.fit_P = float(p_fit)
            if sw["baseline_note"] != self._last_note and self.events is not None:
                self._last_note = sw["baseline_note"]
                self.events.write(t, "nmr", sw["baseline_note"])
            self._w.writerow([f"{time.time():.3f}", f"{t:.3f}", f"{sw['true_P']:.4f}",
                              f"{sw['true_Q']:.4f}", reply.get("fit_type"), p_fit,
                              reply.get("P_area"), reply.get("Q_fit"), reply.get("Q_eq"),
                              reply.get("warning", ""), sw["baseline_note"]])
            if self.sent % 20 == 0:
                self._fh.flush()
            wait = MIN_WALL_S - (time.time() - wall0)
            if wait > 0:
                time.sleep(wait)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
