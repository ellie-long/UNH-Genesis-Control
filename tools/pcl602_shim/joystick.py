"""Linux/headless joystick worker based on the original pygame controller."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from controller import Axis
from motion import MotionCoordinator

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoystickConfig:
    index: int = 0
    x_axis: int = 0
    y_axis: int = 1
    invert_x: bool = False
    invert_y: bool = False
    deadzone: float = 0.4
    deadman_button: int = 1
    estop_button: int = 0
    poll_hz: int = 60
    reconnect_seconds: float = 2.0


class PygameJoystickWorker:
    def __init__(self, coordinator: MotionCoordinator, config: JoystickConfig):
        self.coordinator = coordinator
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="robot-joystick", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.coordinator.joystick_disconnected()

    def _direction(self, value: float, inverted: bool) -> int:
        if inverted:
            value = -value
        if value > self.config.deadzone:
            return 1
        if value < -self.config.deadzone:
            return -1
        return 0

    def _run(self) -> None:
        # Prevent pygame from trying to open a desktop/audio device on a headless Pi.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        try:
            import pygame
        except ImportError:
            LOG.error("pygame is required when joystick_enabled is true")
            self.coordinator.joystick_disconnected()
            return

        pygame.init()
        pygame.joystick.init()
        try:
            while not self._stop.is_set():
                if pygame.joystick.get_count() <= self.config.index:
                    self.coordinator.joystick_disconnected()
                    time.sleep(self.config.reconnect_seconds)
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    continue
                joystick = pygame.joystick.Joystick(self.config.index)
                joystick.init()
                LOG.info("Joystick connected: %s", joystick.get_name())
                try:
                    self._poll_device(pygame, joystick)
                except Exception:
                    LOG.exception("Joystick polling failed")
                    time.sleep(self.config.reconnect_seconds)
                finally:
                    self.coordinator.joystick_disconnected()
                    joystick.quit()
        finally:
            pygame.joystick.quit()
            pygame.quit()

    def _poll_device(self, pygame, joystick) -> None:
        clock = pygame.time.Clock()
        while not self._stop.is_set() and joystick.get_init():
            events = pygame.event.get()
            if any(event.type == pygame.JOYDEVICEREMOVED for event in events):
                return
            if self._button(joystick, self.config.estop_button):
                self.coordinator.emergency_stop()
                self.coordinator.set_mode(0)
                return
            deadman = (
                True if self.config.deadman_button < 0
                else self._button(joystick, self.config.deadman_button)
            )
            directions = {
                Axis.X: self._direction(
                    joystick.get_axis(self.config.x_axis), self.config.invert_x),
                Axis.Y: self._direction(
                    joystick.get_axis(self.config.y_axis), self.config.invert_y),
            }
            self.coordinator.joystick_command(directions, deadman)
            clock.tick(self.config.poll_hz)

    @staticmethod
    def _button(joystick, index: int) -> bool:
        return 0 <= index < joystick.get_numbuttons() and bool(joystick.get_button(index))
