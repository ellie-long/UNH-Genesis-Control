#!/usr/bin/env python3
r"""
tools/sim_ag33220a.py - a fake Agilent 33220A on a TCP socket (no hardware needed).

    python tools\sim_ag33220a.py                 # listens on 127.0.0.1:5025
    python tools\sim_ag33220a.py --port 15025 -v

Answers the SCPI subset the ag33220a driver uses: *IDN? *CLS *OPC? *TRG,
FUNC, FREQ, VOLT, VOLT:OFFS, FUNC:PULS:WIDT, BURS:STAT/MODE/NCYC, TRIG:SOUR,
OUTP, APPL:PULS, SYST:ERR?.  Rejected values go into the error queue, so the
driver's SYST:ERR? handling can be tested too.
"""
import argparse
import logging
import socketserver
import threading
import socket

log = logging.getLogger("sim_ag33220a")
IDN = "Agilent Technologies,33220A,MY44040157,2.06-2.06-26-2"


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.function = "PULS"
        self.freq = 10.0
        self.volt = 1.05
        self.offset = -0.05
        self.width = 1e-3
        self.ncyc = 1
        self.burst = False
        self.burst_mode = "TRIG"
        self.trig_source = "BUS"
        self.output = False
        self.errors = []
        self.triggers = 0

    def handle(self, raw):
        cmd = raw.strip()
        if not cmd:
            return None
        u = cmd.upper()
        head, _, arg = cmd.partition(" ")
        h = head.upper()
        with self.lock:
            if u == "*IDN?":
                return IDN
            if u == "*CLS":
                self.errors.clear(); return None
            if u == "*OPC?":
                return "1"
            if u == "*TRG":
                if not self.burst:
                    self.errors.append('-221,"Settings conflict; burst is off"')
                self.triggers += 1
                return None
            if u.startswith("SYST:ERR"):
                return self.errors.pop(0) if self.errors else '+0,"No error"'

            if h.startswith("APPL:PULS"):
                parts = [p for p in arg.split(",") if p.strip()]
                if len(parts) >= 3:
                    self.freq, self.volt, self.offset = (float(parts[0]), float(parts[1]),
                                                         float(parts[2]))
                self.function = "PULS"
                return None
            if h in ("FUNC", "FUNC?"):
                if u.endswith("?"): return self.function
                self.function = arg.strip().upper(); return None
            if h.startswith("FUNC:PULS:WIDT"):
                if u.endswith("?"): return f"{self.width:.9f}"
                self.width = float(arg); return None
            if h in ("FREQ", "FREQ?"):
                if u.endswith("?"): return f"{self.freq:.6f}"
                self.freq = float(arg); return None
            if h.startswith("VOLT:OFFS"):
                if u.endswith("?"): return f"{self.offset:.4f}"
                self.offset = float(arg); return None
            if h in ("VOLT", "VOLT?"):
                if u.endswith("?"): return f"{self.volt:.4f}"
                v = float(arg)
                if v > 10.0:                       # real instrument's limit into 50 ohm
                    self.errors.append('-222,"Data out of range;VOLT"')
                    return None
                self.volt = v; return None
            if h.startswith("BURS:NCYC"):
                if u.endswith("?"): return f"{self.ncyc}"
                self.ncyc = int(float(arg)); return None
            if h.startswith("BURS:MODE"):
                if u.endswith("?"): return self.burst_mode
                self.burst_mode = arg.strip().upper(); return None
            if h.startswith("BURS:STAT"):
                if u.endswith("?"): return "1" if self.burst else "0"
                self.burst = arg.strip().upper() in ("ON", "1"); return None
            if h.startswith("TRIG:SOUR"):
                if u.endswith("?"): return self.trig_source
                self.trig_source = arg.strip().upper(); return None
            if h in ("OUTP", "OUTP?", "OUTP:STAT", "OUTP:STAT?"):
                if u.endswith("?"): return "1" if self.output else "0"
                self.output = arg.strip().upper() in ("ON", "1"); return None

        log.debug("unhandled: %r", cmd)
        self.errors.append(f'-113,"Undefined header;{cmd[:20]}"')
        return "0" if cmd.endswith("?") else None


class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.server.register_client(self.request)

    def finish(self):
        try:
            self.server.unregister_client(self.request)
        finally:
            super().finish()

    def handle(self):
        log.info("client connected %s", self.client_address)

        for raw in self.rfile:
            for part in raw.decode("ascii", "replace").split(";"):
                try:
                    reply = self.server.state.handle(part)
                except Exception as exc:
                    self.server.state.errors.append(
                        f'-113,"Undefined header;{exc}"'
                    )
                    reply = None

                log.debug(
                    "%-30s -> %s",
                    part.strip(),
                    reply,
                )

                if reply is not None:
                    self.wfile.write(
                        (reply + "\n").encode("ascii")
                    )

        log.info("client disconnected")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, state):
        self._clients = set()
        self._clients_lock = threading.Lock()

        super().__init__(addr, Handler)

        self.state = state

    def register_client(self, client):
        with self._clients_lock:
            self._clients.add(client)

    def unregister_client(self, client):
        with self._clients_lock:
            self._clients.discard(client)

    def drop_clients(self):
        """Force-disconnect all active clients for resilience testing."""

        with self._clients_lock:
            clients = list(self._clients)

        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            try:
                client.close()
            except OSError:
                pass


def serve(host="127.0.0.1", port=5025):
    srv = Server((host, port), State())
    log.info("33220A simulator listening on %s:%d", host, port)
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5025)
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        serve(a.host, a.port).serve_forever()
    except KeyboardInterrupt:
        pass
