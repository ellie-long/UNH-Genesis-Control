"""High-level PCL602USB Direct Talk commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading

from transport import Transport


class Axis(str, Enum):
    X = "X"
    Y = "Y"


def _bounded(name: str, value: int, low: int, high: int) -> int:
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}], got {value}")
    return value


@dataclass
class PCL602Controller:
    transport: Transport
    address: int = 0

    def __post_init__(self) -> None:
        _bounded("address", self.address, 0, 99)
        self._command_lock = threading.RLock()

    def _prefix(self, axis: Axis | str | None) -> str:
        if axis is None:
            return f"@{self.address}"
        return f"@{self.address}{Axis(axis).value}"

    def write(self, axis: Axis | str, command: str) -> None:
        with self._command_lock:
            self.transport.command(self._prefix(axis) + command)

    def query(self, axis: Axis | str, command: str) -> str:
        with self._command_lock:
            reply = self.transport.command(self._prefix(axis) + command, expect_reply=True)
        assert reply is not None
        return reply

    @staticmethod
    def _integer_reply(request: str, reply: str) -> int:
        try:
            return int(reply)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"controller reply to {request!r} is not an integer: {reply!r}") from exc

    def query_int(self, axis: Axis | str, command: str) -> int:
        reply = self.query(axis, command)
        return self._integer_reply(self._prefix(axis) + command, reply)

    def set_output(self, axis, output, enabled):
        self.write(axis, f"O{_bounded('output', output, 1, 8)}={int(bool(enabled))}")

    def outputs(self, axis):
        value = self.query_int(axis, 'VO')
        if not 0 <= value <= 255:
            raise RuntimeError('invalid output register readback')
        return value

    def profile_rates(self, axis):
        return tuple(self.query_int(axis, 'V' + command) for command in ('B', 'M', 'A'))

    def set_acceleration(self, axis: Axis | str, value: int) -> None:
        self.write(axis, f"A{_bounded('acceleration', value, 100, 9_999_999)}")

    def set_base_speed(self, axis: Axis | str, value: int) -> None:
        self.write(axis, f"B{_bounded('base speed', value, 1, 5_000)}")

    def set_max_speed(self, axis: Axis | str, value: int) -> None:
        self.write(axis, f"M{_bounded('max speed', value, 1, 50_000)}")

    def set_jog_speed(self, axis: Axis | str, value: int) -> None:
        self.write(axis, f"J{_bounded('jog speed', value, 1, 50_000)}")

    def set_motor_enabled(self, axis: Axis | str, enabled: bool) -> None:
        self.write(axis, f"T{int(bool(enabled))}")

    def motor_enabled(self, axis: Axis | str) -> bool:
        return bool(self.query_int(axis, "VT"))

    def set_position(self, axis: Axis | str, value: int) -> None:
        self.write(axis, f"Z{_bounded('position', value, -8_388_607, 8_388_607)}")

    def set_direction(self, axis: Axis | str, positive: bool) -> None:
        self.write(axis, "+" if positive else "-")

    def move_relative(self, axis: Axis | str, steps: int) -> None:
        steps = _bounded("relative steps", steps, -8_388_607, 8_388_607)
        if steps == 0:
            return
        with self._command_lock:
            self.write(axis, "+" if steps > 0 else "-")
            self.write(axis, f"N{abs(steps)}")
            self.write(axis, "G")

    def move_absolute(self, axis: Axis | str, position: int) -> None:
        with self._command_lock:
            self.write(axis, f"P{_bounded('target position', position, -8_388_607, 8_388_607)}")
            self.write(axis, "G")

    def slew(self, axis: Axis | str, positive: bool) -> None:
        with self._command_lock:
            self.set_direction(axis, positive)
            self.write(axis, "S")

    def home(self, axis: Axis | str, home_type: int) -> None:
        self.write(axis, f"H{_bounded('home type', home_type, 0, 1)}")

    def stop(self, axis: Axis | str, *, hard: bool = True) -> None:
        self.write(axis, "." if hard else ",")

    def emergency_stop(self) -> None:
        # Axis omission makes '.' stop both axes per the manual.
        with self._command_lock:
            self.transport.command(self._prefix(None) + ".")

    def position(self, axis: Axis | str) -> int:
        return self.query_int(axis, "VZ")

    def busy(self, axis: Axis | str) -> bool:
        value = self.query_int(axis, "VF")
        if value not in (0, 1):
            raise RuntimeError(f"invalid busy status for {Axis(axis).value}: {value!r}")
        return bool(value)

    def inputs(self, axis: Axis | str) -> int:
        return self.query_int(axis, "IR")

    def limits(self, axis: Axis | str) -> int:
        return self.query_int(axis, "VL")

    def error_code(self) -> int:
        with self._command_lock:
            reply = self.transport.command(self._prefix(None) + "!", expect_reply=True)
        assert reply is not None
        return self._integer_reply(self._prefix(None) + "!", reply)

    def firmware_version(self) -> str:
        with self._command_lock:
            reply = self.transport.command(self._prefix(None) + "$", expect_reply=True)
        assert reply is not None
        return reply

    def close(self) -> None:
        self.transport.close()
