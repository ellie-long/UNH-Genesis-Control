#!/usr/bin/env python3
r"""
tools/sim_smc100a.py - a fake R&S SMC100A on a TCP socket (no hardware needed).

    python tools\sim_smc100a.py                  # listens on 127.0.0.1:5025
    python tools\sim_smc100a.py --port 15025

Point the app at it (settings.json -> "smc_resource": "127.0.0.1:5025") to
develop or demo the BLAST sequence with no signal generator on the bench.

It answers the SCPI subset this project uses: *IDN? *CLS *OPC? *TRG,
FREQ/FREQ:MODE, POW/POW:MODE, POW:STAR/STOP, SWE:POW:*, SWE:RES, OUTP,
FM:STAT/SOUR/DEV/EXT:DEV, SYST:ERR?.  Unknown commands are accepted silently
and unknown queries answer "0", exactly as a real instrument tolerates.
"""
import argparse
import logging
import socket
import socketserver
import threading

log = logging.getLogger("sim_smc100a")

IDN = "Rohde&Schwarz,SMC100A,1411.4002k02/106687,3.1.18.2-3.01.134.28"


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.freq = 33e6
        self.freq_mode = "CW"
        self.pow = -22.0
        self.pow_mode = "CW"
        self.outp = False
        self.fm_state = False
        self.fm_source = "EXT"
        self.fm_dev = 10e3
        self.sweep = {"start": -120.0, "stop": -22.0, "step": 1.0,
                      "dwell": 0.01, "shape": "TRI", "trig_source": "EXT", "trig_slope": "POS"}
        self.errors = []

    # one SCPI message in, reply string (or None) out
    def handle(self, raw):
        cmd = raw.strip()
        if not cmd:
            return None
        u = cmd.upper()
        head, _, arg = cmd.partition(" ")
        head_u = head.upper()
        with self.lock:
            if u == "*IDN?":
                return IDN
            if u in ("*CLS", "*RST", "*TRG", "SWE:RES", "SWE:POW:RES"):
                if u == "*CLS":
                    self.errors.clear()
                return None
            if u == "*OPC?":
                return "1"
            if u.startswith("SYST:ERR"):
                return self.errors.pop(0) if self.errors else '0,"No error"'

            # frequency
            if head_u in ("FREQ", "FREQ?", ":FREQ", ":FREQ?"):
                if u.endswith("?"):
                    return f"{self.freq:.6f}"
                self.freq = float(arg); return None
            if head_u.startswith("FREQ:MODE"):
                if u.endswith("?"):
                    return self.freq_mode
                self.freq_mode = arg.strip().upper(); return None

            # level
            if head_u in ("POW", "POW?"):
                if u.endswith("?"):
                    return f"{self.pow:.2f}"
                self.pow = float(arg); return None
            if head_u.startswith("POW:MODE"):
                if u.endswith("?"):
                    return self.pow_mode
                self.pow_mode = arg.strip().upper(); return None
            if head_u.startswith("POW:STAR"):
                if u.endswith("?"): return f"{self.sweep['start']:.2f}"
                self.sweep["start"] = float(arg); return None
            if head_u.startswith("POW:STOP"):
                if u.endswith("?"): return f"{self.sweep['stop']:.2f}"
                self.sweep["stop"] = float(arg); return None

            # sweep
            if head_u.startswith("SWE:POW:STEP"):
                if u.endswith("?"): return f"{self.sweep['step']:.2f}"
                self.sweep["step"] = float(arg); return None
            if head_u.startswith("SWE:POW:DWEL"):
                if u.endswith("?"): return f"{self.sweep['dwell']:.4f}"
                self.sweep["dwell"] = float(arg); return None
            if head_u.startswith("SWE:POW:SHAP"):
                if u.endswith("?"): return self.sweep["shape"]
                self.sweep["shape"] = arg.strip().upper(); return None
            if head_u.startswith("TRIG") or head_u.startswith("SWE:POW:MODE"):
                if u.endswith("?"): return self.sweep["trig_source"]
                self.sweep["trig_source"] = arg.strip().upper(); return None

            # output
            if head_u in ("OUTP", "OUTP?", "OUTP:STAT", "OUTP:STAT?"):
                if u.endswith("?"):
                    return "1" if self.outp else "0"
                self.outp = arg.strip().upper() in ("ON", "1"); return None

            # FM
            if head_u.startswith("FM:STAT"):
                if u.endswith("?"):
                    return "1" if self.fm_state else "0"
                self.fm_state = arg.strip().upper() in ("ON", "1"); return None
            if head_u.startswith("FM:SOUR"):
                if u.endswith("?"): return self.fm_source
                self.fm_source = arg.strip().upper(); return None
            if head_u.startswith("FM:EXT:DEV") or head_u.startswith("FM:DEV"):
                if u.endswith("?"): return f"{self.fm_dev:.1f}"
                self.fm_dev = float(arg); return None

        log.debug("unhandled: %r", cmd)
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
                    # Bad argument -> simulated instrument error queue.
                    self.server.state.errors.append(
                        f'-113,"Undefined header;{exc}"'
                    )
                    reply = None

                log.debug(
                    "%-28s -> %s",
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
        """Track an active simulator TCP connection."""

        with self._clients_lock:
            self._clients.add(client)

    def unregister_client(self, client):
        """Remove a finished simulator TCP connection."""

        with self._clients_lock:
            self._clients.discard(client)

    def drop_clients(self):
        """Force-disconnect all active clients.

        Intended for software resilience testing. This simulates loss of
        the instrument/network connection without modifying instrument state.
        """

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
    log.info("SMC100A simulator listening on %s:%d", host, port)
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5025)
    ap.add_argument("-v", action="store_true", help="log every SCPI exchange")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        serve(a.host, a.port).serve_forever()
    except KeyboardInterrupt:
        pass
