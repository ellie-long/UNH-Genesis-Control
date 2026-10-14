"""Read-only PV recorder: every monitor update of the watched PVs -> probe.csv.

The probe only subscribes (camonitor); it never writes.  It also records
connection state, so PVs that the live configuration expects but no IOC
serves show up as never connected.

Callers must configure the loopback client environment
(``epics_env.apply_client_env_here``) before importing this module, because
libca reads EPICS_CA_* once at load.
"""

from __future__ import annotations

import csv
import math
import threading
import time
from pathlib import Path

from aioca import FORMAT_TIME, camonitor


def _cell(value) -> str:
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else "nan"
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            data = value.tolist()
            if isinstance(data, list):
                return f"<array {len(data)}>"
            return repr(data)
        except Exception:      # noqa: BLE001
            pass
    return str(value)


class Probe:
    def __init__(self, pvs: list[str], path: Path):
        self.pvs = sorted(set(pvs))
        self.path = Path(path)
        self._fh = open(self.path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(["wall", "pv", "value", "severity", "connected"])
        self._lock = threading.Lock()
        self._subs = []
        self.connected: set[str] = set()
        self.ever_connected: set[str] = set()
        self.latest: dict[str, object] = {}
        self._n = 0

    def start(self) -> None:
        self._subs = camonitor(self.pvs, self._callback, format=FORMAT_TIME,
                               notify_disconnect=True, all_updates=True)

    def _callback(self, value, index) -> None:
        pv = self.pvs[index]
        wall = time.time()
        ok = getattr(value, "ok", True)
        with self._lock:
            if ok:
                self.connected.add(pv)
                self.ever_connected.add(pv)
                self.latest[pv] = value
                sev = getattr(value, "severity", 0)
                self._w.writerow([f"{wall:.3f}", pv, _cell(value), sev, 1])
            else:
                self.connected.discard(pv)
                self._w.writerow([f"{wall:.3f}", pv, "", "", 0])
            self._n += 1
            if self._n % 100 == 0:
                self._fh.flush()

    def close(self) -> None:
        for sub in self._subs:
            try:
                sub.close()
            except Exception:  # noqa: BLE001
                pass
        self._subs = []
        with self._lock:
            if not self._fh.closed:
                self._fh.flush()
                self._fh.close()

    def never_connected(self) -> list[str]:
        return sorted(set(self.pvs) - self.ever_connected)
