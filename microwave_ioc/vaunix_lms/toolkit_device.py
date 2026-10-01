"""Laptop-side EPICS driver for the Vaunix TCP shim."""
import logging
import asyncio

from microwave_ioc.rf_safety import read_snapshot, validate_on, fresh, flag, finite
import socket
import subprocess
import threading
import time

from softioc import builder
from devices.base_device import BaseDevice

LOG = logging.getLogger(__name__)


class ShimRejected(RuntimeError):
    """The TCP connection works, but the shim rejected the operation."""


class ShimConnection:
    """One persistent, synchronized request/reply connection to the Pi."""

    def __init__(self, host, port, timeout):
        self.host, self.port = host, int(port)
        self.timeout = float(timeout)
        self.socket = self.reader = None
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        self.close()
        self.socket = socket.create_connection((self.host, self.port), self.timeout)
        self.socket.settimeout(self.timeout)
        self.reader = self.socket.makefile("rb")

    def close(self):
        if self.reader is not None:
            try: self.reader.close()
            except OSError: pass
        if self.socket is not None:
            try: self.socket.close()
            except OSError: pass
        self.reader = self.socket = None

    def command(self, request):
        with self.lock:
            if self.socket is None: raise OSError("shim connection is closed")
            try:
                self.socket.sendall((request + "\n").encode("ascii"))
                raw = self.reader.readline(4097)
            except (OSError, ValueError) as exc:
                self.close()
                raise OSError(f"shim communication failed: {exc}") from exc
            if not raw or len(raw) > 4096 or not raw.endswith(b"\n"):
                self.close()
                raise OSError("shim returned an incomplete or oversized response")
            reply = raw.decode("ascii", "replace").strip()
            if reply.startswith("ERR "): raise ShimRejected(reply[4:])
            return reply

    def identity(self):
        reply = self.command("*IDN?")
        fields = reply.split(",")
        if len(fields) != 5 or fields[0] != "VAUNIX" or fields[4] not in ("TEST", "HARDWARE"):
            raise OSError("invalid *IDN? response")
        return reply, int(fields[4] == "TEST")

    def limits(self):
        values = self.command("LIMITS?").split()
        if len(values) != 4: raise OSError("invalid LIMITS? response")
        fmin, fmax, pmin, pmax = map(float, values)
        return fmin / 1e9, fmax / 1e9, pmin, pmax

    def read(self):
        values = self.command("READ?").split()
        if len(values) != 5: raise OSError("invalid READ? response")
        frequency, power = map(float, values[:2])
        try: flags = tuple(int(value) for value in values[2:])
        except ValueError: raise OSError("invalid flags in READ? response") from None
        if any(value not in (0, 1) for value in flags):
            raise OSError("invalid flags in READ? response")
        return frequency / 1e9, power, *flags

    def read_rf(self):
        value = self.command("RF?")
        if value not in ("0", "1"):
            raise OSError("invalid RF? response")
        return int(value)

    def _set(self, name, value):
        reply = self.command(f"{name} {value}").split()
        if len(reply) != 2 or reply[0] != "OK":
            raise OSError(f"invalid {name} response")
        try: return float(reply[1])
        except ValueError: raise OSError(f"invalid {name} value") from None

    def set_frequency(self, ghz):
        return self._set("FREQ", f"{float(ghz) * 1e9:.0f}") / 1e9

    def set_power(self, dbm): return self._set("POW", f"{float(dbm):.2f}")
    def set_rf(self, enabled): return int(self._set("RF", 1 if enabled else 0))
    def acquire(self): return self._set("CONTROL", "ACQUIRE")
    def heartbeat(self): return self._set("CONTROL", "HEARTBEAT")
    def owned(self): return self.command("CONTROL?") == "1"
    def set_reference(self, internal):
        return int(self._set("REF", 1 if internal else 0))

    def sweep(self):
        values = self.command("SWEEP?").split()
        if len(values) != 9: raise OSError("invalid SWEEP? response")
        start, end, step = map(float, values[:3])
        duration = int(values[3])
        flags = tuple(int(value) for value in values[4:])
        if any(value not in (0, 1) for value in flags):
            raise OSError("invalid flags in SWEEP? response")
        return start / 1e9, end / 1e9, step / 1e9, duration, *flags

    def set_test_mode(self, enabled): return int(self._set("TEST_MODE", 1 if enabled else 0))
    def set_sweep_start(self, ghz): return self._set("SWEEP_START", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_end(self, ghz): return self._set("SWEEP_END", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_step(self, ghz): return self._set("SWEEP_STEP", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_time(self, milliseconds): return int(self._set("SWEEP_TIME", int(round(milliseconds))))
    def set_sweep_flag(self, name, enabled):
        return int(self._set(f"SWEEP_{name}", 1 if enabled else 0))


class LocalSimulationConnection:
    """In-process Vaunix simulator for off-lab Genesis integration tests.

    This object implements the same interface used by Device for the
    Raspberry-Pi TCP shim, but performs no network, SSH, USB, or vendor-SDK
    operations.
    """

    def __init__(self, settings):
        self.settings = settings
        self.closed = False

        self.frequency_min = float(
            settings.get("frequency_min_ghz", 11.5)
        )
        self.frequency_max = float(
            settings.get("frequency_max_ghz", 11.8333333333)
        )
        self.power_min = float(
            settings.get("power_min_dbm", -40.0)
        )
        self.power_max = float(
            settings.get("power_max_dbm", 10.0)
        )

        self.frequency = float(
            settings.get("simulation_frequency_ghz", self.frequency_min)
        )
        self.power = float(
            settings.get("simulation_power_dbm", self.power_min)
        )

        self.rf = 0
        self.control_owned = False
        self.reference = 1
        self.lock = 1

        self.sweep_start = self.frequency_min
        self.sweep_end = self.frequency_max
        self.sweep_step = float(
            settings.get("simulation_sweep_step_ghz", 0.001)
        )
        self.sweep_time = int(
            settings.get("simulation_sweep_time_ms", 1000)
        )

        self.sweep_direction = 1
        self.sweep_repeat = 0
        self.sweep_bidirectional = 0
        self.sweep_external_trigger = 0
        self.sweep_active = 0

        self._validate_frequency(self.frequency)
        self._validate_power(self.power)

    def acquire(self):
        self.set_sweep_flag("RUN", False)
        self.set_rf(False)
        self.control_owned = True
        return 6.0

    def heartbeat(self):
        if not self.control_owned:
            raise ShimRejected("control not owned")
        return 6.0

    def owned(self): return self.control_owned

    def close(self):
        self.control_owned = False
        self.closed = True

    def identity(self):
        return "VAUNIX,GENESIS,LOCAL,SIMULATOR,TEST", 1

    def limits(self):
        return (
            self.frequency_min,
            self.frequency_max,
            self.power_min,
            self.power_max,
        )

    def read_rf(self): return self.rf

    def read(self):
        return (
            self.frequency,
            self.power,
            self.rf,
            self.reference,
            self.lock,
        )

    def sweep(self):
        return (
            self.sweep_start,
            self.sweep_end,
            self.sweep_step,
            self.sweep_time,
            self.sweep_direction,
            self.sweep_repeat,
            self.sweep_bidirectional,
            self.sweep_external_trigger,
            self.sweep_active,
        )

    def _validate_frequency(self, value):
        value = float(value)
        if not self.frequency_min <= value <= self.frequency_max:
            raise ShimRejected(
                f"frequency {value} GHz outside "
                f"{self.frequency_min}..{self.frequency_max} GHz"
            )
        return value

    def _validate_power(self, value):
        value = float(value)
        if not self.power_min <= value <= self.power_max:
            raise ShimRejected(
                f"power {value} dBm outside "
                f"{self.power_min}..{self.power_max} dBm"
            )
        return value

    def set_frequency(self, ghz):
        self.frequency = self._validate_frequency(ghz)
        return self.frequency

    def set_power(self, dbm):
        self.power = self._validate_power(dbm)
        return self.power

    def set_rf(self, enabled):
        self.rf = int(bool(enabled))
        return self.rf

    def set_reference(self, internal):
        self.reference = int(bool(internal))
        return self.reference

    def set_test_mode(self, enabled):
        # Local simulation must never masquerade as hardware mode.
        if not bool(enabled):
            raise ShimRejected(
                "local Genesis simulation cannot enter hardware mode"
            )
        return 1

    def set_sweep_start(self, ghz):
        self.sweep_start = self._validate_frequency(ghz)
        return self.sweep_start

    def set_sweep_end(self, ghz):
        self.sweep_end = self._validate_frequency(ghz)
        return self.sweep_end

    def set_sweep_step(self, ghz):
        value = float(ghz)
        if value <= 0:
            raise ShimRejected("sweep step must be positive")
        self.sweep_step = value
        return self.sweep_step

    def set_sweep_time(self, milliseconds):
        value = int(round(float(milliseconds)))
        if value < 1:
            raise ShimRejected("sweep time must be at least 1 ms")
        self.sweep_time = value
        return self.sweep_time

    def set_sweep_flag(self, name, enabled):
        value = int(bool(enabled))

        mapping = {
            "DIRECTION": "sweep_direction",
            "REPEAT": "sweep_repeat",
            "BIDIRECTIONAL": "sweep_bidirectional",
            "EXTERNAL_TRIGGER": "sweep_external_trigger",
            "RUN": "sweep_active",
        }

        if name not in mapping:
            raise ShimRejected(f"unsupported sweep flag: {name}")

        setattr(self, mapping[name], value)
        return value


class Device(BaseDevice):
    """One Vaunix channel with command and readback EPICS records."""

    def _create_pvs(self):
        channels = self._skip_none_channels()
        if len(channels) != 1:
            raise ValueError("Vaunix LMS requires exactly one channel name")
        self._rf_lock = asyncio.Lock()
        self._rf_generation = 0
        self._session = 0
        self._off_confirmed = False
        self._supervisor_ok = False
        # Configuration may narrow the existing operating envelope, never widen it.
        safety = dict(self.settings["rf_safety"])
        for kind, lower, upper in (("frequency", 11.5, 11.8333333333), ("power", -40.0, 10.0)):
            unit = "ghz" if kind == "frequency" else "dbm"
            lo, hi = f"{kind}_min_{unit}", f"{kind}_max_{unit}"
            safety[lo] = max(lower, finite(self.settings.get(lo, lower), lo), finite(safety[lo], lo))
            safety[hi] = min(upper, finite(self.settings.get(hi, upper), hi), finite(safety[hi], hi))
            if safety[lo] > safety[hi]:
                raise ValueError(f"invalid {kind} limits")
        self.settings["rf_safety"] = safety
        self.channel = channels[0]
        self.multiplier = float(self.settings.get("multiplier", 1))
        self.simulation = bool(self.settings.get("simulation", False))
        channel = self.channel
        self.pvs[channel + "_FI"] = builder.aIn(channel + "_FI", **self.sevr)
        self.pvs[channel + "_FC"] = builder.aOut(
            channel + "_FC", on_update_name=self.do_sets, always_update=True, **self.sevr)
        self.pvs[channel + "_OutFI"] = builder.aIn(channel + "_OutFI", **self.sevr)
        self.pvs[channel + "_PWRI"] = builder.aIn(channel + "_PWRI", **self.sevr)
        self.pvs[channel + "_PWRC"] = builder.aOut(
            channel + "_PWRC", on_update_name=self.do_sets, **self.sevr)
        # Legacy raw RF is OFF-only. Normal ON uses RF_CMD with fresh supervisor
        # authorization and independent IOC safety checks.
        self.pvs[channel + "_RF"] = builder.boolOut(
            channel + "_RF", on_update=self._legacy_rf, always_update=True)
        self.pvs[channel + "_RF_CMD"] = builder.boolOut(
            channel + "_RF_CMD", on_update=self._rf_command, always_update=True)
        self.pvs[channel + "_RF_RBV"] = builder.boolIn(channel + "_RF_RBV")
        self.pvs[channel + "_RF_VALID"] = builder.boolIn(channel + "_RF_VALID")
        self.pvs[channel + "_RF_READY"] = builder.boolIn(channel + "_RF_READY")
        self.pvs[channel + "_RF_TIME"] = builder.aIn(channel + "_RF_TIME")
        self.pvs[channel + "_RF_DONE_TIME"] = builder.aIn(channel + "_RF_DONE_TIME")
        self.pvs[channel + "_RF_DONE_TIME"].set(0.0)
        self.pvs[channel + "_RF_STATE"] = builder.mbbIn(channel + "_RF_STATE", "UNKNOWN", "OFF", "ON")
        self.pvs[channel + "_CONTROL_OWNED"] = builder.boolIn(channel + "_CONTROL_OWNED")
        self.pvs[channel + "_CONTROL_SESSION"] = builder.longIn(channel + "_CONTROL_SESSION")
        self.pvs[channel + "_RF_ERROR"] = builder.stringIn(channel + "_RF_ERROR")
        self.pvs[channel + "_Ref"] = builder.boolOut(
            channel + "_Ref", on_update_name=self.do_sets)
        self.pvs[channel + "_Lock"] = builder.boolIn(channel + "_Lock")
        self.pvs[channel + "_Connected"] = builder.boolIn(
            channel + "_Connected", ZNAM="Disconnected", ONAM="Connected")
        self.pvs[channel + "_Simulation"] = builder.boolIn(
            channel + "_Simulation", ZNAM="Physical", ONAM="Local Simulation")
        self.pvs[channel + "_TestMode"] = builder.boolOut(
            channel + "_TestMode", ZNAM="Hardware", ONAM="Test",
            on_update_name=self.do_sets)
        for suffix in ("SweepStart", "SweepEnd", "SweepStep"):
            self.pvs[channel + f"_{suffix}I"] = builder.aIn(
                channel + f"_{suffix}I", EGU="GHz", PREC=6, **self.sevr)
            self.pvs[channel + f"_{suffix}C"] = builder.aOut(
                channel + f"_{suffix}C", EGU="GHz", PREC=6,
                on_update_name=self.do_sets, **self.sevr)
        self.pvs[channel + "_SweepTimeI"] = builder.longIn(
            channel + "_SweepTimeI", EGU="ms")
        self.pvs[channel + "_SweepTimeC"] = builder.longOut(
            channel + "_SweepTimeC", EGU="ms", DRVL=1,
            on_update_name=self.do_sets)
        sweep_flags = {
            "SweepDirection": ("Down", "Up"),
            "SweepRepeat": ("One Shot", "Repeat"),
            "SweepBidirectional": ("Sawtooth", "Triangle"),
            "SweepExternalTrigger": ("Internal", "External"),
            "SweepRun": ("Stopped", "Running"),
        }
        for suffix, (zero, one) in sweep_flags.items():
            self.pvs[channel + "_" + suffix] = builder.boolOut(
                channel + "_" + suffix, ZNAM=zero, ONAM=one,
                on_update_name=self.do_sets)
        self.pvs[channel + "_SweepActive"] = builder.boolIn(
            channel + "_SweepActive", ZNAM="Idle", ONAM="Sweeping")

    def _create_connection(self):
        if self.simulation:
            return LocalSimulationConnection(self.settings)
        return ShimConnection(self.settings["ip"], self.settings["port"],
                              self.settings["timeout"])

    def _start_remote_service(self):
        """Idempotently start the Pi service before opening its TCP socket."""
        if self.simulation:
            return

        shim = self.settings.get("shim", {})
        if not shim.get("start_with_ioc", True):
            return
        user, host = shim.get("ssh_user"), shim.get("ssh_host")
        if not user or not host:
            raise OSError("shim.ssh_user and shim.ssh_host are required")
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                   f"{user}@{host}",
                   "systemctl --user start vaunix-shim.service"]
        try:
            subprocess.run(command, check=True, timeout=12,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            raise OSError(f"could not start Pi shim service: {detail.strip()}") from exc
        deadline = time.monotonic() + float(shim.get("startup_timeout", 15))
        last_error = None
        while time.monotonic() < deadline:
            try:
                probe = socket.create_connection(
                    (self.settings["ip"], int(self.settings["port"])), timeout=1)
                probe.close()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        raise OSError(f"Pi shim service did not open its TCP port: {last_error}")

    def _unknown(self, message):
        self.pvs[self.channel + "_RF_VALID"].set(False)
        self.pvs[self.channel + "_RF_STATE"].set(0)
        self.pvs[self.channel + "_RF_ERROR"].set(str(message).encode()[:39].decode(errors="ignore"))

    def _clear_commands(self):
        for suffix in ("_RF", "_RF_CMD"):
            self.pvs[self.channel + suffix].set(0, process=False)

    def _off(self):
        errors = []
        self._off_confirmed = False
        self.pvs[self.channel + "_RF_READY"].set(False)
        self._rf_generation += 1
        self._clear_commands()
        self._unknown("OFF requested")
        if self.t is None:
            raise OSError("disconnected: OFF unconfirmed")
        for operation in (lambda: self.t.set_sweep_flag("RUN", False),
                          lambda: self.t.set_rf(False)):
            try:
                operation()
            except Exception as exc:
                errors.append(str(exc))
        try:
            actual = self.t.read_rf()
            if actual != 0:
                raise OSError("vendor RF OFF not confirmed")
            self._apply_rf(actual)
        except Exception as exc:
            self._unknown(exc)
            errors.append(str(exc))
        try:
            if self.t.sweep()[-1] != 0:
                raise OSError("hardware sweep stop not confirmed")
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            self.pvs[self.channel + "_RF_ERROR"].set("OFF failed: " + str(errors[0])[:27])
            raise OSError("; ".join(errors))
        self._off_confirmed = True
        self.pvs[self.channel + "_RF_READY"].set(True)
        self.pvs[self.channel + "_RF_ERROR"].set("")

    def connect(self):
        self._off_confirmed = False
        self.pvs[self.channel + "_RF_READY"].set(False)
        self._clear_commands()
        self._unknown("connecting; RF unknown")
        self.pvs[self.channel + "_Connected"].set(False)
        self.pvs[self.channel + "_CONTROL_OWNED"].set(False)
        try:
            self._start_remote_service()
            self.t = self._create_connection()
            identity, test_mode = self.t.identity()
            self.pvs[self.channel + "_Simulation"].set(self.simulation)
            self.pvs[self.channel + "_TestMode"].set(test_mode, process=False)
            self.t.acquire()  # Remote safe OFF is mandatory before ownership.
            self._off()      # Independently confirm from a new vendor read.
            self.t.limits()
            self.read_outs()
            self._session += 1
            self.pvs[self.channel + "_CONTROL_SESSION"].set(self._session)
            self.pvs[self.channel + "_CONTROL_OWNED"].set(True)
            self.pvs[self.channel + "_Connected"].set(True)
            LOG.info("safe control acquired: %s", identity)
        except (OSError, ShimRejected, ValueError) as exc:
            LOG.error("Vaunix connection failed: %s", exc)
            if self.t is not None:
                self.t.close()
            self.t = None
            self._off_confirmed = False
            self.pvs[self.channel + "_RF_READY"].set(False)
            self._unknown(exc)
            self.pvs[self.channel + "_Connected"].set(False)
            self.pvs[self.channel + "_CONTROL_OWNED"].set(False)

    def reconnect(self):
        self._rf_generation += 1
        if self.t is not None:
            self.t.close()
        self.t = None
        self.connect()

    async def _legacy_rf(self, enabled):
        if enabled:
            self.pvs[self.channel + "_RF"].set(0, process=False)
            self.pvs[self.channel + "_RF_ERROR"].set("Raw ON disabled; use Supervisor")
            return
        await self._rf_command(0)

    async def _rf_command(self, enabled):
        if not enabled:
            self._rf_generation += 1
            self._clear_commands()
        generation = self._rf_generation
        async with self._rf_lock:
            try:
                self.pvs[self.channel + "_RF_ERROR"].set("")
                if not enabled:
                    self._off()
                    return
                if not self._off_confirmed:
                    raise ValueError("safe OFF baseline not confirmed")
                values = await read_snapshot(self.settings["rf_safety_pvs"], self.settings.get("ca_timeout", 2.0))
                validate_on(values, self.settings["rf_safety"], self.simulation)
                if not flag(values["supervisor_request"], "supervisor request"):
                    raise ValueError("Supervisor authorization required")
                fresh(values["supervisor_time"], self.settings["rf_safety"].get("maximum_readback_age", 4.0), "Supervisor")
                if generation != self._rf_generation:
                    raise ValueError("ON cancelled by OFF/reconnect")
                if self.t is None or not self.t.owned():
                    raise ValueError("control not owned")
                actual = self.t.read()
                limits = self.settings["rf_safety"]
                if not limits["frequency_min_ghz"] <= actual[0] <= limits["frequency_max_ghz"]:
                    raise ValueError("carrier outside configured limits")
                if not limits["power_min_dbm"] <= actual[1] <= limits["power_max_dbm"]:
                    raise ValueError("power outside configured limits")
                if actual[4] != 1:
                    raise ValueError("PLL unlocked")
                self._unknown("ON requested")
                self.pvs[self.channel + "_RF_CMD"].set(1, process=False)
                achieved = self.t.set_rf(True)
                values = self.t.read()
                if achieved != 1 or values[2] != 1:
                    raise OSError("vendor RF ON not confirmed")
                self._apply(values)
                self.pvs[self.channel + "_RF_ERROR"].set("")
            except Exception as exc:
                self._clear_commands()
                # Rejected/failed ON also attempts mute; failed OFF stays unknown
                # unless a fresh independent read really confirmed OFF.
                if enabled:
                    try:
                        self._off()
                    except Exception:
                        self._unknown("ON failed; OFF unconfirmed")
                self.pvs[self.channel + "_RF_ERROR"].set(str(exc).encode()[:39].decode(errors="ignore"))
                try:
                    owned = self.t is not None and self.t.owned()
                except (OSError, ShimRejected):
                    owned = False
                self.pvs[self.channel + "_CONTROL_OWNED"].set(owned)
                if not owned:
                    self._off_confirmed = False
                    self.pvs[self.channel + "_RF_READY"].set(False)
            finally:
                # Completion is distinct from periodic read freshness. A poll
                # cannot substitute for execution of a repeated OFF command.
                self.pvs[self.channel + "_RF_DONE_TIME"].set(time.time())

    def _apply(self, values, include_setpoints=False):
        frequency, power, rf, reference, lock = values
        finite(frequency, "vendor frequency")
        finite(power, "vendor power")
        for label, value in (("RF", rf), ("reference", reference), ("PLL", lock)):
            flag(value, label)
        channel = self.channel
        self.pvs[channel + "_FI"].set(frequency)
        self.pvs[channel + "_OutFI"].set(frequency * self.multiplier)
        self.pvs[channel + "_PWRI"].set(power)
        self._apply_rf(rf)
        self.pvs[channel + "_Ref"].set(reference, process=False)
        self.pvs[channel + "_Lock"].set(lock)
        if include_setpoints:
            self.pvs[channel + "_FC"].set(frequency, process=False)
            self.pvs[channel + "_PWRC"].set(power, process=False)

    def _apply_rf(self, rf):
        flag(rf, "vendor RF")
        channel = self.channel
        self.pvs[channel + "_RF_RBV"].set(rf)
        self.pvs[channel + "_RF_VALID"].set(True)
        self.pvs[channel + "_RF_TIME"].set(time.time())
        self.pvs[channel + "_RF_STATE"].set(2 if rf else 1)

    def _apply_sweep(self, values, include_setpoints=False):
        start, end, step, duration, direction, repeat, bidirectional, trigger, active = values
        channel = self.channel
        for suffix, value in (("SweepStartI", start), ("SweepEndI", end),
                              ("SweepStepI", step), ("SweepTimeI", duration),
                              ("SweepActive", active)):
            self.pvs[channel + "_" + suffix].set(value)
        for suffix, value in (("SweepDirection", direction), ("SweepRepeat", repeat),
                              ("SweepBidirectional", bidirectional),
                              ("SweepExternalTrigger", trigger), ("SweepRun", active)):
            self.pvs[channel + "_" + suffix].set(value, process=False)
        if include_setpoints:
            for suffix, value in (("SweepStartC", start), ("SweepEndC", end),
                                  ("SweepStepC", step), ("SweepTimeC", duration)):
                self.pvs[channel + "_" + suffix].set(value, process=False)

    def read_outs(self):
        if self.t is not None:
            try:
                self._apply(self.t.read(), include_setpoints=True)
                self._apply_sweep(self.t.sweep(), include_setpoints=True)
            except (OSError, ShimRejected) as exc:
                raise OSError(f"Vaunix startup read failed: {exc}") from exc

    def do_sets(self, new_value, pv):
        if self.t is None:
            LOG.warning("ignoring %s while the Vaunix shim is disconnected", pv)
            return
        name = pv.rsplit(":", 1)[-1]
        try:
            if name.endswith("_FC"):
                if not self.settings["rf_safety"]["frequency_min_ghz"] <= float(new_value) <= self.settings["rf_safety"]["frequency_max_ghz"]:
                    raise ShimRejected("frequency outside configured limits")
                achieved = self.t.set_frequency(new_value)
                self.pvs[self.channel + "_FI"].set(achieved)
                self.pvs[self.channel + "_OutFI"].set(achieved * self.multiplier)
            elif name.endswith("_PWRC"):
                if not self.settings["rf_safety"]["power_min_dbm"] <= float(new_value) <= self.settings["rf_safety"]["power_max_dbm"]:
                    raise ShimRejected("power outside configured limits")
                self.pvs[self.channel + "_PWRI"].set(self.t.set_power(new_value))
            elif name.endswith("_Ref"):
                self.pvs[self.channel + "_Ref"].set(self.t.set_reference(new_value), process=False)
            elif name.endswith("_TestMode"):
                mode = self.t.set_test_mode(new_value)
                self.pvs[self.channel + "_TestMode"].set(mode, process=False)
                self.reconnect()
            elif name.endswith("_SweepStartC"):
                self.pvs[self.channel + "_SweepStartI"].set(self.t.set_sweep_start(new_value))
            elif name.endswith("_SweepEndC"):
                self.pvs[self.channel + "_SweepEndI"].set(self.t.set_sweep_end(new_value))
            elif name.endswith("_SweepStepC"):
                self.pvs[self.channel + "_SweepStepI"].set(self.t.set_sweep_step(new_value))
            elif name.endswith("_SweepTimeC"):
                self.pvs[self.channel + "_SweepTimeI"].set(self.t.set_sweep_time(new_value))
            elif "_Sweep" in name:
                suffix = name.split("_Sweep", 1)[1]
                if suffix == "Run" and new_value and not getattr(self, "simulation", False):
                    raise ShimRejected("physical hardware sweep start not authorized")
                mapping = {"Direction": "DIRECTION", "Repeat": "REPEAT",
                           "Bidirectional": "BIDIRECTIONAL",
                           "ExternalTrigger": "EXTERNAL_TRIGGER", "Run": "RUN"}
                achieved = self.t.set_sweep_flag(mapping[suffix], new_value)
                self.pvs[self.channel + "_Sweep" + suffix].set(
                    achieved, process=False)
                if suffix == "Run":
                    self.pvs[self.channel + "_SweepActive"].set(
                        achieved)
        except ShimRejected as exc:
            LOG.warning("Vaunix rejected %s=%s: %s", name, new_value, exc)
            self.read_outs()
        except OSError as exc:
            LOG.error("Vaunix write failed: %s", exc)
            self.reconnect()

    async def do_reads(self):
        if self.t is None:
            self.connect()
            if self.t is None:
                self.set_alarm(self.channel)
                return False
        try:
            self.t.heartbeat()
            self.pvs[self.channel + "_CONTROL_OWNED"].set(self.t.owned())
            if not self.t.owned():
                raise ShimRejected("lease lost")
            # Supervisor process loss revokes authorization. Recovery cannot
            # replay ON; a fresh supervisor request is necessary.
            if not self.simulation:
                try:
                    status = await read_snapshot({"time": self.settings["rf_safety_pvs"]["supervisor_time"],
                                                  "request": self.settings["rf_safety_pvs"]["supervisor_request"]},
                                                 self.settings.get("ca_timeout", 2.0))
                    fresh(status["time"], self.settings["rf_safety"].get("maximum_readback_age", 4.0), "Supervisor")
                    if not flag(status["request"], "Supervisor request") and self.pvs[self.channel + "_RF_CMD"].get():
                        self._off()
                    self._supervisor_ok = True
                except Exception:
                    if self._supervisor_ok or self.pvs[self.channel + "_RF_CMD"].get():
                        self._rf_generation += 1
                        self._off()
                    self._supervisor_ok = False
            self._apply(self.t.read())
            self._apply_sweep(self.t.sweep())
            self.pvs[self.channel + "_Connected"].set(True)
            self.remove_alarm(self.channel)
            return True
        except ShimRejected as exc:
            LOG.warning("Vaunix read rejected: %s", exc)
            self.set_alarm(self.channel)
            self.pvs[self.channel + "_Connected"].set(False)
            self.pvs[self.channel + "_CONTROL_OWNED"].set(False)
            self._unknown(exc)
            self._off_confirmed = False
            self.pvs[self.channel + "_RF_READY"].set(False)
            self._clear_commands()
            if self.t is not None:
                self.t.close()
            self.t = None
            return False
        except (OSError, ValueError) as exc:
            LOG.error("Vaunix read failed: %s", exc)
            self.set_alarm(self.channel)
            self.pvs[self.channel + "_Connected"].set(False)
            self._unknown(exc)
            self.reconnect()
            return False

    def set_alarm(self, _channel):
        for suffix in ("_FI", "_OutFI", "_PWRI", "_Lock", "_SweepStartI",
                       "_SweepEndI", "_SweepStepI", "_SweepTimeI", "_SweepActive"):
            super().set_alarm(self.channel + suffix)

    def remove_alarm(self, _channel):
        for suffix in ("_FI", "_OutFI", "_PWRI", "_Lock", "_SweepStartI",
                       "_SweepEndI", "_SweepStepI", "_SweepTimeI", "_SweepActive"):
            super().remove_alarm(self.channel + suffix)
