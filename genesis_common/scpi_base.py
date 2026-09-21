"""
Common Genesis raw-TCP SCPI support.

Based on the SCPI abstraction developed in Farooq's UNH DAQ IOC work,
with additional robustness carried over from the validated Genesis
SPD1305X integration.

Used for instruments that speak line-oriented SCPI over TCP, typically
port 5025.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

from softioc import alarm

from .base_device import GenesisBaseDevice


log = logging.getLogger(__name__)

DEFAULT_SCPI_PORT = 5025

OPEN_LIMITS = {
    "HIHI": 1e30,
    "HIGH": 1e29,
    "LOW": -1e29,
    "LOLO": -1e30,
}


class ScpiConnection:
    """Thread-safe line-oriented SCPI connection over raw TCP."""

    def __init__(
        self,
        host,
        port=DEFAULT_SCPI_PORT,
        timeout=5.0,
        settle=0.05,
    ):
        self.host = str(host)
        self.port = int(port)
        self.timeout = float(timeout)
        self.settle = float(settle)

        self._lock = threading.RLock()
        self._buffer = bytearray()
        self.sock = None

        try:
            self.sock = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            )
            self.sock.settimeout(self.timeout)

        except OSError as exc:
            self.close()
            raise OSError(
                f"connect to {self.host}:{self.port} failed: {exc}"
            ) from exc

        log.info(
            "connected to %s:%d",
            self.host,
            self.port,
        )

    @staticmethod
    def _encode(command: str) -> bytes:
        if "\r" in command or "\n" in command:
            raise ValueError(
                "SCPI command must not contain line terminators"
            )

        return (command + "\n").encode("ascii")

    def _send_locked(self, command: str):
        if self.sock is None:
            raise OSError("SCPI connection is closed")

        self.sock.sendall(self._encode(command))

    def _readline_locked(self) -> str:
        if self.sock is None:
            raise OSError("SCPI connection is closed")

        while b"\n" not in self._buffer:
            chunk = self.sock.recv(4096)

            if not chunk:
                raise ConnectionError(
                    f"{self.host}:{self.port} closed the connection"
                )

            self._buffer.extend(chunk)

        raw, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)

        return raw.decode(
            "ascii",
            errors="strict",
        ).strip("\r \t")

    def write(self, command: str):
        with self._lock:
            try:
                self._send_locked(command)

                if self.settle > 0:
                    time.sleep(self.settle)

            except (OSError, UnicodeError) as exc:
                self.close()
                raise OSError(
                    f"write '{command}' to {self.host}: {exc}"
                ) from exc

    def query(self, command: str) -> str:
        with self._lock:
            try:
                self._send_locked(command)
                reply = self._readline_locked()

                if self.settle > 0:
                    time.sleep(self.settle)

                log.debug(
                    "%s <- %r (%r)",
                    self.host,
                    command,
                    reply,
                )

                return reply

            except (OSError, UnicodeError) as exc:
                # Closing is intentional. A reply arriving after a timeout
                # must never be mistaken for the response to a later query.
                self.close()

                raise OSError(
                    f"query '{command}' to {self.host}: {exc}"
                ) from exc

    def query_float(self, command: str) -> float:
        raw = self.query(command)

        try:
            return float(raw)

        except ValueError as exc:
            raise OSError(
                f"{self.host}: bad reply to {command!r}: {raw!r}"
            ) from exc

    def query_bool(
        self,
        command: str,
        true_values=("1", "ON"),
    ) -> bool:
        return (
            self.query(command).strip().upper()
            in tuple(v.upper() for v in true_values)
        )

    def set_timeout(self, timeout):
        self.timeout = float(timeout)

        if self.sock is not None:
            self.sock.settimeout(self.timeout)

    def idn(self):
        return self.query("*IDN?")

    def errors(self, max_reads=10):
        errors = []

        for _ in range(max_reads):
            response = self.query("SYST:ERR?").strip()
            code = response.split(",", 1)[0].strip()

            if code in ("0", "+0") or "NO ERROR" in response.upper():
                break

            errors.append(response)

        return errors

    def close(self):
        sock = self.sock
        self.sock = None
        self._buffer.clear()

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class ScpiDevice(GenesisBaseDevice):
    """Common Genesis EPICS base for raw-TCP SCPI instruments."""

    connection_class = ScpiConnection

    @property
    def analog(self):
        return {
            **self.sevr,
            **OPEN_LIMITS,
        }

    def _create_connection(self):
        settings = self.settings

        return self.connection_class(
            settings["ip"],
            int(settings.get("port", DEFAULT_SCPI_PORT)),
            float(settings.get("timeout", 5.0)),
            float(settings.get("settle", 0.05)),
        )

    def _set_ignored(self, pv_name):
        log.warning(
            "%s: command ignored; instrument not writable",
            pv_name,
        )

        if pv_name in self.pvs:
            self.pvs[pv_name].set_alarm(
                severity=alarm.MAJOR_ALARM,
                alarm=alarm.WRITE_ALARM,
            )

    def _channel_pvs(self, channel):
        return [
            name
            for name in self.pvs
            if (
                name == channel
                or name.startswith(channel + "_")
            )
            and name not in ("COMM", "ERROR")
        ]

    def _handle_read_error(self, channels=None):
        for channel in self._skip_none_channels(channels):
            for name in self._channel_pvs(channel):
                self.set_alarm(name)

        self._set_comm(False, "Instrument read failed")
        self.reconnect()

    def _handle_read_success(self, channels=None):
        for channel in self._skip_none_channels(channels):
            for name in self._channel_pvs(channel):
                self.remove_alarm(name)

        self._set_comm(True)

    async def do_reads(self):
        if self.t is None:
            self._set_comm(False, "Instrument disconnected")
            self.reconnect()
            return False

        try:
            for index, channel in enumerate(
                self.channels,
                start=1,
            ):
                if "None" in channel:
                    continue

                values = self.read_all(
                    channel,
                    index,
                )

                for name, value in values.items():
                    self.pvs[name].set(value)

            self._handle_read_success()
            return True

        except OSError as exc:
            log.warning(
                "%s: read failed: %s",
                self.device_name,
                exc,
            )

            self._set_comm(False, str(exc))
            self._handle_read_error()

            return False

    def read_all(self, channel, index):
        raise NotImplementedError(
            "SCPI drivers must implement read_all()"
        )