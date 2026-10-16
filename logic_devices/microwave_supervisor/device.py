"""pySoftIOC adapter for microwave interlocks, conversions and slow sweep."""

from __future__ import annotations

import asyncio
import time

import aioca
from softioc import alarm, builder

from microwave_ioc.rf_safety import read_snapshot, validate_on, flag, fresh, finite

from .conversions import FrequencyConversion
from .magic import configured_magic
from .sweep import sweep_points, validate_dwell
from .magic import dopant_index


class Device:
    """System-level IOC.  Hardware IOCs remain the only hardware drivers."""

    def __init__(self, device_name, settings):
        self.device_name, self.settings = device_name, settings
        self.pvs = {}
        self.simulation = bool(settings.get("simulation", True))
        self.source = settings["source_pvs"]
        self.settings["rf_safety"] = dict(settings["rf_safety"], **settings["interlock"])
        limits = settings["frequency"]
        self.conversion = FrequencyConversion(
            limits["multiplier"], limits["output_min_ghz"],
            limits["output_max_ghz"])
        self.dopants = settings.get("dopants", [])
        # the target's dopant is chosen with the material on the Cryo Safety
        # screen (UNH:GEN:CRYO:STATE:dopant); the supervisor adopts each change
        self.follow_dopant_pv = settings.get("follow_dopant_pv")
        # Magic constants depend on the target material too (cryo selection)
        self.material_pv = settings.get("material_pv")
        self._followed_dopant = None
        self._next_dopant_check = 0.0
        self._rf_lock = asyncio.Lock()
        self._rf_generation = 0
        self._rf_request = False
        self._rf_valid = False
        self._snapshot = {}
        self._source_session = None
        self._startup_off = True
        self._rf_actual = False
        self._uca_connected = self._vaunix_connected = self.simulation
        self._uca_voltage = float(settings["interlock"]["required_voltage"])
        self._output_frequency = self.conversion.minimum
        self._points = []
        self._point_index = -1
        self._next_point_at = None
        self._sweep_state = 0
        self._create_pvs()

    def _create_pvs(self):
        prefix = self.settings.get("record_prefix", "MW_SUP:")
        p = self.pvs
        p["SystemConnected"] = builder.boolIn(prefix + "SystemConnected", ZNAM="Disconnected", ONAM="Connected")
        p["UCAConnected"] = builder.boolIn(prefix + "UCAConnected", ZNAM="Disconnected", ONAM="Connected")
        p["VaunixConnected"] = builder.boolIn(prefix + "VaunixConnected", ZNAM="Disconnected", ONAM="Connected")
        p["UCAVoltage"] = builder.aIn(prefix + "UCAVoltage", EGU="V", PREC=3)
        p["RFPermissive"] = builder.boolIn(prefix + "RFPermissive", ZNAM="Blocked", ONAM="Permitted")
        p["InterlockMessage"] = builder.stringIn(prefix + "InterlockMessage")
        p["LastError"] = builder.stringIn(prefix + "LastError")
        low, high = self.conversion.minimum, self.conversion.maximum
        p["OutputFrequency"] = builder.aOut(
            prefix + "OutputFrequency", EGU="GHz", PREC=6, DRVL=low, DRVH=high,
            on_update_name=self._set_frequency, always_update=True)
        p["OutputFrequencyRBV"] = builder.aIn(prefix + "OutputFrequencyRBV", EGU="GHz", PREC=6)
        p["CentralFrequency"] = builder.aOut(prefix + "CentralFrequency", EGU="GHz", PREC=6)
        names = [item["name"] for item in self.dopants] or ["UNCONFIGURED"]
        p["Dopant"] = builder.mbbOut(prefix + "Dopant", *names)
        p["MagicPlus"] = builder.Action(prefix + "MagicPlus", on_update=self._magic_plus)
        p["MagicMinus"] = builder.Action(prefix + "MagicMinus", on_update=self._magic_minus)
        p["RFEnable"] = builder.boolOut(
            prefix + "RFEnable", ZNAM="OFF", ONAM="ON", on_update=self._set_rf, always_update=True)
        p["RFAbort"] = builder.Action(prefix + "RFAbort", on_update=self._abort_rf)
        p["RFActual"] = builder.boolIn(prefix + "RFActual", ZNAM="OFF", ONAM="ON")
        p["RFValid"] = builder.boolIn(prefix + "RFValid")
        p["RFState"] = builder.mbbIn(prefix + "RFState", "UNKNOWN", "OFF", "ON")
        p["ControlOwned"] = builder.boolIn(prefix + "ControlOwned")
        for name, initial in (("SweepStart", low), ("SweepStop", high),
                              ("SweepStep", 0.001), ("SweepDwell", 1.0)):
            kwargs = {"EGU": "s" if name == "SweepDwell" else "GHz", "PREC": 6}
            p[name] = builder.aOut(prefix + name, **kwargs)
            p[name].set(initial, process=False)
        p["SweepStartCommand"] = builder.Action(prefix + "SweepStartCommand", on_update=self._start_sweep)
        p["SweepStopCommand"] = builder.Action(prefix + "SweepStopCommand", on_update=self._stop_sweep)
        p["SweepCancel"] = builder.Action(prefix + "SweepCancel", on_update=self._cancel_sweep)
        p["SweepState"] = builder.mbbIn(prefix + "SweepState", "Idle", "Running", "Complete", "Cancelled", "Error")
        p["SweepPoint"] = builder.longIn(prefix + "SweepPoint")
        p["SweepTotal"] = builder.longIn(prefix + "SweepTotal")
        p["SweepProgress"] = builder.aIn(prefix + "SweepProgress", EGU="%", PREC=1)
        p["SweepCurrentFrequency"] = builder.aIn(prefix + "SweepCurrentFrequency", EGU="GHz", PREC=6)
        p["SweepError"] = builder.stringIn(prefix + "SweepError")
        p["Simulation"] = builder.boolIn(prefix + "Simulation", ZNAM="Hardware", ONAM="Simulation")
        p["Simulation"].set(self.simulation)
        p["OutputFrequency"].set(low, process=False)
        p["CentralFrequency"].set((low + high) / 2, process=False)
        self._publish()

    def connect(self):
        """No direct connection: Channel Access is polled by do_reads."""

    @staticmethod
    def _epics_string(message):
        """Fit text into an EPICS DBF_STRING (40 bytes including its NUL)."""
        raw = str(message).encode("utf-8")[:39]
        return raw.decode("utf-8", errors="ignore")

    def _set_string(self, name, message):
        self.pvs[name].set(self._epics_string(message))

    def _error(self, message):
        self._set_string("LastError", message)

    def _permissive(self):
        try:
            if self._startup_off:
                raise ValueError("startup OFF not confirmed")
            if not self._rf_valid:
                raise ValueError("vendor RF state UNKNOWN")
            validate_on(self._snapshot, self.settings["rf_safety"], self.simulation)
            return True, "RF ON permitted"
        except Exception as exc:
            return False, str(exc)

    def _publish(self):
        permitted, message = self._permissive()
        self.pvs["SystemConnected"].set(self._uca_connected and self._vaunix_connected)
        self.pvs["UCAConnected"].set(self._uca_connected)
        self.pvs["VaunixConnected"].set(self._vaunix_connected)
        self.pvs["UCAVoltage"].set(self._uca_voltage)
        self.pvs["RFPermissive"].set(permitted)
        self._set_string("InterlockMessage", message)
        self.pvs["RFEnable"].set(self._rf_request, process=False)
        self.pvs["RFActual"].set(self._rf_actual)
        self.pvs["RFValid"].set(self._rf_valid)
        self.pvs["RFState"].set((2 if self._rf_actual else 1) if self._rf_valid else 0)
        self.pvs["ControlOwned"].set(bool(self._snapshot.get("control_owned", False)))
        self.pvs["OutputFrequencyRBV"].set(self._output_frequency)
        self.pvs["SweepState"].set(self._sweep_state)

    async def _put(self, key, value):
        if self.simulation:
            if key == "vaunix_rf":
                self._rf_actual = bool(value)
                self._rf_valid = True
            if key == "vaunix_frequency": self._output_frequency = self.conversion.from_vaunix(value)
            return
        pv_key = "vaunix_frequency_command" if key == "vaunix_frequency" else key
        await aioca.caput(self.source[pv_key], value, wait=True,
                          timeout=float(self.settings.get("ca_timeout", 2)))

    async def _command_frequency(self, output_ghz):
        value = self.conversion.validate_output(output_ghz)
        await self._put("vaunix_frequency", self.conversion.to_vaunix(value))
        self.pvs["OutputFrequency"].set(value, process=False)
        if self.simulation: self._output_frequency = value

    async def _set_frequency(self, value, _pv):
        try:
            await self._command_frequency(value)
            self._error("")
        except Exception as exc:
            self._error(exc)
            self.pvs["OutputFrequency"].set(self._output_frequency, process=False)

    def _clear_request(self):
        self._rf_request = False
        self.pvs["RFEnable"].set(False, process=False)

    async def _confirm_rf(self, enabled, after):
        if self.simulation:
            return
        timeout = float(self.settings.get("ca_timeout", 2.0))
        deadline = time.monotonic() + float(self.settings.get("rf_confirmation_timeout", 4.0))
        mapping = self.settings["rf_safety_pvs"]
        while time.monotonic() < deadline:
            values = await read_snapshot({k: mapping[k] for k in ("rf_actual", "rf_valid", "rf_time", "rf_done_time", "rf_error", "rf_ready", "vaunix_connected", "control_owned")}, timeout)
            if float(values["rf_done_time"]) < after:
                await asyncio.sleep(0.05)
                continue
            if (flag(values["rf_valid"], "RF valid") and flag(values["vaunix_connected"], "Vaunix connected")
                    and float(values["rf_time"]) >= after):
                fresh(values["rf_time"], self.settings["rf_safety"].get("maximum_readback_age", 4.0), "RF")
                self._snapshot.update(values)
                self._rf_actual = flag(values["rf_actual"], "RF actual")
                self._rf_valid = True
                if str(values["rf_error"]):
                    raise ValueError(str(values["rf_error"]))
                if self._rf_actual == bool(enabled):
                    return
            if str(values["rf_error"]):
                raise ValueError(str(values["rf_error"]))
            await asyncio.sleep(0.05)
        self._rf_valid = False
        raise ValueError("RF confirmation failed/UNKNOWN")

    async def _off_locked(self):
        self._clear_request()
        self._cancel_sweep()
        self._rf_valid = False
        after = time.time()
        await self._put("vaunix_rf", 0)  # IOC independently stops hardware sweep.
        await self._confirm_rf(False, after)
        self._startup_off = False

    async def _abort_rf(self, _value):
        await self._set_rf(False)

    async def _set_rf(self, enabled):
        # Run before the first await: any OFF invalidates pending ON work.
        if not enabled:
            self._rf_generation += 1
            self._clear_request()
            self._cancel_sweep()
            if self._rf_lock.locked():
                # Deliver cancellation to a pending source callback immediately;
                # the serialized OFF below is still required after any in-flight ON.
                try:
                    await self._put("vaunix_rf", 0)
                except Exception:
                    pass  # Final OFF/confirmation reports transport failure.
        generation = self._rf_generation
        async with self._rf_lock:
            try:
                if not enabled:
                    await self._off_locked()
                elif not self._rf_request:
                    changed = await self._read_sources()
                    if changed:
                        await self._off_locked()
                        raise ValueError("source reconnected; request ON again")
                    permitted, message = self._permissive()
                    if not permitted:
                        raise ValueError(message)
                    if generation != self._rf_generation:
                        raise ValueError("ON cancelled by OFF/ABORT")
                    self._rf_request = True
                    self.pvs["RFEnable"].set(True, process=False)
                    self._rf_valid = False
                    after = time.time()
                    await self._put("vaunix_rf", 1)
                    await self._confirm_rf(True, after)
                    if generation != self._rf_generation:
                        await self._off_locked()
                self._error("")
            except Exception as exc:
                self._clear_request()
                if enabled:
                    try:
                        await self._off_locked()
                    except Exception:
                        self._rf_valid = False
                self._error(exc)
            finally:
                self._publish()

    async def _magic(self, sign):
        try:
            material = None
            if self.material_pv:
                material = await aioca.caget(self.material_pv, datatype=str, timeout=1.0)
            value = configured_magic(self.dopants, self.pvs["Dopant"].get(),
                                     sign, self.pvs["CentralFrequency"].get(), material)
            await self._command_frequency(value)
            self._error("")
        except Exception as exc:
            self._error(exc)

    async def _magic_plus(self, _value): await self._magic("+")
    async def _magic_minus(self, _value): await self._magic("-")

    def _start_sweep(self, _value):
        try:
            dwell = validate_dwell(self.pvs["SweepDwell"].get())
            points = sweep_points(self.pvs["SweepStart"].get(),
                                  self.pvs["SweepStop"].get(),
                                  self.pvs["SweepStep"].get(),
                                  maximum_points=int(self.settings.get("maximum_sweep_points", 100000)))
            for value in points: self.conversion.validate_output(value)
            if not self._vaunix_connected: raise ValueError("Vaunix disconnected")
            self._points, self._point_index = points, -1
            self._sweep_dwell, self._next_point_at = dwell, time.monotonic()
            self._sweep_state = 1
            self.pvs["SweepTotal"].set(len(points))
            self.pvs["SweepPoint"].set(0)
            self.pvs["SweepProgress"].set(0)
            self.pvs["SweepError"].set("")
            self._error("")
        except Exception as exc:
            self._sweep_state = 4
            self._set_string("SweepError", exc)
            self._error(exc)
        self._publish()

    def _cancel_sweep(self, _value=None):
        if self._sweep_state == 1: self._sweep_state = 3
        self._points, self._next_point_at = [], None
        self._publish()

    def _stop_sweep(self, _value):
        """Orderly operator stop: retain frequency and return to Idle."""
        self._points, self._next_point_at, self._sweep_state = [], None, 0
        self._publish()

    async def _advance_sweep(self):
        if self._sweep_state != 1 or time.monotonic() < self._next_point_at:
            return
        if not self._vaunix_connected:
            self._sweep_state = 4
            self.pvs["SweepError"].set("Vaunix disconnected")
            self._error("Sweep stopped: Vaunix disconnected")
            return
        self._point_index += 1
        if self._point_index >= len(self._points):
            self._sweep_state, self._next_point_at = 2, None
            self.pvs["SweepProgress"].set(100)
            return
        value = self._points[self._point_index]
        try:
            await self._command_frequency(value)
        except Exception as exc:
            self._sweep_state = 4
            self._set_string("SweepError", exc)
            self._error(f"Sweep stopped: {exc}")
            return
        point = self._point_index + 1
        self.pvs["SweepPoint"].set(point)
        self.pvs["SweepProgress"].set(100 * point / len(self._points))
        self.pvs["SweepCurrentFrequency"].set(value)
        # Dwell starts after the command completes. This state machine never
        # blocks the IOC loop and Cancel remains responsive during the dwell.
        self._next_point_at = time.monotonic() + self._sweep_dwell

    async def _read_sources(self):
        if self.simulation:
            now = time.time()
            self._rf_valid = True
            self._snapshot = dict(uca_connected=self._uca_connected,
                vaunix_connected=self._vaunix_connected, uca_output=True,
                uca_voltage=self._uca_voltage, uca_time=now, vaunix_time=now,
                rf_time=now, rf_valid=True, rf_ready=True, control_owned=True,
                simulation=True, test_mode=True, pll=True,
                frequency=self._output_frequency / self.conversion.multiplier, power=10.0)
            return False
        values = await read_snapshot(self.settings["rf_safety_pvs"], float(self.settings.get("ca_timeout", 2)))
        age = self.settings["rf_safety"].get("maximum_readback_age", 4.0)
        for key in ("uca_time", "vaunix_time", "rf_time"):
            fresh(values[key], age, key)
        self._snapshot = values
        self._uca_connected = flag(values["uca_connected"], "UCA")
        self._vaunix_connected = flag(values["vaunix_connected"], "Vaunix")
        self._uca_voltage = finite(values["uca_voltage"], "UCA voltage")
        self._rf_actual = flag(values["rf_actual"], "RF actual")
        self._rf_valid = flag(values["rf_valid"], "RF valid") and self._vaunix_connected
        self._output_frequency = self.conversion.from_vaunix(finite(values["frequency"], "carrier"))
        finite(values["power"], "power")
        session = values["session"]
        changed = self._source_session is not None and session != self._source_session
        self._source_session = session
        return changed

    async def _follow_dopant(self):
        """Adopt the cryo target-dopant selection when it changes (every ~2 s)."""
        if not self.follow_dopant_pv or time.monotonic() < self._next_dopant_check:
            return
        self._next_dopant_check = time.monotonic() + 2.0
        try:
            value = await aioca.caget(self.follow_dopant_pv, datatype=str, timeout=1.0)
        except Exception:  # noqa: BLE001 - the cryo IOC may be down; keep the current choice
            return
        if value == self._followed_dopant:
            return
        self._followed_dopant = value
        index = dopant_index([d["name"] for d in self.dopants], value)
        if index is not None and index != self.pvs["Dopant"].get():
            self.pvs["Dopant"].set(index)

    async def do_reads(self):
        await self._follow_dopant()
        if self._rf_lock.locked():
            return True  # RF transition owns confirmation; do not overwrite it.
        rf_observed = False
        try:
            changed = await self._read_sources()
            rf_observed = self._rf_valid and self._vaunix_connected
            communications = self._uca_connected and self._vaunix_connected and self._rf_valid and bool(self._snapshot.get("control_owned"))
            # Output enable is a continuous hard interlock. Voltage remains an
            # ON-edge check so operators can adjust attenuation while RF is ON.
            uca_output_lost = (not self.simulation and rf_observed and self._rf_actual
                               and not flag(self._snapshot["uca_output"], "UCA output"))
            if self._startup_off or changed or (self._rf_actual and not self._rf_request) or (not communications and self._rf_request) or uca_output_lost:
                await self._set_rf(False)
                rf_observed = self._rf_valid
                if not self._rf_valid:
                    raise ValueError("RF OFF unconfirmed")
                if uca_output_lost:
                    self._error("UCA output OFF; RF OFF confirmed")
            if not communications:
                raise ValueError("required communication/control lost")
            if self._rf_request and not self._rf_actual:
                self._clear_request()
            await self._advance_sweep()
            self._publish()
            for name in ("SystemConnected", "UCAVoltage", "OutputFrequencyRBV"):
                self.pvs[name].set_alarm(severity=alarm.NO_ALARM, alarm=alarm.NO_ALARM)
            return True
        except Exception as exc:
            requested = self._rf_request
            self._rf_generation += 1
            self._clear_request()
            self._uca_connected = self._vaunix_connected = False
            self._snapshot = {}
            # Loss of UCA communication must not erase an independently fresh
            # vendor OFF confirmation. Missing source evidence remains UNKNOWN.
            self._rf_valid = rf_observed
            self._cancel_sweep()
            if requested:
                await self._set_rf(False)
            self._error(f"Device read failed: {exc}")
            self._publish()
            for name in ("SystemConnected", "UCAVoltage", "OutputFrequencyRBV"):
                self.pvs[name].set_alarm(severity=alarm.MAJOR_ALARM, alarm=alarm.COMM_ALARM)
            return False
