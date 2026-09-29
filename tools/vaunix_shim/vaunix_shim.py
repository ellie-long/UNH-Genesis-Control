#!/usr/bin/env python3
"""Expose one Vaunix LMS/BLX device as a line-oriented TCP service.

This runs on the Raspberry Pi. Hardware mode and test mode both use Vaunix's
real ARM Linux library. Test mode calls ``fnLMS_SetTestMode(True)``; there is
deliberately no Python device simulator.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
from pathlib import Path

LOG = logging.getLogger("vaunix-shim")
ERROR_BIT = 0x80000000
SWEEP_ACTIVE_BIT = 0x00000004
SWEEP_UP_BIT = 0x00000008
FREQUENCY_UNIT_HZ = 10
POWER_UNIT_DBM = 0.25
MAX_DEVICES = 64
MAX_MODEL_NAME = 32


class VaunixError(RuntimeError):
    """A library, device, or request error safe to show to a client."""


class VaunixLibrary:
    """Typed wrapper around the Linux ``liblmshid.so`` API."""

    def __init__(self, path):
        library = Path(path).expanduser()
        if not library.is_file():
            raise VaunixError(f"Vaunix library does not exist: {library}")
        try:
            self.lib = ctypes.CDLL(str(library))
        except OSError as exc:
            raise VaunixError(f"cannot load {library}: {exc}") from exc
        self.path = str(library)
        self._bind()

    def _function(self, name, *, argtypes=(), restype=ctypes.c_int):
        try:
            fn = getattr(self.lib, name)
        except AttributeError:
            raise VaunixError(f"{self.path} does not export {name}") from None
        fn.argtypes = list(argtypes)
        fn.restype = restype
        return fn

    def _bind(self):
        uint, integer, boolean = ctypes.c_uint, ctypes.c_int, ctypes.c_bool
        char_p = ctypes.POINTER(ctypes.c_char)
        self.initialize = self._function("fnLMS_Init", restype=None)
        self.set_test_mode = self._function(
            "fnLMS_SetTestMode", argtypes=(boolean,), restype=None)
        self.get_version = self._function("fnLMS_GetLibVersion")
        self.get_num_devices = self._function("fnLMS_GetNumDevices")
        self.get_dev_info = self._function(
            "fnLMS_GetDevInfo", argtypes=(ctypes.POINTER(uint),))
        self.get_model_name = self._function(
            "fnLMS_GetModelName", argtypes=(uint, char_p))
        self.get_serial = self._function("fnLMS_GetSerialNumber", argtypes=(uint,))
        self.open_device = self._function("fnLMS_InitDevice", argtypes=(uint,))
        self.close_device = self._function("fnLMS_CloseDevice", argtypes=(uint,))
        self.get_frequency = self._function(
            "fnLMS_GetFrequencyEx", argtypes=(uint,), restype=uint)
        self.get_min_frequency = self._function(
            "fnLMS_GetMinFreqEx", argtypes=(uint,), restype=uint)
        self.get_max_frequency = self._function(
            "fnLMS_GetMaxFreqEx", argtypes=(uint,), restype=uint)
        self.set_frequency = self._function(
            "fnLMS_SetFrequencyEx", argtypes=(uint, uint))
        self.get_sweep_start = self._function("fnLMS_GetStartFrequencyEx", argtypes=(uint,), restype=uint)
        self.set_sweep_start = self._function("fnLMS_SetStartFrequencyEx", argtypes=(uint, uint))
        self.get_sweep_end = self._function("fnLMS_GetEndFrequencyEx", argtypes=(uint,), restype=uint)
        self.set_sweep_end = self._function("fnLMS_SetEndFrequencyEx", argtypes=(uint, uint))
        self.get_sweep_step = self._function("fnLMS_GetFrequencyStepEx", argtypes=(uint,), restype=uint)
        self.set_sweep_step = self._function("fnLMS_SetFrequencyStepEx", argtypes=(uint, uint))
        self.get_sweep_time = self._function("fnLMS_GetSweepTime", argtypes=(uint,))
        self.set_sweep_time = self._function("fnLMS_SetSweepTime", argtypes=(uint, integer))
        self.get_sweep_repeat = self._function("fnLMS_GetSweepmode", argtypes=(uint,))
        self.set_sweep_repeat = self._function("fnLMS_SetSweepMode", argtypes=(uint, boolean))
        self.get_sweep_bidirectional = self._function("fnLMS_GetSweepbidirectionalmode", argtypes=(uint,))
        self.set_sweep_bidirectional = self._function("fnLMS_SetSweepType", argtypes=(uint, boolean))
        self.get_external_trigger = self._function("fnLMS_GetUseExtSweepTrigger", argtypes=(uint,))
        self.set_external_trigger = self._function("fnLMS_SetUseExternalSweepTrigger", argtypes=(uint, boolean))
        self.set_sweep_direction = self._function("fnLMS_SetSweepDirection", argtypes=(uint, boolean))
        self.start_sweep = self._function("fnLMS_StartSweep", argtypes=(uint, boolean))
        self.get_status = self._function("fnLMS_GetDeviceStatus", argtypes=(uint,))
        # Linux uses GetPowerLevel for absolute quarter-dBm. GetAbsPowerLevel
        # is a Windows API symbol and is absent from the supplied ARM object.
        self.get_power = self._function("fnLMS_GetPowerLevel", argtypes=(uint,))
        self.get_min_power = self._function("fnLMS_GetMinPwr", argtypes=(uint,))
        self.get_max_power = self._function("fnLMS_GetMaxPwr", argtypes=(uint,))
        self.set_power = self._function(
            "fnLMS_SetPowerLevel", argtypes=(uint, integer))
        self.get_rf = self._function("fnLMS_GetRF_On", argtypes=(uint,))
        self.set_rf = self._function("fnLMS_SetRFOn", argtypes=(uint, boolean))
        self.get_reference = self._function(
            "fnLMS_GetUseInternalRef", argtypes=(uint,))
        self.set_reference = self._function(
            "fnLMS_SetUseInternalRef", argtypes=(uint, boolean))
        self.get_pll_lock = self._function("fnLMS_GetPLLLock", argtypes=(uint,))
        self.save_settings = self._function("fnLMS_SaveSettings", argtypes=(uint,))


class LabBrick:
    """Own one library device and convert API units to Hz and dBm."""

    def __init__(self, library, *, test_mode=False, serial=None, test_serial=None,
                 minimum_hz=None, maximum_hz=None):
        self.api = library
        self.test_mode = bool(test_mode)
        self.hardware_serial = serial
        self.test_serial = test_serial
        self.device_id = self.serial = None
        self.model = "unknown"
        self.library_version = 0
        self.minimum_frequency_hz = self.maximum_frequency_hz = None
        self.minimum_power_dbm = self.maximum_power_dbm = None
        self.configured_minimum_hz = minimum_hz
        self.configured_maximum_hz = maximum_hz

    def open(self):
        # Required order shown by the vendor example: initialize, mode, scan.
        self.api.initialize()
        self.api.set_test_mode(self.test_mode)
        self.library_version = int(self.api.get_version()) & 0xFFFF
        count = int(self.api.get_num_devices())
        if not 1 <= count <= MAX_DEVICES:
            mode = "test" if self.test_mode else "hardware"
            raise VaunixError(f"library reported {count} devices in {mode} mode")
        ids = (ctypes.c_uint * MAX_DEVICES)()
        found = int(self.api.get_dev_info(ids))
        if not 1 <= found <= MAX_DEVICES:
            raise VaunixError(f"fnLMS_GetDevInfo returned {found}")
        devices = [(int(ids[i]), int(self.api.get_serial(ids[i]))) for i in range(found)]
        requested_serial = self.test_serial if self.test_mode else self.hardware_serial
        if requested_serial is None:
            if len(devices) != 1:
                serials = ", ".join(str(serial) for _, serial in devices)
                raise VaunixError(f"multiple devices found ({serials}); configure a serial")
            self.device_id, self.serial = devices[0]
        else:
            matches = [item for item in devices if item[1] == requested_serial]
            if not matches:
                serials = ", ".join(str(serial) for _, serial in devices)
                raise VaunixError(f"serial {requested_serial} not found (found {serials})")
            self.device_id, self.serial = matches[0]
        self._check(self.api.open_device(self.device_id), "open device")
        try:
            name = ctypes.create_string_buffer(MAX_MODEL_NAME)
            result = int(self.api.get_model_name(self.device_id, name))
            if result <= 0:
                raise VaunixError(f"read model name failed ({result})")
            self.model = name.value.decode("ascii", "replace")
            self._read_limits()
            self._configure_frequency_limits()
            # The vendor's simulated devices initially report raw frequency 0,
            # even though it is outside their advertised range. Seed only test
            # mode to the range midpoint so the IOC can perform its first read.
            # Never change a physical device automatically at startup.
            if self.test_mode:
                raw = int(self.api.get_frequency(self.device_id))
                hz = raw * FREQUENCY_UNIT_HZ
                limits = self.allowed_frequency_limits()
                if not self._within(hz, limits):
                    midpoint = (limits[0] + limits[1]) / 2
                    self._check(self.api.set_frequency(
                        self.device_id, int(round(midpoint / FREQUENCY_UNIT_HZ))),
                        "initialize test-mode frequency")
                # Test devices also start with unset sweep endpoints/time.
                # Seed only invalid fields through the real vendor API.
                start = int(self.api.get_sweep_start(self.device_id)) * FREQUENCY_UNIT_HZ
                end = int(self.api.get_sweep_end(self.device_id)) * FREQUENCY_UNIT_HZ
                duration = int(self.api.get_sweep_time(self.device_id))
                if not self._within(start, limits):
                    self._check(self.api.set_sweep_start(
                        self.device_id, int(round(limits[0] / FREQUENCY_UNIT_HZ))),
                        "initialize test-mode sweep start")
                if not self._within(end, limits):
                    self._check(self.api.set_sweep_end(
                        self.device_id, int(round(limits[1] / FREQUENCY_UNIT_HZ))),
                        "initialize test-mode sweep end")
                if duration <= 0 or duration & ERROR_BIT:
                    self._check(self.api.set_sweep_time(
                        self.device_id, 1000), "initialize test-mode sweep time")
            self._enforce_safe_state()
        except Exception:
            self.close()
            raise
        LOG.info("opened %s serial %s in %s mode; %.9g-%.9g GHz, %+.2f-%+.2f dBm",
                 self.model, self.serial, "test" if self.test_mode else "hardware",
                 self.minimum_frequency_hz / 1e9, self.maximum_frequency_hz / 1e9,
                 self.minimum_power_dbm, self.maximum_power_dbm)

    def close(self):
        if self.device_id is not None:
            try:
                self.api.close_device(self.device_id)
            finally:
                self.device_id = None

    @staticmethod
    def _check(status, operation):
        status = int(status) & 0xFFFFFFFF
        if status & ERROR_BIT:
            raise VaunixError(f"{operation} failed (0x{status:08X})")

    def _read_limits(self):
        device = self.device_id
        self.minimum_frequency_hz = int(self.api.get_min_frequency(device)) * FREQUENCY_UNIT_HZ
        self.maximum_frequency_hz = int(self.api.get_max_frequency(device)) * FREQUENCY_UNIT_HZ
        self.minimum_power_dbm = ctypes.c_int(self.api.get_min_power(device)).value * POWER_UNIT_DBM
        self.maximum_power_dbm = ctypes.c_int(self.api.get_max_power(device)).value * POWER_UNIT_DBM
        if not 0 < self.minimum_frequency_hz < self.maximum_frequency_hz:
            raise VaunixError("invalid frequency limits returned by the library")
        if not self.minimum_power_dbm < self.maximum_power_dbm:
            raise VaunixError("invalid power limits returned by the library")

    def _configure_frequency_limits(self):
        self.allowed_min_hz = max(
            self.minimum_frequency_hz,
            self.configured_minimum_hz or self.minimum_frequency_hz)
        self.allowed_max_hz = min(
            self.maximum_frequency_hz,
            self.configured_maximum_hz or self.maximum_frequency_hz)
        if self.allowed_min_hz > self.allowed_max_hz:
            raise VaunixError("configured frequency limits do not overlap device limits")

    def allowed_frequency_limits(self):
        return self.allowed_min_hz, self.allowed_max_hz

    @staticmethod
    def _within(value, limits):
        return limits[0] <= value <= limits[1]

    def _stop_and_mute(self):
        self._check(self.api.start_sweep(self.device_id, False), "stop sweep")
        self._check(self.api.set_rf(self.device_id, False), "disable RF")

    def _enforce_safe_state(self):
        """Always stop sweeps and disable RF whenever a device is opened."""
        self._stop_and_mute()

    def frequency(self):
        raw = int(self.api.get_frequency(self.device_id))
        value = raw * FREQUENCY_UNIT_HZ
        if not self.minimum_frequency_hz <= value <= self.maximum_frequency_hz:
            raise VaunixError(f"frequency read failed or is out of range (0x{raw:08X})")
        return value

    def set_frequency(self, hz):
        limits = self.allowed_frequency_limits()
        if not self._within(hz, limits):
            raise VaunixError(
                f"frequency {hz / 1e9:.9g} GHz is outside currently allowed "
                f"{limits[0] / 1e9:.9g}-{limits[1] / 1e9:.9g} GHz")
        self._check(self.api.set_frequency(
            self.device_id, int(round(hz / FREQUENCY_UNIT_HZ))), "set frequency")
        return self.frequency()

    def power(self):
        raw = ctypes.c_int(self.api.get_power(self.device_id)).value
        value = raw * POWER_UNIT_DBM
        if not self.minimum_power_dbm <= value <= self.maximum_power_dbm:
            raise VaunixError(f"power read failed or is out of range (0x{raw & 0xFFFFFFFF:08X})")
        return value

    def set_power(self, dbm):
        if not self.minimum_power_dbm <= dbm <= self.maximum_power_dbm:
            raise VaunixError(f"power {dbm:g} dBm is outside device limits")
        self._check(self.api.set_power(
            self.device_id, int(round(dbm / POWER_UNIT_DBM))), "set power")
        return self.power()

    def _flag(self, getter, label):
        value = int(getter(self.device_id))
        if value not in (0, 1):
            raise VaunixError(f"{label} read failed (0x{value & 0xFFFFFFFF:08X})")
        return value

    def rf(self): return self._flag(self.api.get_rf, "RF")
    def reference(self): return self._flag(self.api.get_reference, "reference")
    def pll_locked(self): return self._flag(self.api.get_pll_lock, "PLL lock")

    def set_rf(self, enabled):
        if enabled:
            limits = self.allowed_frequency_limits()
            if not self._within(self.frequency(), limits):
                raise VaunixError("cannot enable RF: carrier is outside currently allowed limits")
        self._check(self.api.set_rf(self.device_id, bool(enabled)), "set RF")
        return self.rf()

    def set_reference(self, internal):
        self._check(self.api.set_reference(self.device_id, bool(internal)), "set reference")
        return self.reference()

    def save(self):
        self._check(self.api.save_settings(self.device_id), "save settings")

    def set_test_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self.test_mode: return int(self.test_mode)
        previous = self.test_mode
        self.close()
        self.test_mode = enabled
        try:
            self.open()
        except Exception as error:
            self.test_mode = previous
            try: self.open()
            except Exception: LOG.exception("failed to restore previous mode")
            raise VaunixError(f"cannot switch to {'test' if enabled else 'hardware'} mode: {error}")
        return int(self.test_mode)

    def _frequency_value(self, getter, label):
        raw = int(getter(self.device_id))
        value = raw * FREQUENCY_UNIT_HZ
        if not self.minimum_frequency_hz <= value <= self.maximum_frequency_hz:
            raise VaunixError(f"{label} read failed or is out of range (0x{raw:08X})")
        return value

    def sweep_state(self):
        status = int(self.api.get_status(self.device_id)) & 0xFFFFFFFF
        if status & ERROR_BIT: raise VaunixError(f"device status read failed (0x{status:08X})")
        step_raw = int(self.api.get_sweep_step(self.device_id))
        # LMS-123 test mode returns BAD_PARAMETER because that simulated model
        # does not implement stepped sweep. Report 0 (unavailable) without
        # making every other sweep readback fail; writes still return the real
        # vendor error.
        step_hz = 0 if step_raw & ERROR_BIT else step_raw * FREQUENCY_UNIT_HZ
        return (self._frequency_value(self.api.get_sweep_start, "sweep start"),
                self._frequency_value(self.api.get_sweep_end, "sweep end"),
                step_hz, int(self.api.get_sweep_time(self.device_id)),
                1 if status & SWEEP_UP_BIT else 0,
                self._flag(self.api.get_sweep_repeat, "sweep repeat"),
                self._flag(self.api.get_sweep_bidirectional, "sweep bidirectional"),
                self._flag(self.api.get_external_trigger, "external sweep trigger"),
                1 if status & SWEEP_ACTIVE_BIT else 0)

    def _set_sweep_frequency(self, setter, getter, hz, label):
        limits = self.allowed_frequency_limits()
        if not self._within(hz, limits):
            raise VaunixError(
                f"{label} {hz / 1e9:.9g} GHz is outside currently allowed "
                f"{limits[0] / 1e9:.9g}-{limits[1] / 1e9:.9g} GHz")
        self._check(setter(self.device_id, int(round(hz / FREQUENCY_UNIT_HZ))), label)
        return self._frequency_value(getter, label)

    def set_sweep_start(self, hz):
        return self._set_sweep_frequency(self.api.set_sweep_start, self.api.get_sweep_start, hz, "set sweep start")

    def set_sweep_end(self, hz):
        return self._set_sweep_frequency(self.api.set_sweep_end, self.api.get_sweep_end, hz, "set sweep end")

    def set_sweep_step(self, hz):
        if not 0 < hz <= self.maximum_frequency_hz - self.minimum_frequency_hz:
            raise VaunixError("sweep step must be positive and no larger than the device span")
        self._check(self.api.set_sweep_step(self.device_id, int(round(hz / FREQUENCY_UNIT_HZ))), "set sweep step")
        return int(self.api.get_sweep_step(self.device_id)) * FREQUENCY_UNIT_HZ

    def set_sweep_time(self, milliseconds):
        value = int(round(milliseconds))
        if value <= 0: raise VaunixError("sweep time must be positive")
        self._check(self.api.set_sweep_time(self.device_id, value), "set sweep time")
        return int(self.api.get_sweep_time(self.device_id))

    def set_sweep_flag(self, name, enabled):
        setters = {"DIRECTION": self.api.set_sweep_direction,
                   "REPEAT": self.api.set_sweep_repeat,
                   "BIDIRECTIONAL": self.api.set_sweep_bidirectional,
                   "EXTERNAL_TRIGGER": self.api.set_external_trigger,
                   "RUN": self.api.start_sweep}
        if name == "RUN" and enabled:
            limits = self.allowed_frequency_limits()
            state = self.sweep_state()
            if not self._within(state[0], limits) or not self._within(state[1], limits):
                raise VaunixError("cannot start sweep: endpoints are outside currently allowed limits")
        self._check(setters[name](self.device_id, bool(enabled)), f"set sweep {name.lower()}")
        return int(bool(enabled))


class Protocol:
    """Parse one request and return exactly one response line."""

    def __init__(self, brick): self.brick = brick

    def dispatch(self, request):
        try:
            return self._dispatch(request.strip())
        except VaunixError as exc:
            return f"ERR {exc}"
        except Exception:
            LOG.exception("unexpected error handling %r", request)
            return "ERR internal shim error"

    def _dispatch(self, request):
        fields = request.split()
        if not fields: raise VaunixError("empty command")
        command, arguments = fields[0].upper(), fields[1:]
        b = self.brick
        if command == "*IDN?":
            mode = "TEST" if b.test_mode else "HARDWARE"
            return f"VAUNIX,{b.model},{b.serial},{b.library_version},{mode}"
        if command == "READ?":
            return f"{b.frequency():.0f} {b.power():.2f} {b.rf()} {b.reference()} {b.pll_locked()}"
        if command == "LIMITS?":
            allowed_min, allowed_max = b.allowed_frequency_limits()
            return (f"{allowed_min:.0f} {allowed_max:.0f} "
                    f"{b.minimum_power_dbm:.2f} {b.maximum_power_dbm:.2f}")
        if command == "FREQ?": return f"{b.frequency():.0f}"
        if command == "POW?": return f"{b.power():.2f}"
        if command == "RF?": return str(b.rf())
        if command == "REF?": return str(b.reference())
        if command == "TEST_MODE?": return str(int(b.test_mode))
        if command == "SWEEP?":
            start, end, step, duration, direction, repeat, bidirectional, trigger, active = b.sweep_state()
            return (f"{start:.0f} {end:.0f} {step:.0f} {duration} {direction} "
                    f"{repeat} {bidirectional} {trigger} {active}")
        if command == "SAVE":
            self._expect(arguments, 0, command); b.save(); return "OK"
        if command == "FREQ": return f"OK {b.set_frequency(self._number(arguments, command)):.0f}"
        if command == "POW": return f"OK {b.set_power(self._number(arguments, command)):.2f}"
        if command == "RF": return f"OK {b.set_rf(self._boolean(arguments, command))}"
        if command == "REF": return f"OK {b.set_reference(self._boolean(arguments, command))}"
        if command == "TEST_MODE": return f"OK {b.set_test_mode(self._boolean(arguments, command))}"
        if command == "SWEEP_START": return f"OK {b.set_sweep_start(self._number(arguments, command)):.0f}"
        if command == "SWEEP_END": return f"OK {b.set_sweep_end(self._number(arguments, command)):.0f}"
        if command == "SWEEP_STEP": return f"OK {b.set_sweep_step(self._number(arguments, command)):.0f}"
        if command == "SWEEP_TIME": return f"OK {b.set_sweep_time(self._number(arguments, command))}"
        if command.startswith("SWEEP_"):
            name = command.removeprefix("SWEEP_")
            if name in {"DIRECTION", "REPEAT", "BIDIRECTIONAL", "EXTERNAL_TRIGGER", "RUN"}:
                return f"OK {b.set_sweep_flag(name, self._boolean(arguments, command))}"
        raise VaunixError(f"unknown command {command}")

    @staticmethod
    def _expect(arguments, count, command):
        if len(arguments) != count:
            raise VaunixError(f"{command} expects {count} argument(s)")

    @classmethod
    def _number(cls, arguments, command):
        cls._expect(arguments, 1, command)
        try: return float(arguments[0])
        except ValueError: raise VaunixError(f"{command} requires a number") from None

    @classmethod
    def _boolean(cls, arguments, command):
        value = cls._number(arguments, command)
        if value not in (0, 1): raise VaunixError(f"{command} requires 0 or 1")
        return bool(value)


class Server:
    def __init__(self, protocol):
        self.protocol, self.lock = protocol, asyncio.Lock()

    async def client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        LOG.info("client connected: %s", peer)
        try:
            while line := await reader.readline():
                if len(line) > 4096:
                    response = "ERR request is too long"
                else:
                    request = line.decode("ascii", "replace").strip()
                    async with self.lock:
                        response = await asyncio.to_thread(self.protocol.dispatch, request)
                writer.write((response + "\n").encode("ascii"))
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            LOG.info("client disconnected: %s", peer)


async def run(args):
    brick = LabBrick(VaunixLibrary(args.library),
                     test_mode=args.test_mode, serial=args.serial,
                     test_serial=args.test_serial,
                     minimum_hz=args.minimum_hz,
                     maximum_hz=args.maximum_hz)
    brick.open()
    try:
        server = Server(Protocol(brick))
        listener = await asyncio.start_server(server.client, args.bind, args.port)
        LOG.info("listening on %s", ", ".join(str(s.getsockname()) for s in listener.sockets))
        async with listener: await listener.serve_forever()
    finally:
        brick.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, help="path to ARM Linux liblmshid.so")
    parser.add_argument("--test-mode", action="store_true",
                        help="call fnLMS_SetTestMode(True); USB hardware is not accessed")
    parser.add_argument("--serial", type=int)
    parser.add_argument("--test-serial", type=int,
                        help="vendor test device serial; separate from physical --serial")
    parser.add_argument("--minimum-hz", type=float)
    parser.add_argument("--maximum-hz", type=float)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1701)
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try: asyncio.run(run(args))
    except (VaunixError, OSError) as exc:
        LOG.error("startup failed: %s", exc)
        raise SystemExit(2)
