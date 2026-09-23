"""
Software simulator for a Mini-Circuits dual-SPDT RF switch.

Implements the subset of the HTTP interface required by Genesis:

    /MN?
    /SN?
    /SWPORT?
    /SETP=0
    /SETP=3
"""

from __future__ import annotations

import argparse
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
import logging
import socket
import threading


log = logging.getLogger(__name__)


class State:
    """Thread-safe simulated switch state."""

    def __init__(self):
        self.lock = threading.RLock()

        self.model = (
            "RC-2SPDT-A18"
        )

        self.serial = (
            "SIM0001"
        )

        # Safe simulator default:
        # both internal switches on Port 2.
        self.port_mask = 3

        # Test hook used to simulate a switch that accepts a command
        # but physically fails to move.
        self.ignore_set = False

        self.request_log = []


class MiniCircuitsHandler(
    BaseHTTPRequestHandler
):
    protocol_version = (
        "HTTP/1.1"
    )

    def _reply(
        self,
        body,
        status=200,
    ):
        payload = (
            str(body)
            .encode("ascii")
        )

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "text/plain",
        )

        self.send_header(
            "Content-Length",
            str(len(payload)),
        )

        self.end_headers()

        self.wfile.write(
            payload
        )

    def do_GET(self):
        state = (
            self.server.state
        )

        raw_path = self.path

        with state.lock:
            state.request_log.append(
                raw_path
            )

        # Some HTTP libraries may normalize a trailing empty query.
        # Accept either spelling in the simulator.
        path = raw_path.upper()

        if path in (
            "/MN?",
            "/MN",
        ):
            with state.lock:
                value = state.model

            self._reply(value)
            return

        if path in (
            "/SN?",
            "/SN",
        ):
            with state.lock:
                value = state.serial

            self._reply(value)
            return

        if path in (
            "/SWPORT?",
            "/SWPORT",
        ):
            with state.lock:
                value = (
                    state.port_mask
                )

            self._reply(value)
            return

        if path.startswith(
            "/SETP="
        ):
            try:
                value = int(
                    path.split(
                        "=",
                        1,
                    )[1]
                )

            except ValueError:
                self._reply(
                    "BAD SETP",
                    status=400,
                )
                return

            if value not in (
                0,
                3,
            ):
                self._reply(
                    "BAD SETP",
                    status=400,
                )
                return

            with state.lock:
                if not state.ignore_set:
                    state.port_mask = (
                        value
                    )

            # Genesis does not trust this reply. It verifies SWPORT?.
            self._reply("OK")
            return

        self._reply(
            "NOT FOUND",
            status=404,
        )

    def log_message(
        self,
        format,
        *args,
    ):
        # Keep automated tests quiet.
        if getattr(
            self.server,
            "verbose",
            False,
        ):
            log.debug(
                "%s - %s",
                self.address_string(),
                format % args,
            )


class MiniCircuitsServer(
    ThreadingHTTPServer
):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address,
        *,
        state=None,
        verbose=False,
    ):
        self.state = (
            state
            if state is not None
            else State()
        )

        self.verbose = bool(
            verbose
        )

        self._clients = set()
        self._clients_lock = (
            threading.Lock()
        )

        super().__init__(
            server_address,
            MiniCircuitsHandler,
        )

    def register_client(
        self,
        client,
    ):
        """Track an active simulator HTTP client."""

        with self._clients_lock:
            self._clients.add(
                client
            )

    def unregister_client(
        self,
        client,
    ):
        """Stop tracking an HTTP client."""

        with self._clients_lock:
            self._clients.discard(
                client
            )

    def process_request(
        self,
        request,
        client_address,
    ):
        """Register a client before its handler thread starts."""

        self.register_client(
            request
        )

        try:
            return super().process_request(
                request,
                client_address,
            )

        except Exception:
            self.unregister_client(
                request
            )
            raise

    def shutdown_request(
        self,
        request,
    ):
        """Remove a client after its handler finishes."""

        try:
            super().shutdown_request(
                request
            )

        finally:
            self.unregister_client(
                request
            )

    def drop_clients(self):
        """Force-disconnect all active simulator clients."""

        with self._clients_lock:
            clients = list(
                self._clients
            )

        for client in clients:
            try:
                client.shutdown(
                    socket.SHUT_RDWR
                )
            except OSError:
                pass

            try:
                client.close()
            except OSError:
                pass

            self.unregister_client(
                client
            )


def serve(
    host="127.0.0.1",
    port=0,
    *,
    verbose=False,
):
    """Create a simulator server without starting its loop."""

    return MiniCircuitsServer(
        (
            host,
            int(port),
        ),
        verbose=verbose,
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8080,
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

    server = serve(
        args.host,
        args.port,
        verbose=args.verbose,
    )

    host, port = (
        server.server_address
    )

    print(
        "Mini-Circuits switch simulator "
        f"listening on {host}:{port}"
    )

    print(
        "Base URL: "
        f"http://{host}:{port}"
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
