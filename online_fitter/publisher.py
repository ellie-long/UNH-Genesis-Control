"""Broadcast full fit results (the fitted lineshape + components + P/Q) to other software.

A loopback-only TCP *publisher*: any number of subscriber programs connect to
127.0.0.1:<publish port> and receive one newline-delimited JSON (NDJSON) line per fitted
sweep. Each line carries the frequency axis, the processed (cubic-subtracted) data, the
total fit, and the four component curves -- I+/I- for the primary and secondary bonds --
plus the P/Q context. Any language that can read a TCP line stream can consume it.

Design notes:
  * Loopback only (127.0.0.1), like the fit server -- it never touches the lab LAN.
  * Publish is fire-and-forget and NON-BLOCKING: each subscriber socket has a short send
    timeout, and any subscriber that errors or can't keep up is dropped, so a slow consumer
    can never stall the fitter or the acquisition.
  * NaN (e.g. a component that is all-NaN in Baseline/No-NMR modes) is emitted as JSON null.
"""
import json
import socket
import threading

import numpy as np

DEFAULT_PUBLISH_PORT = 8778
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_SEND_TIMEOUT = 0.25          # seconds; drop a subscriber that blocks longer than this


def _clean(arr):
    """ndarray -> list of floats with non-finite values as None (valid JSON)."""
    if arr is None:
        return None
    a = np.asarray(arr, float)
    return [None if not np.isfinite(x) else round(float(x), 8) for x in a]


def _num(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 4)


def _g(x):
    """Constants need more precision than the %-scaled P/Q values -> round to 8 places."""
    return None if x is None or not np.isfinite(x) else round(float(x), 8)


class Publisher:
    """Loopback TCP fan-out of fit results. Start it, then call ``publish(...)`` per sweep."""

    def __init__(self, host="127.0.0.1", port=DEFAULT_PUBLISH_PORT):
        self.host = host
        self.port = port
        self._subs = set()
        self._lock = threading.Lock()
        self._srv = None

    def start(self):
        if self.host not in _LOOPBACK:
            raise ValueError(f"publisher must bind loopback only, not {self.host!r} "
                             f"(the lab LAN carries the VME crate / RF switches)")
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        threading.Thread(target=self._accept_loop, name="fit-publisher", daemon=True).start()
        return self

    def _accept_loop(self):
        while True:
            try:
                conn, _addr = self._srv.accept()
            except OSError:
                break
            conn.settimeout(_SEND_TIMEOUT)
            with self._lock:
                self._subs.add(conn)

    def publish(self, result, ts=None, freqs=None, raw=None, constants=None, baseline_sub=None):
        """Broadcast one fitted sweep to all subscribers. No-op if nobody is subscribed. Fields, in
        the streamed order:
          1. good fit constants  -- ``constants`` (engine.constants_snapshot): CC, CC_DAQ, lineshape
             constants; ``constants_good`` is True once calibrated (Update Constants / import).
          2. processed NMR data  -- ``data`` (cubic-subtracted line).
          3. processed NMR fit   -- ``total_fit`` + I+/I- primary & secondary (secondaries are all 0
             for single-bond ND3).
          4. raw NMR             -- ``raw`` (the sweep as received from LabVIEW).
        ``freqs`` is the shared frequency axis (MHz)."""
        with self._lock:
            if not self._subs:
                return
        freqs_arr = freqs if freqs is not None else result.get("freqs")
        n = 0 if freqs_arr is None else int(np.asarray(freqs_arr).size)
        csnap = constants or {}
        model = csnap.get("model")
        good = csnap.get("source") in ("fit", "imported")
        const_block = None
        if csnap:
            const_block = {"source": csnap.get("source"), "material": csnap.get("material"),
                           "model": model, "CC": _g(csnap.get("CC")), "CC_DAQ": _g(csnap.get("CC_DAQ")),
                           "red_chisq": _num(csnap.get("redchi"))}
            for k, val in (csnap.get("constants") or {}).items():   # omegD, omegQ.., A.., eta.., K, scale, phase, r
                const_block[k] = _g(val)
        # Secondaries: force all-zeros for single-bond ND3 (it has no O-D bond).
        sec_p, sec_m = result.get("cSp"), result.get("cSm")
        if model == "single":
            sec_p = np.zeros(n)
            sec_m = np.zeros(n)
        msg = {
            "type": "fit", "ts": ts,
            "fit_type": result.get("fit_type"), "mode": result.get("mode"),
            "material": csnap.get("material"), "model": model, "npts": n,
            "freqs_mhz": _clean(freqs_arr),
            # current-sweep polarizations (context)
            "P_area": _num(result.get("P_area")), "P_fit": _num(result.get("P_fit")),
            "Q_eq": _num(result.get("Q_eq")), "Q_fit": _num(result.get("Q_fit")),
            "r": _num(result.get("r")), "phase_deg": _num(result.get("phase_deg")),
            "red_chisq": _num(result.get("red_chisq")),
            # 1. good fit constants
            "constants_good": good,
            "constants": const_block,
            # 2. processed NMR data
            "data": _clean(result.get("flat")),
            # 3. processed NMR fit (total + components; ND3 secondaries all 0)
            "total_fit": _clean(result.get("fit")),
            "I_plus_primary": _clean(result.get("cPp")),
            "I_minus_primary": _clean(result.get("cPm")),
            "I_plus_secondary": _clean(sec_p),
            "I_minus_secondary": _clean(sec_m),
            # 4. raw NMR
            "raw": _clean(raw),
            "baseline_sub": _clean(baseline_sub) if baseline_sub is not None else [],
        }
        line = (json.dumps(msg) + "\r\n").encode("utf-8")
        dead = []
        with self._lock:
            subs = list(self._subs)
        for c in subs:
            try:
                c.sendall(line)
            except OSError:                          # slow/broken subscriber -> drop it
                dead.append(c)
        if dead:
            with self._lock:
                for c in dead:
                    self._subs.discard(c)
            for c in dead:
                try:
                    c.close()
                except OSError:
                    pass

    def stop(self):
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        with self._lock:
            for c in self._subs:
                try:
                    c.close()
                except OSError:
                    pass
            self._subs.clear()
