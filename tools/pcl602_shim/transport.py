"""Thread-safe serial transport for the PCL602USB."""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Transport(Protocol):
    def command(self, request: str, *, expect_reply: bool = False) -> str | None: ...

    def close(self) -> None: ...


class SerialTransport:
    """Serial or raw-TCP serial-device-server connection.

    ``port`` may be a local device such as ``/dev/ttyUSB0`` or a pyserial URL
    such as ``socket://192.0.2.10:4001``.
    """

    def __init__(
        self,
        port: str,
        timeout: float = 0.5,
        *,
        xonxoff: bool = False,
        command_delay: float = 0.05,
    ):
        import serial

        self._serial = serial.serial_for_url(
            port,
            exclusive=True,
            baudrate=38400,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            # Match the supplied, previously working Windows joystick program.
            # The manual lists XON/XOFF, so this remains configurable.
            xonxoff=bool(xonxoff),
            rtscts=False,
            dsrdtr=False,
            timeout=timeout,
            write_timeout=timeout,
        )
        self._lock = threading.RLock()
        self._command_delay = float(command_delay)

    def command(self, request: str, *, expect_reply: bool = False) -> str | None:
        if "\r" in request or "\n" in request:
            raise ValueError("request must not contain line terminators")
        wire = (request + "\r").encode("ascii")
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(wire)
            self._serial.flush()
            if not expect_reply:
                if self._command_delay:
                    time.sleep(self._command_delay)
                return None
            raw = self._serial.read_until(b"\r")
            if not raw.endswith(b"\r"):
                raise TimeoutError(f"no reply to {request!r}")
            reply = raw.decode("ascii", errors="strict").strip("\r\n \t")
            # Some half-duplex adapters echo the transmitted command, either as
            # its own line or immediately before the reply.
            if reply.startswith(request):
                reply = reply[len(request):].strip()
            if not reply:
                raw = self._serial.read_until(b"\r")
                if not raw.endswith(b"\r"):
                    raise TimeoutError(f"only echo received for {request!r}")
                reply = raw.decode("ascii", errors="strict").strip("\r\n \t")
            if self._command_delay:
                time.sleep(self._command_delay)
            return reply

    def close(self) -> None:
        self._serial.close()
