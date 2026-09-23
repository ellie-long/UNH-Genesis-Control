"""
Minimal Keysight WaveGen SCPI simulator for Genesis development.

This simulator intentionally implements only the scalar command subset
currently used by ssrf_ioc.keysight_wavegen.DeviceConnection.

It listens on a raw TCP socket so PyVISA-py can connect using:

    TCPIP0::127.0.0.1::<port>::SOCKET
"""

from __future__ import annotations

import argparse
import logging
import socketserver
import threading


log = logging.getLogger(__name__)


class WaveGenState:
    def __init__(self):
        self.lock = threading.RLock()

        self.idn = (
            "KEYSIGHT TECHNOLOGIES,"
            "DSOX3024T,"
            "MY57252012,"
            "TEST"
        )

        self.function = "ARB"
        self.output_on = False

        self.period_s = 0.010
        self.vhigh_v = 0.700
        self.vlow_v = -0.700

        self.arb_filter = "NORM"
        self.interpolate = True
        self.load = "INF"
        self.output_mode = "NORM"
        self.invert = False

        self.errors = []

    def push_error(self, message):
        with self.lock:
            self.errors.append(str(message))

    def pop_error(self):
        with self.lock:
            if self.errors:
                return self.errors.pop(0)

        return '+0,"No error"'


class WaveGenSCPI:
    def __init__(self, state=None, verbose=False):
        self.state = state or WaveGenState()
        self.verbose = bool(verbose)

    @staticmethod
    def _bool_value(value):
        value = str(value).strip().upper()

        if value in ("1", "ON"):
            return True

        if value in ("0", "OFF"):
            return False

        raise ValueError(
            f"invalid boolean value: {value}"
        )

    def execute(self, command):
        raw = str(command).strip()
        upper = raw.upper()

        if not raw:
            return None

        if self.verbose:
            log.debug("%-32s", raw)

        with self.state.lock:
            try:
                reply = self._execute_locked(
                    raw,
                    upper,
                )

            except Exception as exc:
                message = f'-100,"{exc}"'
                self.state.push_error(message)

                if self.verbose:
                    log.debug(
                        "%-32s -> ERROR %s",
                        raw,
                        message,
                    )

                return None

        if (
            self.verbose
            and reply is not None
        ):
            log.debug(
                "%-32s -> %s",
                raw,
                reply,
            )

        return reply

    def _execute_locked(self, raw, upper):
        s = self.state

        # ----------------------------------------------------------
        # Common IEEE / system commands
        # ----------------------------------------------------------

        if upper == "*IDN?":
            return s.idn

        if upper == "*OPC?":
            return "1"

        if upper in ("*CLS",):
            s.errors.clear()
            return None

        if upper in (
            ":SYST:ERR?",
            "SYST:ERR?",
        ):
            return s.pop_error()

        # ----------------------------------------------------------
        # WaveGen function
        # ----------------------------------------------------------

        if upper == ":WGEN:FUNC?":
            return s.function

        if upper.startswith(":WGEN:FUNC "):
            value = raw.split(None, 1)[1].strip().upper()

            if value != "ARB":
                raise ValueError(
                    "simulator currently supports ARB only"
                )

            s.function = value
            return None

        # ----------------------------------------------------------
        # Output enable
        # ----------------------------------------------------------

        if upper == ":WGEN:OUTP?":
            return "1" if s.output_on else "0"

        if upper.startswith(":WGEN:OUTP "):
            value = raw.split(None, 1)[1]
            s.output_on = self._bool_value(value)
            return None

        # ----------------------------------------------------------
        # Period
        # ----------------------------------------------------------

        if upper == ":WGEN:PER?":
            return f"{s.period_s:.12g}"

        if upper.startswith(":WGEN:PER "):
            value = float(
                raw.split(None, 1)[1]
            )

            if value <= 0:
                raise ValueError(
                    "period must be positive"
                )

            s.period_s = value
            return None

        # ----------------------------------------------------------
        # Voltage levels
        # ----------------------------------------------------------

        if upper == ":WGEN:VOLT:HIGH?":
            return f"{s.vhigh_v:.12g}"

        if upper.startswith(
            ":WGEN:VOLT:HIGH "
        ):
            s.vhigh_v = float(
                raw.split(None, 1)[1]
            )
            return None

        if upper == ":WGEN:VOLT:LOW?":
            return f"{s.vlow_v:.12g}"

        if upper.startswith(
            ":WGEN:VOLT:LOW "
        ):
            s.vlow_v = float(
                raw.split(None, 1)[1]
            )
            return None

        # ----------------------------------------------------------
        # ARB preparation commands from Chhetra's controller
        # ----------------------------------------------------------

        if upper.startswith(
            ":FUNC:ARB:FILT "
        ):
            s.arb_filter = (
                raw.split(None, 1)[1]
                .strip()
                .upper()
            )
            return None

        if upper.startswith(
            ":WGEN:ARB:INTERPOLATE "
        ):
            value = raw.split(None, 1)[1]
            s.interpolate = self._bool_value(
                value
            )
            return None

        if upper.startswith(
            ":WGEN:OUTP:LOAD "
        ):
            s.load = (
                raw.split(None, 1)[1]
                .strip()
                .upper()
            )
            return None

        if upper.startswith(
            ":WGEN:OUTP:MODE "
        ):
            s.output_mode = (
                raw.split(None, 1)[1]
                .strip()
                .upper()
            )
            return None

        if upper.startswith(
            ":WGEN:OUTP:INV "
        ):
            value = raw.split(None, 1)[1]
            s.invert = self._bool_value(
                value
            )
            return None

        raise ValueError(
            f"unsupported SCPI command: {raw}"
        )


class WaveGenTCPHandler(
    socketserver.StreamRequestHandler
):
    def handle(self):
        simulator = self.server.simulator

        while True:
            raw = self.rfile.readline()

            if not raw:
                return

            try:
                command = raw.decode(
                    "ascii"
                ).strip("\r\n")

            except UnicodeDecodeError:
                return

            reply = simulator.execute(command)

            if reply is not None:
                self.wfile.write(
                    (str(reply) + "\n").encode(
                        "ascii"
                    )
                )
                self.wfile.flush()


class WaveGenTCPServer(
    socketserver.ThreadingTCPServer
):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address,
        simulator,
    ):
        self.simulator = simulator

        super().__init__(
            server_address,
            WaveGenTCPHandler,
        )


def start_server(
    host="127.0.0.1",
    port=0,
    verbose=False,
):
    simulator = WaveGenSCPI(
        verbose=verbose
    )

    server = WaveGenTCPServer(
        (host, int(port)),
        simulator,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )

    thread.start()

    return server, thread, simulator


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5027,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=(
            logging.DEBUG
            if args.verbose
            else logging.INFO
        ),
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(message)s"
        ),
    )

    server, _, _ = start_server(
        host=args.host,
        port=args.port,
        verbose=args.verbose,
    )

    host, port = server.server_address

    print(
        f"Keysight WaveGen simulator "
        f"listening on {host}:{port}"
    )

    print(
        "VISA resource: "
        f"TCPIP0::{host}::{port}::SOCKET"
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        pass

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
