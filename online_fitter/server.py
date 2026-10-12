"""Loopback-only TCP server that receives NMR sweeps from LabVIEW and fits them.

NETWORK SAFETY: this server binds to 127.0.0.1 ONLY. It never listens on a LAN
interface, so it cannot interfere with the VME crate or the networked RF switches
(which live on the lab LAN). ``make_server`` asserts the bind address is loopback
and refuses to start otherwise.

Each client connection is a stream of newline-delimited JSON messages (see
protocol.py). For every "sweep" message the server:
  1. reconstructs the freq axis + raw/baseline-subtracted arrays,
  2. reads the operator's current MODE from the shared state (set in the GUI),
  3. fits via ``FitEngine.fit_line``,
  4. pushes a display update onto ``state.result_queue`` (consumed by the GUI), and
  5. replies to the client with a compact P/Q result (LabVIEW may log it).

The fit itself is serialized inside FitEngine (its own lock), so multiple client
connections are safe though normally there is just one (the VME Loop).
"""
import socketserver
import threading
import queue
import time
import json
import logging
import socket

import numpy as np

from . import protocol
from . import engine as engine_mod

log = logging.getLogger("online_fitter.server")

_REPLY_TIMEOUT = 1.0     # seconds to wait to send the optional P/Q reply before DROPPING it (never block)


class SharedState:
    """State shared between the GUI (main thread) and the server (background thread).

    The GUI sets the fitting ``mode`` and owns the ``engine``; the server reads the
    mode and calls the engine. Results flow back to the GUI through ``result_queue``.
    """

    def __init__(self, engine, mode=engine_mod.MODE_BASELINE, queue_max=200, publisher=None):
        self.engine = engine
        self.result_queue = queue.Queue(maxsize=queue_max)
        self.publisher = publisher      # optional Publisher: fan-out full fit results to other software
        self._mode = mode
        self._lock = threading.Lock()
        self.n_received = 0

    def set_mode(self, mode):
        if mode not in engine_mod.ALL_MODES:
            raise ValueError(f"unknown mode {mode!r}")
        with self._lock:
            self._mode = mode

    def get_mode(self):
        with self._lock:
            return self._mode

    def push_result(self, item):
        """Enqueue a display update, dropping the oldest if the GUI has fallen behind
        (live display should never block the acquisition socket)."""
        try:
            self.result_queue.put_nowait(item)
        except queue.Full:
            try:
                self.result_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.result_queue.put_nowait(item)
            except queue.Full:
                pass


def _handle_sweep(state, msg):
    """Fit one decoded 'sweep' message; return the compact reply dict and enqueue the
    full display update. Never raises -- errors come back as {"ok": False, ...}."""
    freqs, raw, baseline_sub = protocol.sweep_arrays(msg)
    span = float(msg["span_mhz"])
    centfreq = float(msg["centfreq_mhz"])
    mode = state.get_mode()
    result = state.engine.fit_line(freqs, baseline_sub, mode, span=span, centfreq=centfreq)
    state.n_received += 1
    # Full update for the GUI (arrays kept as ndarrays; the GUI runs in-process).
    state.push_result({
        "type": "sweep_result",
        "ts": msg.get("ts"),
        "recv_time": time.time(),      # computer wall-clock time this line was grabbed + fit (strip x-axis)
        "mode": mode,
        "freqs": freqs,
        "raw": raw,
        "baseline_sub": baseline_sub,  # LabVIEW's baseline-subtracted line (for the left-panel middle view)
        "result": result,
    })
    if state.publisher is not None:    # fan out the constants + processed data + fit + raw to other software
        state.publisher.publish(result, ts=msg.get("ts"), freqs=freqs, raw=raw,
                                constants=state.engine.constants_snapshot())

    def num(x):
        return None if x is None or not np.isfinite(x) else round(float(x), 4)

    reply = {"ok": True, "fit_type": result.get("fit_type"),
             "P_area": num(result.get("P_area")), "Q_eq": num(result.get("Q_eq")),
             "P_fit": num(result.get("P_fit")), "Q_fit": num(result.get("Q_fit")),
             "r": num(result.get("r"))}
    if result.get("error"):
        reply["warning"] = result["error"]
    return reply


class _Handler(socketserver.StreamRequestHandler):
    """One connection = a stream of NDJSON messages. Replies one NDJSON line each."""
    # Long read timeout: the LabVIEW acquisition can legitimately pause between sweeps (mode changes,
    # slow VME cycles, RF ops). A short timeout would DROP a healthy connection during such a gap, and
    # since LabVIEW keeps writing to the now-dead refnum it never reconnects -> "fitter stopped after
    # N hours". An hour is generous for a real gap while still eventually reaping a truly dead socket.
    timeout = 3600.0

    def handle(self):
        state = self.server.state
        try:                                         # TCP keepalive: detect a truly dead peer even mid-gap
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        self._dropped_replies = 0
        log.info("LabVIEW connection OPEN from %s", self.client_address)
        if getattr(self.server, "verbose", False):
            print(f"[conn] open from {self.client_address}", flush=True)
        reason = "client ended stream"
        try:
            self._serve(state)
        except (socket.timeout, TimeoutError):
            reason = f"READ TIMEOUT ({self.timeout:.0f}s with no data -- LabVIEW gap or dead link?)"
        except (ConnectionError, OSError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
        log.warning("LabVIEW connection CLOSED (%s) after %d sweeps total", reason, state.n_received)
        if getattr(self.server, "verbose", False):
            print(f"[conn] closed {self.client_address} -- {reason}", flush=True)

    def _serve(self, state):
        verbose = getattr(self.server, "verbose", False)
        for raw_line in self.rfile:                 # iterates on '\n'-delimited lines
            if verbose:
                print(f"[recv] {raw_line!r}", flush=True)   # repr shows the terminator (or lack of one)
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                msg = protocol.decode_message(raw_line)
                mtype = msg.get("type", "sweep")
                if mtype == "sweep":
                    processor = getattr(self.server, "sweep_processor", None)
                    if processor is None:
                        reply = _handle_sweep(state, msg)
                    else:
                        reply = processor(
                            state, msg, self.server.trusted_source
                        )
                elif mtype == "ping":
                    reply = {"ok": True, "pong": True, "n_received": state.n_received,
                             "status": state.engine.status()}
                else:
                    reply = {"ok": False, "error": f"unknown message type {mtype!r}"}
            except json.JSONDecodeError as e:        # show the text AROUND the parse error to debug LabVIEW JSON
                txt = raw_line.decode("utf-8", "replace") if isinstance(raw_line, (bytes, bytearray)) else str(raw_line)
                lo = max(0, e.pos - 30)
                near = txt[lo:e.pos] + "  <<<HERE>>>  " + txt[e.pos:e.pos + 30]
                reply = {"ok": False, "error": f"JSONDecodeError: {e.msg} at char {e.pos}", "near": near}
                if verbose:
                    print(f"[json-error] {e.msg} at char {e.pos}\n           near: ...{near}...", flush=True)
            except Exception as exc:                 # never crash the worker on a bad message
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            # The reply is OPTIONAL (LabVIEW logs P/Q or ignores it). Send it BEST-EFFORT with a short
            # write timeout: if LabVIEW's reply-reads fall behind, the send buffer fills and a BLOCKING
            # write would DEADLOCK the socket -- the fitter would stop reading sweeps, LabVIEW's write
            # would then block too, and both hang on a live connection (the "ran N hours then stopped;
            # clicking Play un-stuck it" symptom). Dropping a reply keeps the sweep read loop alive.
            try:
                self.connection.settimeout(_REPLY_TIMEOUT)
                self.wfile.write(protocol.encode_reply(reply))
                self.wfile.flush()
                if verbose:
                    print(f"[send] {reply}", flush=True)
            except (BrokenPipeError, ConnectionResetError):
                break
            except (socket.timeout, TimeoutError, OSError) as exc:
                self._dropped_replies += 1
                if self._dropped_replies in (1, 5, 25) or self._dropped_replies % 100 == 0:
                    log.warning("reply send stalled: dropped %d (LabVIEW not draining replies?) -- %s",
                                self._dropped_replies, exc)
            finally:
                try:
                    self.connection.settimeout(self.timeout)   # restore the long read timeout
                except OSError:
                    pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def make_server(state, host=protocol.DEFAULT_HOST, port=protocol.DEFAULT_PORT, verbose=False, *, trusted_source=None, sweep_processor=None):
    """Create the loopback-only TCP server. Refuses any non-loopback bind address so
    the lab LAN (VME crate / RF switches) is never exposed. ``verbose`` prints each
    connection, received line (with its terminator), and reply -- for LabVIEW debugging."""
    if host not in _LOOPBACK:
        raise ValueError(f"refusing to bind non-loopback host {host!r} -- "
                         f"this server must be 127.0.0.1 only (LAN carries the VME crate / RF switches)")
    if (trusted_source is None) != (sweep_processor is None):
        raise ValueError("protected receiver requires both source and processor")
    if sweep_processor is not None:
        if trusted_source not in ("labview", "pynmr"):
            raise ValueError("invalid trusted receiver source")
        if not callable(sweep_processor):
            raise ValueError("sweep processor must be callable")
    srv = _Server((host, port), _Handler)
    srv.state = state
    srv.verbose = verbose
    srv.trusted_source = trusted_source
    srv.sweep_processor = sweep_processor
    return srv


def serve_in_thread(server):
    """Start ``server`` on a daemon thread; return the thread."""
    t = threading.Thread(target=server.serve_forever, name="nmr-fit-server", daemon=True)
    t.start()
    return t
