"""
Common Genesis HTTP device support.

Used for simple laboratory instruments controlled with HTTP GET
requests, including the Mini-Circuits RF switches used by the
Genesis ssRF system.
"""

from __future__ import annotations

import http.client
import logging
import threading

from genesis_common.base_device import GenesisBaseDevice


log = logging.getLogger(__name__)

DEFAULT_HTTP_PORT = 80


class HttpConnection:
    """Thread-safe persistent HTTP GET connection."""

    def __init__(
        self,
        host,
        port=DEFAULT_HTTP_PORT,
        timeout=2.0,
    ):
        self.host = str(host)
        self.port = int(port)
        self.timeout = float(timeout)

        self._lock = threading.RLock()
        self._connection = None

    def _open_locked(self):
        if self._connection is None:
            self._connection = (
                http.client.HTTPConnection(
                    self.host,
                    self.port,
                    timeout=self.timeout,
                )
            )

        return self._connection

    def get(self, path: str) -> str:
        """Issue one HTTP GET and return stripped ASCII response."""

        if not isinstance(path, str):
            raise TypeError(
                "HTTP path must be a string"
            )

        if not path.startswith("/"):
            raise ValueError(
                "HTTP path must start with '/'"
            )

        if "\r" in path or "\n" in path:
            raise ValueError(
                "HTTP path must not contain line terminators"
            )

        with self._lock:
            try:
                connection = (
                    self._open_locked()
                )

                connection.request(
                    "GET",
                    path,
                )

                response = (
                    connection.getresponse()
                )

                payload = response.read()

                if not (
                    200
                    <= response.status
                    < 300
                ):
                    raise OSError(
                        "HTTP GET "
                        f"{path!r} returned "
                        f"{response.status} "
                        f"{response.reason}"
                    )

                try:
                    text = payload.decode(
                        "ascii"
                    )

                except UnicodeDecodeError as exc:
                    raise OSError(
                        "non-ASCII HTTP response "
                        f"from {self.host}"
                    ) from exc

                log.debug(
                    "%s:%d <- GET %s -> %r",
                    self.host,
                    self.port,
                    path,
                    text.strip(),
                )

                return text.strip()

            except (
                OSError,
                http.client.HTTPException,
            ) as exc:
                self.close()

                raise OSError(
                    "HTTP GET "
                    f"{path!r} to "
                    f"{self.host}:{self.port} "
                    f"failed: {exc}"
                ) from exc

    def close(self):
        """Close the current HTTP connection."""

        with self._lock:
            connection = (
                self._connection
            )

            self._connection = None

            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass


class HttpDevice(GenesisBaseDevice):
    """Genesis base class for HTTP-controlled instruments."""

    connection_class = HttpConnection

    def _create_connection(self):
        settings = self.settings

        return self.connection_class(
            settings["ip"],
            int(
                settings.get(
                    "port",
                    DEFAULT_HTTP_PORT,
                )
            ),
            float(
                settings.get(
                    "timeout",
                    2.0,
                )
            ),
        )
