"""Engine control channel: JSON lines over a loopback TCP socket.

The engine (in the ``run`` process) serves; the Sim Control IOC and the
``python -m cooldown_sim ctl`` command are clients.  One request per line,
one reply per line::

    -> {"cmd": "pause"}
    <- {"ok": true, "status": {...}}

Commands: status, play, pause, speed <x>, seek <epoch>, jump <minutes>,
fault <instrument> <mode>, autopilot <leg> <on|off>, note <text>, mark,
model <key> <value>, stop.
"""

from __future__ import annotations

import json
import logging
import socket
import socketserver
import threading
from typing import Callable

from .ports import LOOPBACK

log = logging.getLogger(__name__)


class ControlServer:
    def __init__(self, port: int, handler: Callable[[dict], dict], host: str = LOOPBACK):
        if host != LOOPBACK:
            raise ValueError("control socket binds to 127.0.0.1 only")
        outer = self

        class _H(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    try:
                        request = json.loads(raw.decode("utf-8"))
                        reply = outer.handler(request)
                    except Exception as exc:          # noqa: BLE001 - report, keep serving
                        log.exception("control request failed")
                        reply = {"ok": False, "error": str(exc)}
                    self.wfile.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()

        class _S(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.handler = handler
        self.server = _S((host, port), _H)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                        name="cooldown-sim-control")

    def start(self) -> "ControlServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ControlClient:
    """Blocking client (CLI, tests)."""

    def __init__(self, port: int, host: str = LOOPBACK, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.fh = self.sock.makefile("rwb")

    def call(self, cmd: str, **kw) -> dict:
        self.fh.write((json.dumps({"cmd": cmd, **kw}) + "\n").encode("utf-8"))
        self.fh.flush()
        line = self.fh.readline()
        if not line:
            raise ConnectionError("engine closed the control connection")
        return json.loads(line.decode("utf-8"))

    def close(self) -> None:
        try:
            self.fh.close()
            self.sock.close()
        except OSError:
            pass
