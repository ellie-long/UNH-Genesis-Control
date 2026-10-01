#!/usr/bin/env python3
"""PCL602USB TCP bridge. Run directly; no EPICS installation is needed on the Pi."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path

from controller import Axis, PCL602Controller
from transport import SerialTransport
from profiled_controller import ProfiledController
import limit_settings
from motion import AxisConfig, ControlMode, MotionCoordinator
from joystick import JoystickConfig, PygameJoystickWorker

LOG = logging.getLogger('pcl602-shim')
MAX_LINE = 8192


class Refused(ValueError):
    """Valid connection, rejected operation; never retry motion automatically."""


def integer(value):
    if type(value) is not int:
        raise Refused('value must be an integer')
    return value


class Simulator:
    """Time-based controller substitute for network and motion tests, not physics."""
    def __init__(self):
        self.pos = {a: 0.0 for a in Axis}
        self.targets = dict(self.pos)
        self.speed = {a: 15000 for a in Axis}
        self.enabled = {a: True for a in Axis}
        self.output_register = {a: 0 for a in Axis}
        self.base = {a: 500 for a in Axis}
        self.accel = {a: 10000 for a in Axis}
        self.last = time.monotonic()

    def _tick(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        for a in Axis:
            delta = self.targets[a] - self.pos[a]
            self.pos[a] += math.copysign(min(abs(delta), dt * self.speed[a]), delta)

    def busy(self, a):
        self._tick()
        return self.pos[a] != self.targets[a]

    def position(self, a):
        self._tick()
        return round(self.pos[a])

    def move_absolute(self, a, target): self.targets[a] = target
    def move_relative(self, a, steps): self.move_absolute(a, self.position(a) + steps)
    def stop(self, a, *, hard=True):
        self._tick()
        self.targets[a] = self.pos[a]
    def emergency_stop(self):
        for a in Axis: self.stop(a)
    def set_position(self, a, value): self.pos[a] = self.targets[a] = value
    def set_motor_enabled(self, a, enabled): self.enabled[a] = enabled
    def motor_enabled(self, a): return self.enabled[a]
    def set_max_speed(self, a, value): self.speed[a] = value
    def set_acceleration(self, a, value): self.accel[a] = value
    def set_base_speed(self, a, value): self.base[a] = value
    def set_jog_speed(self, a, value): pass
    def set_output(self, a, output, enabled):
        mask = 1 << (output - 1)
        self.output_register[a] = (self.output_register[a] & ~mask) | (mask if enabled else 0)
    def outputs(self, a): return self.output_register[a]
    def profile_rates(self, a): return self.base[a], self.speed[a], self.accel[a]
    def limits(self, a): return 0
    def error_code(self): return 0
    def firmware_version(self): return 'PCL602 SIMULATOR'
    def close(self): pass


class Robot:
    def __init__(self, controller, config, limits_path=None):
        config = copy.deepcopy(config)
        self.limits_path = limits_path
        self.user_offsets = {a: 0 for a in Axis}
        self.profiled = bool(config.get('motion_profiles'))
        if self.profiled:
            limit_settings.load(limits_path, config['axes'])
            for a in Axis:
                cfg = config['axes'][a.value]
                travel = cfg['travel_cm']
                scale = cfg['steps_per_cm_256']
                if not math.isfinite(travel) or travel <= 0 or not math.isfinite(scale) or scale <= 0:
                    raise ValueError('travel and calibration must be finite and positive')
                if cfg['physical_sign'] not in (-1, 1):
                    raise ValueError('physical_sign must be -1 or 1')
                setting = cfg.setdefault('soft_limits', dict(low_cm=0, high_cm=travel, enabled=True))
                limits = limit_settings.bounds(cfg, setting['low_cm'], setting['high_cm'], setting['enabled'])
                cfg.update(minimum=limits.minimum, maximum=limits.maximum, limits_enabled=limits.limits_enabled)
            controller = ProfiledController(controller, config['axes'], config['motion_profiles'])
        self.controller = controller
        self.config = config
        for axis in ('X', 'Y'):
            for key in ('minimum', 'maximum'):
                integer(config['axes'][axis][key])
        self.axes = {a: AxisConfig(limits_enabled=config['axes'][a.value].get('limits_enabled', True), coordinate_limit=2_147_483_647 if self.profiled else 8_388_607, **{k: config['axes'][a.value][k]
                    for k in ('minimum', 'maximum')}) for a in Axis}
        self.motion = MotionCoordinator(controller, self.axes)
        self.lock = self.motion._lock
        self.owner = None
        self.deadline = 0.0
        self.watchdog = float(config.get('watchdog_seconds', 3.0))
        if not math.isfinite(self.watchdog) or self.watchdog < 0.5:
            raise ValueError('watchdog_seconds must be finite and >= 0.5')
        self.last_joystick = 0.0
        self.fault = ''
        self.reason = 'Startup: select a control mode to enable motion'
        self.values = {}
        # Stop first; do not zero positions or turn motor current off.
        controller.emergency_stop()
        for a in Axis:
            cfg = config['axes'][a.value]
            base = int(cfg.get('base_speed', 500))
            speed = int(cfg.get('max_speed', 15000))
            accel = int(cfg.get('acceleration', 10000))
            if not 1 <= base < speed <= 50000 or not 100 <= accel <= 9999999:
                raise ValueError('invalid base speed, maximum speed, or acceleration')
            if self.profiled:
                base, speed, accel = controller.rates[a]
            else:
                controller.set_base_speed(a, base)
                controller.set_max_speed(a, speed)
                controller.set_acceleration(a, accel)
            self.motion.update_position(a, controller.position(a))
            self.values[a.value] = dict(position=self.motion.positions[a], busy=False,
                position_fresh=True, limits=0, motor_enabled=controller.motor_enabled(a),
                minimum=self.axes[a].minimum, maximum=self.axes[a].maximum,
                max_speed=speed, acceleration=accel)
        self.firmware = controller.firmware_version()
        self.controller_error = 0

    def disable(self, reason):
        # Latch disabled even when USB has failed and the stop cannot be delivered.
        self.motion.mode = ControlMode.DISABLED
        self.motion.joystick_armed = False
        self.motion.joystick_active = False
        self.motion.slew_direction = {a: 0 for a in Axis}
        self.reason = reason
        try:
            self.controller.emergency_stop()
        except Exception as exc:
            self.fault = f'USB stop failed: {exc}'
            LOG.exception('Stop could not be delivered')

    def claim(self, owner):
        with self.lock:
            self.expire()
            if self.owner is not None:
                raise Refused('another IOC owns the controller')
            self.disable('New connection: select a control mode to enable motion')
            self.owner = owner
            self.deadline = time.monotonic() + self.watchdog

    def release(self, owner):
        with self.lock:
            if self.owner is owner:
                self.owner = None
                self.disable('IOC disconnected: motion disabled')

    def expire(self):
        with self.lock:
            if self.owner is not None and time.monotonic() >= self.deadline:
                owner, self.owner = self.owner, None
                self.disable('IOC heartbeat expired: motion disabled')
                try: owner.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            if (self.motion.mode == ControlMode.JOYSTICK and
                    self.motion.joystick_connected and
                    time.monotonic() - self.last_joystick > 1.0):
                self.disable('Joystick updates stopped: motion disabled')
                self.motion.joystick_connected = False

    def joystick_disconnected(self):
        with self.lock:
            if self.motion.mode == ControlMode.JOYSTICK:
                self.disable('Joystick disconnected: motion disabled')
            self.motion.joystick_connected = False
            self.motion.joystick_armed = False

    def emergency_stop(self):
        with self.lock: self.disable('Emergency stop: motion disabled')

    def set_mode(self, mode):
        # Called by the joystick worker only to disable after its stop button.
        if int(mode) != 0: raise Refused('enable control mode through IOC')
        self.emergency_stop()

    def joystick_command(self, directions, deadman):
        with self.lock:
            self.expire()
            self.last_joystick = time.monotonic()
            self.motion.joystick_connected = True
            if self.owner is None or self.fault:
                return
            try:
                if self.motion.mode == ControlMode.JOYSTICK:
                    for a in Axis:
                        if directions.get(a, 0) and not self.values[a.value]['motor_enabled']:
                            raise Refused('motor is disabled')
                self.motion.joystick_command(directions, deadman)
                self.last_joystick = time.monotonic()
            except Exception as exc:
                if self.profiled: self.fault = str(exc)
                self.disable(f'Joystick command failed: {exc}')
                raise

    def snapshot(self):
        if not self.fault:
            for a in Axis:
                state = self.values[a.value]
                if self.profiled: self.controller.verify_resolution(a)
                state['busy'] = self.controller.busy(a)
                state['position_fresh'] = not state['busy']
                # Existing firmware can reject position queries while moving.
                if not state['busy']:
                    state['position'] = self.controller.position(a)
                    self.motion.update_position(a, state['position'])
                    state['limits'] = self.controller.limits(a)
                    state['motor_enabled'] = self.controller.motor_enabled(a)
        if self.fault:
            for state in self.values.values(): state['position_fresh'] = False
        result = dict(protocol=2 if self.profiled else 1, firmware=self.firmware, healthy=not bool(self.fault),
                    error=self.fault or self.reason, mode=int(self.motion.mode),
                    joystick_connected=self.motion.joystick_connected,
                    joystick_active=self.motion.joystick_active,
                    controller_error=self.controller_error, axes=self.values)
        if self.profiled:
            result['limits_configurable'] = True
            result['speed_mode'] = self.controller.profile
            result['profile_speeds_cm_s'] = [p['speed_cm_s'] for p in self.config['motion_profiles']]
            for a in Axis:
                cfg = self.config['axes'][a.value]
                state = result['axes'][a.value]
                state.update(minimum=self.axes[a].minimum, maximum=self.axes[a].maximum,
                             limits_enabled=self.axes[a].limits_enabled,
                             user_offset=self.user_offsets[a],
                             steps_per_cm=cfg['steps_per_cm_256'],
                             physical_sign=cfg['physical_sign'],
                             referenced=self.controller.referenced[a] and not bool(self.fault),
                             resolution=256 // self.controller.factor[a],
                             speed_cm_s=self.controller.rates[a][1] * self.controller.factor[a] / cfg['steps_per_cm_256'])
        return result

    def dispatch(self, owner, request):
        with self.lock:
            self.expire()
            if self.owner is not owner: raise Refused('session expired; reconnect')
            if not isinstance(request, dict): raise Refused('request must be a JSON object')
            op = request.get('op')
            if not isinstance(op, str): raise Refused('op must be a string')
            allowed = {'READ', 'STOP_ALL', 'MODE', 'READ_ERROR', 'MOVE_REL',
                       'STOP', 'ZERO', 'MOTOR_ENABLE', 'MAX_SPEED', 'ACCEL', 'SPEED_MODE',
                       'SET_LIMITS', 'SET_MACHINE_POSITION'}
            if op not in allowed: raise Refused('unknown command; homing is disabled')
            if self.fault and op not in ('READ', 'STOP_ALL', 'STOP'):
                raise Refused('USB fault latched; inspect hardware and restart shim')
            try:
                result = self._execute(op, request)
            except (Refused, PermissionError, ValueError) as exc:
                raise Refused(str(exc)) from exc
            except Exception as exc:
                LOG.exception('Controller operation failed')
                self.fault = f'Controller communication failed: {exc}'
                self.disable(self.fault)
                raise Refused(self.fault) from exc
            self.deadline = time.monotonic() + self.watchdog
            return result

    def _execute(self, op, req):
        if op == 'READ': return self.snapshot()
        if op == 'STOP_ALL':
            self.disable('Emergency stop: motion disabled')
            return None
        if op == 'SPEED_MODE':
            if not self.profiled: raise Refused('speed profiles not configured')
            index = integer(req.get('value'))
            if not 0 <= index <= 2: raise Refused('invalid speed profile')
            if any(self.controller.busy(a) for a in Axis):
                raise Refused('speed changes require both axes idle')
            # Disarm and disable first; a held joystick must never resume on switch.
            self.disable('Speed changed: reselect control mode')
            if self.fault: raise Refused(self.fault)
            try:
                self.controller.set_profile(index)
                for a in Axis:
                    _, speed, accel = self.controller.rates[a]
                    self.values[a.value].update(max_speed=speed, acceleration=accel)
            except Exception as exc:
                self.fault = f'Speed change failed: {exc}'
                self.disable(self.fault)
                raise Refused(self.fault) from exc
            return index
        if op == 'MODE':
            mode = ControlMode(integer(req.get('value')))
            if mode == ControlMode.JOYSTICK and not self.motion.joystick_connected:
                raise Refused('joystick is not connected')
            if mode != self.motion.mode and mode != ControlMode.DISABLED:
                # Keep arbitration disabled until BOTH currents are verified.
                # This runs under the same lock as EPICS and joystick commands.
                # Never remove holding current on a partial failure.
                self.disable('Preparing control mode')
                try:
                    if self.fault:
                        raise Refused(self.fault)
                    if any(self.controller.busy(a) for a in Axis):
                        raise Refused('axes still stopping; select mode again when idle')
                    for a in Axis:
                        self.controller.set_motor_enabled(a, True)
                        enabled = bool(self.controller.motor_enabled(a))
                        self.values[a.value]['motor_enabled'] = enabled
                        if not enabled:
                            raise Refused(f'{a.value} motor current did not enable')
                    self.motion.set_mode(mode)
                except Exception as exc:
                    self.disable(f'Control mode enable failed: {exc}')
                    raise
            else:
                self.motion.set_mode(mode)
            self.reason = ''
            return int(mode)
        if op == 'READ_ERROR':
            self.controller_error = self.controller.error_code()
            return self.controller_error
        a = Axis(req.get('axis'))
        if op == 'STOP':
            self.motion.joystick_armed = False
            self.motion.joystick_active = False
            self.motion.stop_axis(a, hard=True)
            return None
        if op in ('SET_LIMITS', 'SET_MACHINE_POSITION'):
            if not self.profiled:
                raise Refused('calibration controls require speed-profile shim')
            if self.motion.mode != ControlMode.DISABLED or any(self.controller.busy(axis) for axis in Axis):
                raise Refused('calibration requires Disabled mode and idle axes')
            cfg = self.config['axes'][a.value]
            if op == 'SET_LIMITS':
                low, high, enabled = req.get('low_cm'), req.get('high_cm'), req.get('enabled')
                limits = limit_settings.bounds(cfg, low, high, enabled)
                updated = copy.deepcopy(self.config['axes'])
                updated[a.value]['soft_limits'] = dict(low_cm=low, high_cm=high, enabled=enabled)
                try:
                    limit_settings.save(self.limits_path, updated)
                except OSError as exc:
                    raise Refused(f'limits not saved: {exc}') from exc
                # Commit both enforcement layers only after the durable write succeeds.
                cfg.update(updated[a.value])
                cfg.update(minimum=limits.minimum, maximum=limits.maximum, limits_enabled=enabled)
                self.axes[a] = limits
            else:
                cm = req.get('value_cm')
                if type(cm) not in (int, float) or not math.isfinite(cm):
                    raise Refused('machine position must be finite centimeters')
                steps = cm * cfg['steps_per_cm_256'] * cfg['physical_sign']
                if not math.isfinite(steps) or abs(steps) > limit_settings.COORDINATE_LIMIT:
                    raise Refused('machine position exceeds coordinate range')
                self.controller.set_position(a, round(steps))
                self.user_offsets[a] = 0
                self.motion.update_position(a, self.controller.position(a))
            self.reason = ''
            return None
        if op == 'ZERO':
            if self.motion.mode != ControlMode.DISABLED:
                raise Refused('zero requires Disabled mode')
            if self.controller.busy(a): raise Refused('axis is moving')
            if self.profiled:
                # Work/display zero never rewrites the machine reference or its limits.
                self.user_offsets[a] = self.controller.position(a)
            else:
                self.axes[a].validate_target(0)
                self.controller.set_position(a, 0)
            self.motion.update_position(a, self.controller.position(a))
            return None
        value = integer(req.get('value'))
        if op == 'MOVE_REL':
            if not self.controller.motor_enabled(a): raise Refused('motor is disabled')
            if self.controller.busy(a): raise Refused('axis is moving')
            self.motion.move_relative(a, value)
        elif op == 'MOTOR_ENABLE':
            if value not in (0, 1): raise Refused('motor enable must be 0 or 1')
            if self.motion.mode != ControlMode.DISABLED:
                raise Refused('motor enable changes require Disabled mode')
            if self.controller.busy(a): raise Refused('axis is moving')
            self.controller.set_motor_enabled(a, bool(value))
            self.values[a.value]['motor_enabled'] = bool(value)
        elif op in ('MAX_SPEED', 'ACCEL'):
            if self.profiled: raise Refused('use Slow/Medium/Fast profiles')
            if self.controller.busy(a): raise Refused('axis is moving')
            if op == 'MAX_SPEED':
                if not int(self.config['axes'][a.value].get('base_speed', 500)) < value <= 50000:
                    raise Refused('max speed must exceed base speed and be <= 50000')
                self.controller.set_max_speed(a, value)
                self.values[a.value]['max_speed'] = value
            else:
                if not 100 <= value <= 9999999: raise Refused('acceleration out of range')
                self.controller.set_acceleration(a, value)
                self.values[a.value]['acceleration'] = value
        return None


class Handler(socketserver.StreamRequestHandler):
    def send(self, value):
        self.wfile.write((json.dumps(value, allow_nan=False) + '\n').encode('ascii'))
        self.wfile.flush()

    def handle(self):
        robot = self.server.robot
        self.request.settimeout(robot.watchdog)
        try:
            try: robot.claim(self.request)
            except Refused as exc:
                self.send(dict(ok=False, error=str(exc)))
                return
            while True:
                raw = self.rfile.readline(MAX_LINE + 1)
                if not raw: return
                if len(raw) > MAX_LINE or not raw.endswith(b'\n'):
                    return  # Truncated or oversized frames invalidate the session.
                try:
                    request = json.loads(raw)
                    result = robot.dispatch(self.request, request)
                    self.send(dict(ok=True, result=result))
                except (ValueError, UnicodeError) as exc:
                    self.send(dict(ok=False, error=str(exc)))
        except (OSError, TimeoutError):
            pass
        finally:
            robot.release(self.request)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, robot):
        self.robot = robot
        self.stopping = threading.Event()
        self.guard = None
        super().__init__(address, Handler)
        self.guard = threading.Thread(target=self._watch, daemon=True)
        self.guard.start()

    def _watch(self):
        while not self.stopping.wait(0.05): self.robot.expire()

    def server_close(self):
        self.stopping.set()
        if self.guard is not None: self.guard.join(timeout=2)
        with self.robot.lock:
            self.robot.disable('Shim shutting down')
            if self.robot.owner is not None:
                try: self.robot.owner.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                self.robot.owner = None
        super().server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = json.loads(Path(args.config).read_text())
    controller = Simulator() if args.simulate else PCL602Controller(
        SerialTransport(config['serial_port'], timeout=float(config.get('serial_timeout', .35)),
                        xonxoff=bool(config.get('xonxoff', False)),
                        command_delay=float(config.get('command_delay', .05))),
        int(config.get('address', 0)))
    joystick = None
    try:
        config_path = Path(args.config).resolve()
        limits_path = config_path.with_name(config_path.stem + ('.sim-limits.json' if args.simulate else '.limits.json'))
        robot = Robot(controller, config, limits_path=limits_path)
        with Server((args.host or config.get('host', '127.0.0.1'),
                     args.port if args.port is not None else config.get('port', 1702)), robot) as server:
            if config.get('joystick_enabled', True) and not args.simulate:
                joy_cfg = JoystickConfig(**config.get('joystick', {}))
                if joy_cfg.deadman_button < 0 or joy_cfg.poll_hz < 10:
                    raise ValueError('joystick requires a deadman button and poll_hz >= 10')
                joystick = PygameJoystickWorker(robot, joy_cfg)
                joystick.start()
            def stop(signum, frame):
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            LOG.info('Listening on %s:%s (simulate=%s)', *server.server_address, args.simulate)
            server.serve_forever(poll_interval=.1)
    finally:
        if joystick: joystick.close()
        try: controller.emergency_stop()
        finally: controller.close()


if __name__ == '__main__': main()
