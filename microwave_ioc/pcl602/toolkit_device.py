"""PCL602 robot IOC over TCP. All USB, motion limits and joystick logic live on the Pi.

Settings: module: devices.instruments.pcl602; ip; port (1702); timeout (3);
channels: [X, Y]; optional axis_map. No pyserial or pygame required on IOC host.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import socket
import threading
from types import SimpleNamespace
from decimal import Decimal, ROUND_HALF_UP
from softioc import alarm, builder
from devices.base_device import BaseDevice


class DeviceRefused(ValueError):
    """Shim rejected an operation; the network connection remains usable."""


class DeviceConnection:
    def __init__(self, host, port=1702, timeout=3.0):
        self.lock = threading.RLock()
        self.sock = socket.create_connection((host, int(port)), float(timeout))
        self.sock.settimeout(float(timeout))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = self.sock.makefile('rb')
        self.closed = False

    def request(self, op, **fields):
        with self.lock:
            if self.closed: raise OSError('shim connection is closed')
            try:
                wire = (json.dumps(dict(op=op, **fields), allow_nan=False) + '\n').encode('ascii')
                self.sock.sendall(wire)
                raw = self.reader.readline(8193)
                if len(raw) > 8192 or not raw.endswith(b'\n'):
                    raise OSError('incomplete or oversized shim response')
                reply = json.loads(raw)
                if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
                    raise OSError('invalid shim response')
                if not reply['ok']:
                    raise DeviceRefused(str(reply.get('error', 'command rejected')))
                if 'result' not in reply: raise OSError('missing shim result')
                return reply['result']
            except DeviceRefused:
                raise
            except Exception as exc:
                # A lost response makes execution uncertain. Close; never resend.
                self.close()
                raise OSError(f'shim communication failed: {exc}') from exc

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                try: self.sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                self.reader.close()
                self.sock.close()


class LocalSimulationConnection:
    """In-process PCL602 simulation for Genesis off-lab integration.

    This implements the request interface used by Device without opening
    sockets, starting a Pi service, loading USB libraries, or moving motors.
    """

    def __init__(self, settings):
        self.closed = False

        self.steps_per_cm = float(
            settings.get("steps_per_cm", 10000)
        )

        if (
            not math.isfinite(self.steps_per_cm)
            or self.steps_per_cm <= 0
        ):
            raise ValueError(
                "steps_per_cm must be finite and positive"
            )

        speeds = settings.get(
            "simulation_profile_speeds_cm_s",
            [0.1, 0.5, 1.0],
        )

        if (
            not isinstance(speeds, list)
            or len(speeds) != 3
            or any(
                not math.isfinite(float(value))
                or float(value) <= 0
                for value in speeds
            )
        ):
            raise ValueError(
                "simulation_profile_speeds_cm_s "
                "must contain three positive values"
            )

        self.profile_speeds = [
            float(value) for value in speeds
        ]

        self.mode = 0
        self.speed_mode = 0
        self.controller_error = 0

        minimum = int(
            settings.get(
                "simulation_minimum_steps",
                -1_000_000,
            )
        )

        maximum = int(
            settings.get(
                "simulation_maximum_steps",
                1_000_000,
            )
        )

        if minimum >= maximum:
            raise ValueError(
                "invalid local simulation coordinate range"
            )

        self.axes = {}

        for axis in ("X", "Y"):
            self.axes[axis] = {
                "position": 0,
                "minimum": minimum,
                "maximum": maximum,
                "limits": 0,
                "busy": False,
                "motor_enabled": False,
                "max_speed": 15_000,
                "acceleration": 10_000,
                "user_offset": 0,
                "referenced": True,
                "steps_per_cm": self.steps_per_cm,
                "physical_sign": 1,
                "resolution": 256,
                "limits_enabled": False,
                "position_fresh": True,
            }

    def close(self):
        self.closed = True

    def _axis(self, name):
        name = str(name).upper()

        if name not in self.axes:
            raise DeviceRefused(
                f"unknown axis {name}"
            )

        return self.axes[name]

    def _state(self):
        axes = copy.deepcopy(self.axes)

        for data in axes.values():
            data["speed_cm_s"] = (
                data["max_speed"]
                / data["steps_per_cm"]
            )

        return {
            "protocol": 2,
            "healthy": True,
            "error": "",
            "firmware": "GENESIS-LOCAL-SIM",
            "mode": self.mode,
            "speed_mode": self.speed_mode,
            "profile_speeds_cm_s": list(
                self.profile_speeds
            ),
            "limits_configurable": False,
            "joystick_connected": False,
            "joystick_active": False,
            "controller_error": self.controller_error,
            "axes": axes,
        }

    def request(self, op, **fields):
        if self.closed:
            raise OSError(
                "local simulation connection is closed"
            )

        op = str(op).upper()

        if op == "READ":
            return self._state()

        if op == "READ_ERROR":
            value = self.controller_error
            self.controller_error = 0
            return value

        if op == "STOP_ALL":
            self.mode = 0

            for data in self.axes.values():
                data["busy"] = False
                data["motor_enabled"] = False

            return True

        if op == "MODE":
            value = int(fields["value"])

            if value not in (0, 1, 2):
                raise DeviceRefused(
                    "mode must be Disabled, EPICS, or Joystick"
                )

            if value == 2:
                raise DeviceRefused(
                    "joystick is unavailable in local simulation"
                )

            self.mode = value
            return value

        if op == "SPEED_MODE":
            value = int(fields["value"])

            if value not in (0, 1, 2):
                raise DeviceRefused(
                    "speed mode must be 0, 1, or 2"
                )

            self.speed_mode = value
            return value

        if op == "MOTOR_ENABLE":
            if self.mode != 0:
                raise DeviceRefused(
                    "motor enable changes require Disabled mode"
                )

            axis = self._axis(fields["axis"])
            axis["motor_enabled"] = bool(
                int(fields["value"])
            )
            return int(axis["motor_enabled"])

        if op == "MOVE_REL":
            axis = self._axis(fields["axis"])

            if self.mode != 1:
                raise DeviceRefused(
                    "relative motion requires EPICS mode"
                )

            if not axis["motor_enabled"]:
                raise DeviceRefused(
                    "relative motion requires motor enabled"
                )

            if not axis["position_fresh"]:
                raise DeviceRefused(
                    "relative motion requires fresh position"
                )

            value = int(fields["value"])

            target = axis["position"] + value

            if not (
                axis["minimum"]
                <= target
                <= axis["maximum"]
            ):
                raise DeviceRefused(
                    "simulated move outside coordinate range"
                )

            # Simulation completes immediately.
            axis["busy"] = True
            axis["position"] = target
            axis["busy"] = False

            return axis["position"]

        if op == "STOP":
            axis = self._axis(fields["axis"])
            axis["busy"] = False
            return True

        if op == "ZERO":
            axis = self._axis(fields["axis"])
            axis["position"] = 0
            axis["user_offset"] = 0
            return True

        if op == "MAX_SPEED":
            axis = self._axis(fields["axis"])
            value = int(fields["value"])

            if value < 1 or value > 50_000:
                raise DeviceRefused(
                    "max speed outside IOC range"
                )

            axis["max_speed"] = value
            return value

        if op == "ACCEL":
            axis = self._axis(fields["axis"])
            value = int(fields["value"])

            if value < 100:
                raise DeviceRefused(
                    "acceleration outside IOC range"
                )

            axis["acceleration"] = value
            return value

        if op in (
            "SET_LIMITS",
            "SET_MACHINE_POSITION",
        ):
            raise DeviceRefused(
                "calibration controls are disabled "
                "in Genesis local simulation"
            )

        raise DeviceRefused(
            f"unsupported local simulation operation {op}"
        )


class Device(BaseDevice):
    def __init__(self, device_name, settings):
        self.lock = threading.RLock()
        self.last_error = ''
        self.simulation = bool(
            settings.get('simulation', False)
        )
        self.steps_per_cm = float(settings.get('steps_per_cm', 10000))
        if not math.isfinite(self.steps_per_cm) or self.steps_per_cm <= 0:
            raise ValueError('steps_per_cm must be finite and positive')
        self.scales = {'X': self.steps_per_cm, 'Y': self.steps_per_cm}
        self.signs = {'X': 1, 'Y': 1}
        self.resolutions = {'X': 256, 'Y': 256}
        self.protocol = 0
        self.limit_drafts = {}
        self.machine_drafts = {}
        self.limits_configurable = False
        self.jog_distance_cm = min(0.01, 8388607 / self.steps_per_cm)
        super().__init__(device_name, settings)
        axes = [self._axis(ch) for ch in self._skip_none_channels()]
        if set(axes) != {'X', 'Y'} or len(axes) != 2:
            raise ValueError('configure exactly one X and one Y channel')

    def _axis(self, channel):
        return self.settings.get('axis_map', {}).get(channel, channel).upper()

    def _create_pvs(self):
        self.pvs['SIMULATION'] = builder.boolIn(
            'SIMULATION',
            ZNAM='Physical',
            ONAM='Local Simulation')
        self.pvs['SIMULATION'].set(
            self.simulation)
        self.pvs['STEPS_PER_CM'] = builder.aIn(
            'STEPS_PER_CM', initial_value=self.steps_per_cm, EGU='steps/cm', PREC=3)
        self.pvs['JOG_DISTANCE_CM'] = builder.aOut(
            'JOG_DISTANCE_CM', initial_value=self.jog_distance_cm, EGU='cm', PREC=4,
            DRVL=0, DRVH=8388607 / self.steps_per_cm, always_update=True,
            on_update_name=self.do_sets)
        for mode in ('SLOW','MEDIUM','FAST'):
            self.pvs['SPEED_' + mode + '_CM_S'] = builder.aIn('SPEED_' + mode + '_CM_S', EGU='cm/s', PREC=2)
        self.pvs['SPEED_MODE'] = builder.mbbOut(
            'SPEED_MODE', 'Slow', 'Medium', 'Fast', initial_value=0,
            on_update_name=self.do_sets)
        self.pvs['LIMITS_CONFIGURABLE'] = builder.boolIn('LIMITS_CONFIGURABLE')
        for channel in self._skip_none_channels():
            for suffix in ('_MACHINE_CM',):
                self.pvs[channel + suffix] = builder.aIn(channel + suffix, EGU='cm', PREC=4)
            self.pvs[channel + '_SOFT_LIMITS_ENABLED'] = builder.boolIn(
                channel + '_SOFT_LIMITS_ENABLED', ZNAM='Disabled', ONAM='Enabled')
            for suffix in ('_LIMIT_LOW_SET_CM', '_LIMIT_HIGH_SET_CM', '_MACHINE_SET_CM'):
                self.pvs[channel + suffix] = builder.aOut(
                    channel + suffix, EGU='cm', PREC=4, always_update=True, on_update_name=self.do_sets)
            self.pvs[channel + '_LIMIT_ENABLE_SET'] = builder.boolOut(
                channel + '_LIMIT_ENABLE_SET', initial_value=True, ZNAM='Disabled', ONAM='Enabled',
                on_update_name=self.do_sets)
            for suffix in ('_LIMIT_APPLY', '_LIMIT_RELOAD', '_MACHINE_APPLY'):
                self.pvs[channel + suffix] = builder.boolOut(
                    channel + suffix, always_update=True, on_update_name=self.do_sets)
            self.pvs[channel + '_REFERENCED'] = builder.boolIn(channel + '_REFERENCED')
            self.pvs[channel + '_STEPS_PER_CM'] = builder.aIn(channel + '_STEPS_PER_CM', EGU='steps/cm', PREC=3)
            self.pvs[channel + '_RESOLUTION'] = builder.longIn(channel + '_RESOLUTION')
            self.pvs[channel + '_SPEED_CM_S'] = builder.aIn(channel + '_SPEED_CM_S', EGU='cm/s', PREC=2)
            for suffix in ('_CM', '_LOW_CM', '_HIGH_CM'):
                self.pvs[channel + suffix] = builder.aIn(
                    channel + suffix, EGU='cm', PREC=4, **self.sevr)
            self.pvs[channel + '_JOG'] = builder.longOut(
                channel + '_JOG', DRVL=-1, DRVH=1, always_update=True,
                on_update_name=self.do_sets)
            limit = 2147483647 if self.settings.get('require_speed_profiles', False) else 8388607
            cfg = SimpleNamespace(minimum=-limit, maximum=limit)
            self.pvs[channel + "_MI"] = builder.longIn(
                channel + "_MI", LOPR=cfg.minimum, HOPR=cfg.maximum,
                EGU="steps", **self.sevr)
            self.pvs[channel + "_LOW_LIMIT"] = builder.longIn(
                channel + "_LOW_LIMIT", initial_value=cfg.minimum, EGU="steps")
            self.pvs[channel + "_HIGH_LIMIT"] = builder.longIn(
                channel + "_HIGH_LIMIT", initial_value=cfg.maximum, EGU="steps")
            self.pvs[channel + "_MOVE_REL"] = builder.longOut(
                channel + "_MOVE_REL", DRVL=-8_388_607, DRVH=8_388_607,
                EGU="steps", always_update=True, on_update_name=self.do_sets)
            self.pvs[channel + "_POSITION_FRESH"] = builder.boolIn(
                channel + "_POSITION_FRESH", ZNAM="Cached", ONAM="Fresh")
            self.pvs[channel + "_BUSY"] = builder.boolIn(
                channel + "_BUSY", ZNAM="Idle", ONAM="Moving")
            self.pvs[channel + "_LIMITS"] = builder.longIn(
                channel + "_LIMITS", LOPR=0, HOPR=3)
            self.pvs[channel + "_MOTOR_ENABLE"] = builder.boolOut(
                channel + "_MOTOR_ENABLE", ZNAM="Off", ONAM="On",
                on_update_name=self.do_sets)
            axis_settings = {}
            self.pvs[channel + "_MAX_SPEED"] = builder.longOut(
                channel + "_MAX_SPEED", DRVL=1, DRVH=50_000, EGU="steps/s",
                initial_value=int(axis_settings.get("max_speed", 15_000)),
                on_update_name=self.do_sets)
            self.pvs[channel + "_ACCEL"] = builder.longOut(
                channel + "_ACCEL", DRVL=100, DRVH=9_999_999, EGU="steps/s2",
                initial_value=int(axis_settings.get("acceleration", 10_000)),
                on_update_name=self.do_sets)
            for action in ("HOME", "STOP", "ZERO"):
                self.pvs[channel + "_" + action] = builder.boolOut(
                    channel + "_" + action, always_update=True,
                    on_update_name=self.do_sets)

        self.pvs["CONTROL_MODE"] = builder.mbbOut(
            "CONTROL_MODE", "Disabled", "EPICS", "Joystick",
            initial_value=0, on_update_name=self.do_sets)
        self.pvs["COMM_STATUS"] = builder.boolIn(
            "COMM_STATUS", ZNAM="Disconnected", ONAM="Connected")
        self.pvs["LAST_ERROR"] = builder.stringIn("LAST_ERROR")
        self.pvs["CONTROLLER_ERROR"] = builder.longIn(
            "CONTROLLER_ERROR", LOPR=0, HOPR=16_383)
        self.pvs["READ_ERROR"] = builder.boolOut(
            "READ_ERROR", always_update=True, on_update_name=self.do_sets)
        self.pvs["FIRMWARE"] = builder.stringIn("FIRMWARE")
        self.pvs["ESTOP"] = builder.boolOut(
            "ESTOP", ZNAM="Ready", ONAM="STOP", always_update=True,
            on_update_name=self.do_sets)
        self.pvs["JOYSTICK_CONNECTED"] = builder.boolIn(
            "JOYSTICK_CONNECTED", ZNAM="Disconnected", ONAM="Connected")
        self.pvs["JOYSTICK_ACTIVE"] = builder.boolIn(
            "JOYSTICK_ACTIVE", ZNAM="Inactive", ONAM="Active")

    def _create_connection(self):
        if self.simulation:
            return LocalSimulationConnection(
                self.settings)
        return DeviceConnection(
            self.settings['ip'],
            self.settings.get('port', 1702),
            self.settings.get('timeout', 3.0))

    def _set_comm(self, connected, message=None):
        self.pvs['COMM_STATUS'].set(bool(connected))
        if not connected:
            self.limits_configurable = False
            self.pvs['LIMITS_CONFIGURABLE'].set(False)
        if message is not None:
            self.last_error = str(message)
        self.pvs['LAST_ERROR'].set(self.last_error[:39])
        for channel in self._skip_none_channels():
            for suffix in ('_MACHINE_CM', '_SOFT_LIMITS_ENABLED', '_MI', '_CM', '_LOW_CM', '_HIGH_CM', '_BUSY', '_LIMITS', '_LOW_LIMIT', '_HIGH_LIMIT', '_POSITION_FRESH'):
                self.pvs[channel + suffix].set_alarm(
                    severity=alarm.NO_ALARM if connected else alarm.MAJOR_ALARM,
                    alarm=alarm.NO_ALARM if connected else alarm.COMM_ALARM)
            if not connected:
                self.pvs[channel + '_POSITION_FRESH'].set(False)
                self.pvs[channel + '_REFERENCED'].set(False)

    def _apply(self, state):
        # Output readbacks must not invoke command callbacks (set defaults to process=True).
        if state['protocol'] not in (1, 2): raise OSError('unsupported shim protocol')
        if self.settings.get('require_speed_profiles', False) and state['protocol'] != 2:
            raise OSError('update Pi shim and config for speed profiles')
        self.protocol = state['protocol']
        self.limits_configurable = bool(state.get('limits_configurable', False))
        self.pvs['LIMITS_CONFIGURABLE'].set(self.limits_configurable)
        if self.protocol == 2:
            self.pvs['SPEED_MODE'].set(state['speed_mode'], process=False)
            for mode, speed in zip(('SLOW','MEDIUM','FAST'), state['profile_speeds_cm_s']):
                self.pvs['SPEED_' + mode + '_CM_S'].set(speed)
        for channel in self._skip_none_channels():
            data = state['axes'][self._axis(channel)]
            self.scales[channel] = data.get('steps_per_cm', self.steps_per_cm)
            self.signs[channel] = data.get('physical_sign', 1)
            self.resolutions[channel] = data.get('resolution', 256)
            scale = self.scales[channel]
            sign = self.signs[channel]
            low, high = sorted((sign * data['minimum'] / scale, sign * data['maximum'] / scale))
            if channel not in self.limit_drafts:
                self._load_limit_draft(channel, low, high, data.get('limits_enabled', True))
            for suffix, value in [('_CM', sign * (data['position'] - data.get('user_offset', 0)) / scale),
                                  ('_MACHINE_CM', sign * data['position'] / scale),
                                  ('_SOFT_LIMITS_ENABLED', data.get('limits_enabled', True)), ('_LOW_CM', low), ('_HIGH_CM', high),
                                  ('_STEPS_PER_CM', scale), ('_RESOLUTION', self.resolutions[channel]),
                                  ('_REFERENCED', data.get('referenced', True)),
                                  ('_SPEED_CM_S', data.get('speed_cm_s', data['max_speed'] / scale))]:
                self.pvs[channel + suffix].set(value)
            fields = {'_MI': 'position', '_BUSY': 'busy', '_LIMITS': 'limits',
                      '_LOW_LIMIT': 'minimum', '_HIGH_LIMIT': 'maximum',
                      '_MOTOR_ENABLE': 'motor_enabled', '_MAX_SPEED': 'max_speed',
                      '_ACCEL': 'acceleration', '_POSITION_FRESH': 'position_fresh'}
            for suffix, field in fields.items():
                if suffix in ('_MOTOR_ENABLE', '_MAX_SPEED', '_ACCEL'):
                    self.pvs[channel + suffix].set(data[field], process=False)
                else:
                    self.pvs[channel + suffix].set(data[field])
        for pv, field in [('FIRMWARE', 'firmware'), ('CONTROL_MODE', 'mode'),
                          ('JOYSTICK_CONNECTED', 'joystick_connected'),
                          ('JOYSTICK_ACTIVE', 'joystick_active'),
                          ('CONTROLLER_ERROR', 'controller_error')]:
            value = state[field]
            if pv == 'CONTROL_MODE':
                self.pvs[pv].set(value, process=False)
            else:
                self.pvs[pv].set(value[:39] if isinstance(value, str) else value)
        self._set_comm(state['healthy'], state['error'] or None)

    def _load_limit_draft(self, channel, low, high, enabled):
        self.limit_drafts[channel] = dict(low_cm=low, high_cm=high, enabled=bool(enabled))
        for suffix, value in (('_LIMIT_LOW_SET_CM', low), ('_LIMIT_HIGH_SET_CM', high),
                              ('_LIMIT_ENABLE_SET', bool(enabled))):
            self.pvs[channel + suffix].set(value, process=False)

    def _restore_calibration_entry(self, name):
        if '_' not in name: return
        channel, action = name.split('_', 1)
        if action == 'MACHINE_SET_CM':
            self.pvs[name].set(self.machine_drafts.get(channel, 0), process=False)
        elif action in ('LIMIT_LOW_SET_CM', 'LIMIT_HIGH_SET_CM', 'LIMIT_ENABLE_SET'):
            keys = {'LIMIT_LOW_SET_CM': 'low_cm', 'LIMIT_HIGH_SET_CM': 'high_cm', 'LIMIT_ENABLE_SET': 'enabled'}
            self.pvs[name].set(self.limit_drafts.get(channel, {}).get(keys[action], 0), process=False)

    def connect(self):
        with self.lock:
            self.close()
            try:
                self.t = self._create_connection()
                self._apply(self.t.request('READ'))
                return True
            except Exception as exc:
                self.close()
                self._set_comm(False, str(exc))
                logging.warning('PCL602 connection failed: %s', exc)
                return False

    def reconnect(self):
        return self.connect()

    def close(self):
        with self.lock:
            if self.t is not None:
                self.t.close()
                self.t = None

    def read_outs(self):
        with self.lock:
            if self.t is not None: self._apply(self.t.request('READ'))

    def _post_connect(self):
        self.read_outs()

    def _jog_steps(self, distance, channel='X'):
        distance = float(distance)
        scale = self.scales[channel]
        steps = distance * scale
        if not math.isfinite(distance) or distance <= 0 or not math.isfinite(steps):
            raise DeviceRefused('Move distance must be finite and positive')
        if steps < 0.5 or steps > 8388607 * (256 // self.resolutions[channel]):
            raise DeviceRefused('Move distance outside step range')
        return int((Decimal(str(distance)) * Decimal(str(scale))).to_integral_value(rounding=ROUND_HALF_UP))

    def do_sets(self, new_value, pv):
        name = str(pv).rsplit(':', 1)[-1]
        with self.lock:
            try:
                if name == 'JOG_DISTANCE_CM':
                    # Editing the distance NEVER initiates motion or requires transport.
                    try:
                        self._jog_steps(new_value)
                        self.jog_distance_cm = float(new_value)
                    finally:
                        self.pvs[name].set(self.jog_distance_cm, process=False)
                    return
                if self.t is None: raise OSError('IOC is disconnected; command was not sent')
                if name == 'CONTROL_MODE':
                    text = str(new_value).upper()
                    mode = {'DISABLED': 0, 'EPICS': 1, 'JOYSTICK': 2}.get(text)
                    if mode is None: mode = int(new_value)
                    self.t.request('MODE', value=mode)
                elif name == 'SPEED_MODE':
                    text = str(new_value).upper()
                    index = {'SLOW': 0, 'MEDIUM': 1, 'FAST': 2}.get(text)
                    if index is None: index = int(new_value)
                    self.t.request('SPEED_MODE', value=index)
                elif name == 'ESTOP':
                    self.t.request('STOP_ALL')
                    self.pvs[name].set(False, process=False)
                elif name == 'READ_ERROR':
                    self.pvs['CONTROLLER_ERROR'].set(self.t.request('READ_ERROR'))
                    self.pvs[name].set(False, process=False)
                else:
                    channel, action = name.split('_', 1)
                    axis = self._axis(channel)
                    if action in ('LIMIT_LOW_SET_CM', 'LIMIT_HIGH_SET_CM', 'LIMIT_ENABLE_SET', 'MACHINE_SET_CM'):
                        if not self.limits_configurable:
                            raise DeviceRefused('Update Pi shim for limit controls')
                        if action == 'LIMIT_ENABLE_SET':
                            if new_value not in (0, 1): raise DeviceRefused('Enable must be 0 or 1')
                            self.limit_drafts[channel]['enabled'] = bool(new_value)
                        else:
                            value = float(new_value)
                            if not math.isfinite(value): raise DeviceRefused('Enter a finite centimeter value')
                            if action == 'MACHINE_SET_CM': self.machine_drafts[channel] = value
                            else: self.limit_drafts[channel]['low_cm' if action == 'LIMIT_LOW_SET_CM' else 'high_cm'] = value
                        self.pvs[name].set(new_value, process=False)
                    elif action in ('LIMIT_APPLY', 'LIMIT_RELOAD', 'MACHINE_APPLY'):
                        self.pvs[name].set(False, process=False)
                        if not new_value: return
                        if not self.limits_configurable:
                            raise DeviceRefused('Update Pi shim for limit controls')
                        if action == 'LIMIT_APPLY':
                            self.t.request('SET_LIMITS', axis=axis, **self.limit_drafts[channel])
                        elif action == 'MACHINE_APPLY':
                            if channel not in self.machine_drafts:
                                raise DeviceRefused('Enter measured machine position first')
                            self.t.request('SET_MACHINE_POSITION', axis=axis, value_cm=self.machine_drafts[channel])
                        if action != 'MACHINE_APPLY':
                            state = self.t.request('READ')
                            self.limit_drafts.pop(channel, None)
                            self._apply(state)
                    elif action == 'JOG':
                        self.pvs[name].set(0, process=False)
                        if new_value not in (-1, 1):
                            raise DeviceRefused('Jog direction must be -1 or 1')
                        state = self.t.request('READ')
                        self._apply(state)
                        if not state['healthy'] or state['mode'] != 1:
                            raise DeviceRefused('Jog requires healthy EPICS mode')
                        if any(a['busy'] or not a['position_fresh'] or not a['motor_enabled']
                               for a in state['axes'].values()):
                            raise DeviceRefused('Jog requires both axes ready')
                        steps = int(new_value) * self.signs[channel] * self._jog_steps(self.jog_distance_cm, channel)
                        if abs(steps) < 256 // self.resolutions[channel]:
                            raise DeviceRefused('distance below selected resolution')
                        self.t.request('MOVE_REL', axis=axis, value=steps)
                    elif action == 'HOME':
                        self.pvs[name].set(False, process=False)
                        raise DeviceRefused('Switch-based homing is disabled')
                    elif action in ('STOP', 'ZERO'):
                        if action == 'STOP' or new_value: self.t.request(action, axis=axis)
                        self.pvs[name].set(False, process=False)
                    elif action in ('MOVE_REL', 'MOTOR_ENABLE', 'MAX_SPEED', 'ACCEL'):
                        self.t.request(action, axis=axis, value=int(new_value))
                    else: raise DeviceRefused('unknown PV command')
                self.last_error = ''
                self._apply(self.t.request('READ'))
            except (DeviceRefused, ValueError) as exc:
                self._restore_calibration_entry(name)
                self.last_error = str(exc)
                self.pvs['LAST_ERROR'].set(self.last_error[:39])
                # Rejected output settings get corrected on the next poll.
            except Exception as exc:
                self.close()
                self._set_comm(False, str(exc))
                # Only the read loop reconnects; never replay this command.

    async def do_reads(self):
        with self.lock:
            if self.t is None: return self.connect()
            try:
                state = self.t.request('READ')
                self._apply(state)
                return bool(state['healthy'])
            except Exception as exc:
                self.close()
                self._set_comm(False, str(exc))
                return False
