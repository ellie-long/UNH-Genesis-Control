"""Thread-safe motion arbitration shared by EPICS and the joystick."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import IntEnum

from controller import Axis, PCL602Controller


class ControlMode(IntEnum):
    DISABLED = 0
    EPICS = 1
    JOYSTICK = 2


@dataclass(frozen=True)
class AxisConfig:
    minimum: int = -8_388_607
    maximum: int = 8_388_607
    home_positive: bool = False
    homing_enabled: bool = False
    coordinate_limit: int = 8_388_607
    limits_enabled: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.coordinate_limit <= 2_147_483_647 or not -self.coordinate_limit <= self.minimum < self.maximum <= self.coordinate_limit:
            raise ValueError("axis limits exceed configured coordinate range")

    def validate_target(self, target: int, current: int | None = None) -> None:
        if not -self.coordinate_limit <= target <= self.coordinate_limit:
            raise ValueError('target outside numerical coordinate range')
        if not self.limits_enabled:
            return
        # Permit gradual recovery toward the envelope from an outside position.
        if current is not None and ((current < target < self.minimum) or
                                    (self.maximum < target < current)):
            return
        if not self.minimum <= target <= self.maximum:
            raise ValueError(
                f"target {target} outside software limits [{self.minimum}, {self.maximum}]"
            )


class MotionCoordinator:
    """Own motion state and prevent EPICS/joystick command conflicts."""

    def __init__(self, controller: PCL602Controller, axes: dict[Axis, AxisConfig]):
        self.controller = controller
        self.axes = axes
        self.mode = ControlMode.DISABLED
        self.positions = {axis: 0 for axis in axes}
        self.slew_direction = {axis: 0 for axis in axes}
        self.joystick_connected = False
        self.joystick_active = False
        self.joystick_armed = False
        self._lock = threading.RLock()

    def replace_controller(self, controller: PCL602Controller) -> None:
        with self._lock:
            self.controller = controller
            self.controller.emergency_stop()
            self.slew_direction = {axis: 0 for axis in self.axes}
            self.mode = ControlMode.DISABLED
            self.joystick_active = False
            self.joystick_armed = False

    def update_position(self, axis: Axis, position: int) -> None:
        with self._lock:
            self.positions[axis] = position
            direction = self.slew_direction[axis]
            cfg = self.axes[axis]
            low = cfg.minimum if cfg.limits_enabled else -cfg.coordinate_limit
            high = cfg.maximum if cfg.limits_enabled else cfg.coordinate_limit
            if (direction < 0 and position <= low) or (direction > 0 and position >= high):
                self.controller.stop(axis, hard=True)
                self.slew_direction[axis] = 0

    def set_mode(self, mode: ControlMode | int) -> None:
        mode = ControlMode(mode)
        with self._lock:
            if mode != self.mode:
                self.controller.emergency_stop()
                self.slew_direction = {axis: 0 for axis in self.axes}
                self.joystick_active = False
                self.joystick_armed = False
                self.mode = mode

    def move_relative(self, axis: Axis, steps: int) -> None:
        with self._lock:
            if self.mode != ControlMode.EPICS:
                raise PermissionError("relative motion requires CONTROL_MODE=EPICS")
            if self.controller.busy(axis):
                raise RuntimeError(f"{axis.value} axis is already moving")
            self.positions[axis] = self.controller.position(axis)
            target = self.positions[axis] + int(steps)
            self.axes[axis].validate_target(target, self.positions[axis])
            self.controller.move_relative(axis, int(steps))

    def home(self, axis: Axis, home_type: int) -> None:
        with self._lock:
            if self.mode != ControlMode.EPICS:
                raise PermissionError("homing requires CONTROL_MODE=EPICS")
            if not self.axes[axis].homing_enabled:
                raise PermissionError("switch-based homing is disabled for this axis")
            if self.controller.busy(axis):
                raise RuntimeError(f"{axis.value} axis is already moving")
            self.controller.set_direction(axis, self.axes[axis].home_positive)
            self.controller.home(axis, home_type)

    def stop_axis(self, axis: Axis, *, hard: bool = True) -> None:
        with self._lock:
            self.controller.stop(axis, hard=hard)
            self.slew_direction[axis] = 0

    def emergency_stop(self) -> None:
        with self._lock:
            self.controller.emergency_stop()
            self.slew_direction = {axis: 0 for axis in self.axes}
            self.joystick_active = False

    def joystick_disconnected(self) -> None:
        with self._lock:
            if self.joystick_connected or any(self.slew_direction.values()):
                self.emergency_stop()
            self.joystick_connected = False
            self.joystick_active = False
            self.joystick_armed = False

    def joystick_command(self, directions: dict[Axis, int], deadman: bool) -> None:
        with self._lock:
            self.joystick_connected = True
            neutral = all(int(directions.get(axis, 0)) == 0 for axis in self.axes)
            if not deadman and neutral:
                # Require neutral with the deadman released after every mode
                # change or reconnect before allowing joystick motion.
                self.joystick_armed = True
            permitted = (
                self.mode == ControlMode.JOYSTICK
                and bool(deadman)
                and self.joystick_armed
            )
            self.joystick_active = permitted
            if not permitted:
                if any(self.slew_direction.values()):
                    # Deadman release or loss of permission is a safety stop.
                    self.emergency_stop()
                return
            for axis in self.axes:
                requested = int(directions.get(axis, 0))
                if requested not in (-1, 0, 1):
                    raise ValueError("joystick direction must be -1, 0 or 1")
                cfg = self.axes[axis]
                current = self.slew_direction[axis]
                if current and requested != current:
                    # ',' only slows an indexed move; '.' actually cancels it.
                    # Do not send a new move in the same joystick update as stop.
                    self.controller.stop(axis, hard=True)
                    self.slew_direction[axis] = 0
                    continue
                if not requested:
                    continue
                # Wait for stop completion, or completion of the previous bounded
                # segment. Do not query position or change direction while busy.
                if self.controller.busy(axis):
                    continue
                self.slew_direction[axis] = 0
                position = self.controller.position(axis)
                self.positions[axis] = position
                if cfg.limits_enabled:
                    target = cfg.maximum if requested > 0 else cfg.minimum
                else:
                    target = cfg.coordinate_limit if requested > 0 else -cfg.coordinate_limit
                distance = requested * (target - position)
                if distance < getattr(self.controller, 'step_quantum', lambda a: 1)(axis):
                    continue
                # A P move to the opposite extreme can overflow N = P - Z.
                # Explicit +/- and bounded N also make direction unambiguous.
                steps = requested * min(distance, 8_388_607)
                self.controller.move_relative(axis, steps)
                self.slew_direction[axis] = requested
