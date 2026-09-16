"""Ethernet/SCPI driver for the Siglent SPD1000X series."""

from __future__ import annotations

import socket
import threading
import time
from typing import Protocol


class SCPITransport(Protocol):
    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...

    def close(self) -> None: ...


class SocketSCPITransport:
    """Newline-terminated SCPI over the SPD1000X raw TCP interface."""

    def __init__(self, host: str, port: int = 5025, timeout: float = 5.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._socket = socket.create_connection((host, self.port), timeout=self.timeout)
        self._socket.settimeout(self.timeout)
        self._lock = threading.RLock()
        self._rx_buffer = bytearray()

    @staticmethod
    def _encode(command: str) -> bytes:
        if "\r" in command or "\n" in command:
            raise ValueError("SCPI command must not contain line terminators")
        return (command + "\n").encode("ascii")

    def write(self, command: str) -> None:
        with self._lock:
            self._socket.sendall(self._encode(command))

    def _readline(self) -> str:
        while b"\n" not in self._rx_buffer:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError("SPD1000X closed the TCP connection")
            self._rx_buffer.extend(chunk)
        raw, _, remainder = self._rx_buffer.partition(b"\n")
        self._rx_buffer = bytearray(remainder)
        return raw.decode("ascii", errors="strict").strip("\r \t")

    def query(self, command: str) -> str:
        with self._lock:
            try:
                self._socket.sendall(self._encode(command))
                return self._readline()
            except (OSError, UnicodeError) as exc:
                # A late reply after a timeout must never answer a later query.
                self.close()
                raise OSError(f"{command}: {exc}") from exc

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass


def _bounded(name: str, value: float, low: float, high: float) -> float:
    value = float(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}], got {value}")
    return value


class SPD1000XController:
    """Single-channel SPD1000X SCPI interface.

    Source-code safety ceilings cannot be raised by IOC configuration or PVs.
    """

    # EDIT THESE IN SOURCE ONLY. Restart the IOC after changing them.
    # Output command limits remain separate from protection trip thresholds.
    SAFETY_MAX_VOLTAGE = 5.0
    SAFETY_MAX_CURRENT = 0.1

    # Default trip thresholds, also the maximum allowed protection PV values.
    # Reapplied while output is OFF on each startup/reconnect.
    DEFAULT_OVP = 5.1
    DEFAULT_OCP = 0.11

    HARDWARE_MAX_VOLTAGE = 30.0
    HARDWARE_MAX_CURRENT = 5.0

    def __init__(
        self,
        transport: SCPITransport,
        *,
        max_voltage: float = HARDWARE_MAX_VOLTAGE,
        max_current: float = HARDWARE_MAX_CURRENT,
        command_delay: float = 0.05,
    ):
        self.transport = transport
        self.max_voltage, self.max_current = self.effective_limits(max_voltage, max_current)
        self.command_delay = _bounded("command delay", command_delay, 0, 60)
        self._lock = threading.RLock()

    @classmethod
    def effective_limits(cls, voltage=HARDWARE_MAX_VOLTAGE, current=HARDWARE_MAX_CURRENT):
        return (
            min(_bounded("configured voltage limit", voltage, 0, cls.HARDWARE_MAX_VOLTAGE),
                _bounded("source voltage limit", cls.SAFETY_MAX_VOLTAGE, 0, 5.0)),
            min(_bounded("configured current limit", current, 0, cls.HARDWARE_MAX_CURRENT),
                _bounded("source current limit", cls.SAFETY_MAX_CURRENT, 0, cls.HARDWARE_MAX_CURRENT)),
        )

    def _write(self, command):
        self.transport.write(command)
        time.sleep(self.command_delay)

    def _query(self, command):
        result = self.transport.query(command)
        time.sleep(self.command_delay)
        return result

    def initialize_safe(self):
        """Never restore an energized output after startup or reconnect."""
        self.set_output(False)
        self._write("TIMEr CH1,OFF")
        self.set_voltage(0)
        self.set_current(0)
        if self.status_word() & ((1 << 4) | (1 << 6)):
            raise ValueError("output/timer did not turn off")
        self._validate_setpoints()
        self.set_ovp(self.DEFAULT_OVP)
        self.set_ocp(self.DEFAULT_OCP)
        if not abs(self.ovp() - self.DEFAULT_OVP) < 0.0005:
            raise ValueError("OVP default readback mismatch")
        if not abs(self.ocp() - self.DEFAULT_OCP) < 0.0005:
            raise ValueError("OCP default readback mismatch")

    def _validate_setpoints(self):
        voltage, current = self.effective_limits(self.max_voltage, self.max_current)
        try:
            _bounded("device voltage", self.voltage_setpoint(), 0, voltage)
            _bounded("device current", self.current_setpoint(), 0, current)
        except ValueError:
            self._write("OUTPut CH1,OFF")
            raise

    @staticmethod
    def _command_value(name, value, maximum):
        value = _bounded(name, value, 0, maximum)
        # Check the actual wire value as well as the unrounded request.
        return _bounded(name, float(f"{value:.3f}"), 0, maximum)

    def identity(self) -> str:
        return self._query("*IDN?")

    def set_voltage(self, volts: float) -> None:
        value = self._command_value("voltage", volts, self.effective_limits(self.max_voltage, self.max_current)[0])
        self._write(f"CH1:VOLTage {value:.3f}")

    def voltage_setpoint(self) -> float:
        return float(self._query("CH1:VOLTage?"))

    def set_current(self, amps: float) -> None:
        value = self._command_value("current", amps, self.effective_limits(self.max_voltage, self.max_current)[1])
        self._write(f"CH1:CURRent {value:.3f}")

    def current_setpoint(self) -> float:
        return float(self._query("CH1:CURRent?"))

    def set_output(self, enabled: bool) -> None:
        with self._lock:
            if enabled:
                if self.effective_limits(self.max_voltage, self.max_current)[1] <= 0:
                    raise ValueError("Set a source-code current limit before enabling output")
                self._write("TIMEr CH1,OFF")
                if self.status_word() & (1 << 6):
                    self._write("OUTPut CH1,OFF")
                    raise ValueError("timer did not turn off")
                self._validate_setpoints()
            state = "ON" if bool(enabled) else "OFF"
            self._write(f"OUTPut CH1,{state}")

    def measure_voltage(self) -> float:
        return float(self._query("MEASure:VOLTage? CH1"))

    def measure_current(self) -> float:
        return float(self._query("MEASure:CURRent? CH1"))

    def measure_power(self) -> float:
        return float(self._query("MEASure:POWEr? CH1"))

    def set_ovp(self, volts: float) -> None:
        value = self._command_value("OVP", volts, _bounded("source OVP ceiling", self.DEFAULT_OVP, 0, self.HARDWARE_MAX_VOLTAGE))
        self._write(f"OVP {value:.3f}")

    def ovp(self) -> float:
        return float(self._query("OVP?"))

    def set_ocp(self, amps: float) -> None:
        value = self._command_value("OCP", amps, _bounded("source OCP ceiling", self.DEFAULT_OCP, 0, self.HARDWARE_MAX_CURRENT))
        self._write(f"OCP {value:.3f}")

    def ocp(self) -> float:
        return float(self._query("OCP?"))

    def status_word(self) -> int:
        reply = self._query("SYSTem:STATus?")
        return int(reply, 0)

    def output_enabled(self, status: int | None = None) -> bool:
        return bool((self.status_word() if status is None else status) & (1 << 4))

    def constant_current(self, status: int | None = None) -> bool:
        return bool((self.status_word() if status is None else status) & 1)

    def system_error(self) -> str:
        return self._query("SYSTem:ERRor?")

    def read_all(self) -> dict[str, float | bool]:
        status = self.status_word()
        values = {
            "voltage": self.measure_voltage(),
            "current": self.measure_current(),
            "power": self.measure_power(),
            "voltage_setpoint": self.voltage_setpoint(),
            "current_setpoint": self.current_setpoint(),
            "output": self.output_enabled(status),
            "constant_current": self.constant_current(status),
            "ovp": self.ovp(),
            "ocp": self.ocp(),
        }
        voltage, current = self.effective_limits(self.max_voltage, self.max_current)
        try:
            _bounded("device voltage", values["voltage_setpoint"], 0, voltage)
            _bounded("device current", values["current_setpoint"], 0, current)
            if status & (1 << 6):
                raise ValueError("unexpected active timer")
        except ValueError:
            self.set_output(False)
            raise
        return values

    def write_and_settle(self, operation, value) -> None:
        with self._lock:
            operation(value)

    def close(self) -> None:
        self.transport.close()
