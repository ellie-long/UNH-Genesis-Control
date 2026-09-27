"""
Common Genesis VISA/SCPI support.

Used for instruments accessed through PyVISA, including USBTMC devices
such as the Keysight WaveGen used by the Genesis ssRF subsystem.

The transport is intentionally independent of instrument-specific logic.
A device driver may therefore use the same code with:

    USB0::...::INSTR

for physical hardware, or:

    TCPIP0::127.0.0.1::5027::SOCKET

for development/simulation.
"""

from __future__ import annotations

import logging
import threading
import time

import pyvisa

from .base_device import GenesisBaseDevice


log = logging.getLogger(__name__)


OPEN_LIMITS = {
    "HIHI": 1e30,
    "HIGH": 1e29,
    "LOW": -1e29,
    "LOLO": -1e30,
}


class VisaConnection:
    """Thread-safe SCPI connection through PyVISA."""

    def __init__(
        self,
        resource,
        backend="@py",
        timeout=5.0,
        settle=0.05,
        read_termination="\n",
        write_termination="\n",
    ):
        self.resource = str(resource)
        self.backend = backend
        self.timeout = float(timeout)
        self.settle = float(settle)

        self.read_termination = read_termination
        self.write_termination = write_termination

        self._lock = threading.RLock()

        self.rm = None
        self.handle = None

        try:
            if self.backend:
                self.rm = pyvisa.ResourceManager(
                    self.backend
                )
            else:
                self.rm = pyvisa.ResourceManager()

            # Deliberately do not call list_resources().
            # With pyvisa-py this can initiate network discovery.
            self.handle = self.rm.open_resource(
                self.resource
            )

            self.handle.timeout = int(
                max(1.0, self.timeout * 1000.0)
            )

            self.handle.write_termination = (
                self.write_termination
            )

            self.handle.read_termination = (
                self.read_termination
            )

        except Exception as exc:
            self.close()

            raise OSError(
                f"open VISA resource {self.resource} "
                f"failed: {exc}"
            ) from exc

        log.info(
            "connected to VISA resource %s",
            self.resource,
        )

    @staticmethod
    def _validate_command(command):
        command = str(command)

        if "\r" in command or "\n" in command:
            raise ValueError(
                "SCPI command must not contain "
                "line terminators"
            )

        return command

    def _require_open(self):
        if self.handle is None:
            raise OSError(
                "VISA connection is closed"
            )

        return self.handle

    def write(self, command):
        command = self._validate_command(command)

        with self._lock:
            try:
                handle = self._require_open()
                handle.write(command)

                if self.settle > 0:
                    time.sleep(self.settle)

            except Exception as exc:
                self.close()

                raise OSError(
                    f"write {command!r} to "
                    f"{self.resource}: {exc}"
                ) from exc

    def query(self, command):
        command = self._validate_command(command)

        with self._lock:
            try:
                handle = self._require_open()
                reply = handle.query(command)

                if self.settle > 0:
                    time.sleep(self.settle)

                reply = str(reply).strip()

                log.debug(
                    "%s <- %r (%r)",
                    self.resource,
                    command,
                    reply,
                )

                return reply

            except Exception as exc:
                self.close()

                raise OSError(
                    f"query {command!r} to "
                    f"{self.resource}: {exc}"
                ) from exc

    def query_float(self, command):
        raw = self.query(command)

        try:
            return float(raw)

        except ValueError as exc:
            raise OSError(
                f"{self.resource}: bad reply to "
                f"{command!r}: {raw!r}"
            ) from exc

    def query_bool(
        self,
        command,
        true_values=("1", "ON"),
    ):
        return (
            self.query(command).strip().upper()
            in tuple(
                value.upper()
                for value in true_values
            )
        )

    def write_binary_values(
        self,
        command,
        values,
        datatype="f",
        is_big_endian=False,
        header_fmt="ieee",
        close_on_error=True,
    ):
        """Write an IEEE binary block through PyVISA."""

        command = self._validate_command(command)

        with self._lock:
            try:
                handle = self._require_open()

                result = handle.write_binary_values(
                    command,
                    values,
                    datatype=datatype,
                    is_big_endian=is_big_endian,
                    header_fmt=header_fmt,
                )

                if self.settle > 0:
                    time.sleep(self.settle)

                return result

            except Exception as exc:
                if close_on_error:
                    self.close()

                raise OSError(
                    f"binary write {command!r} to "
                    f"{self.resource}: {exc}"
                ) from exc

    def write_ascii_values(
        self,
        command,
        values,
    ):
        """Write a SCPI ASCII value list through PyVISA."""

        command = self._validate_command(command)

        with self._lock:
            try:
                handle = self._require_open()

                result = handle.write_ascii_values(
                    command,
                    values,
                )

                if self.settle > 0:
                    time.sleep(self.settle)

                return result

            except Exception as exc:
                self.close()

                raise OSError(
                    f"ASCII write {command!r} to "
                    f"{self.resource}: {exc}"
                ) from exc

    def set_timeout(self, timeout):
        """Set VISA timeout in seconds."""

        self.timeout = float(timeout)

        with self._lock:
            if self.handle is not None:
                self.handle.timeout = int(
                    max(
                        1.0,
                        self.timeout * 1000.0,
                    )
                )

    def idn(self):
        return self.query("*IDN?")

    def opc(self):
        """Wait for preceding instrument operations."""

        return self.query_bool("*OPC?")

    def errors(self, max_reads=10):
        errors = []

        for _ in range(max_reads):
            response = self.query(
                ":SYST:ERR?"
            ).strip()

            code = (
                response.split(",", 1)[0]
                .strip()
            )

            if (
                code in ("0", "+0")
                or "NO ERROR" in response.upper()
            ):
                break

            errors.append(response)

        return errors

    def close(self):
        with self._lock:
            handle = self.handle
            rm = self.rm

            self.handle = None
            self.rm = None

            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

            if rm is not None:
                try:
                    rm.close()
                except Exception:
                    pass


class VisaDevice(GenesisBaseDevice):
    """Common Genesis base for PyVISA instruments."""

    connection_class = VisaConnection

    @property
    def analog(self):
        return {
            **self.sevr,
            **OPEN_LIMITS,
        }

    def _create_connection(self):
        settings = self.settings

        return self.connection_class(
            resource=settings["resource"],
            backend=settings.get(
                "visa_backend",
                "@py",
            ),
            timeout=float(
                settings.get("timeout", 5.0)
            ),
            settle=float(
                settings.get("settle", 0.05)
            ),
            read_termination=settings.get(
                "read_termination",
                "\n",
            ),
            write_termination=settings.get(
                "write_termination",
                "\n",
            ),
        )
