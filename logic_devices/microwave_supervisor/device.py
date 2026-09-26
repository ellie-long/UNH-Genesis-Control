"""pySoftIOC adapter for microwave interlocks, conversions and slow sweep."""

from __future__ import annotations

import asyncio
import time

import aioca
from softioc import alarm, builder

from .conversions import FrequencyConversion
from .interlocks import rf_on_permissive
from .magic import configured_magic
from .sweep import sweep_points, validate_dwell


class Device:
    """System-level IOC.  Hardware IOCs remain the only hardware drivers."""

    def __init__(self, device_name, settings):
        self.device_name, self.settings = device_name, settings
        self.pvs = {}
        self.simulation = bool(settings.get("simulation", True))
        self.source = settings["source_pvs"]
        limits = settings["frequency"]
        self.conversion = FrequencyConversion(
            limits["multiplier"], limits["output_min_ghz"],
            limits["output_max_ghz"])
        self.dopants = settings.get("dopants", [])
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
            on_update_name=self._set_frequency)
        p["OutputFrequencyRBV"] = builder.aIn(prefix + "OutputFrequencyRBV", EGU="GHz", PREC=6)
        p["CentralFrequency"] = builder.aOut(prefix + "CentralFrequency", EGU="GHz", PREC=6)
        names = [item["name"] for item in self.dopants] or ["UNCONFIGURED"]
        p["Dopant"] = builder.mbbOut(prefix + "Dopant", *names)
        p["MagicPlus"] = builder.Action(prefix + "MagicPlus", on_update=self._magic_plus)
        p["MagicMinus"] = builder.Action(prefix + "MagicMinus", on_update=self._magic_minus)
        p["RFEnable"] = builder.boolOut(
            prefix + "RFEnable", ZNAM="OFF", ONAM="ON", on_update=self._set_rf)
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
        cfg = self.settings["interlock"]
        return rf_on_permissive(
            self._uca_connected, self._vaunix_connected, self._uca_voltage,
            cfg["required_voltage"], cfg["tolerance"])

    def _publish(self):
        permitted, message = self._permissive()
        self.pvs["SystemConnected"].set(self._uca_connected and self._vaunix_connected)
        self.pvs["UCAConnected"].set(self._uca_connected)
        self.pvs["VaunixConnected"].set(self._vaunix_connected)
        self.pvs["UCAVoltage"].set(self._uca_voltage)
        self.pvs["RFPermissive"].set(permitted)
        self._set_string("InterlockMessage", message)
        self.pvs["RFEnable"].set(self._rf_actual, process=False)
        self.pvs["OutputFrequencyRBV"].set(self._output_frequency)
        self.pvs["SweepState"].set(self._sweep_state)

    async def _put(self, key, value):
        if self.simulation:
            if key == "vaunix_rf": self._rf_actual = bool(value)
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

    async def _set_rf(self, enabled):
        try:
            if not enabled:                 # OFF is never interlocked.
                self._cancel_sweep()
                await self._put("vaunix_rf", 0)
            elif not self._rf_actual:       # Check only the OFF -> ON edge.
                permitted, message = self._permissive()
                if not permitted: raise ValueError(message)
                await self._put("vaunix_rf", 1)
            self._error("")
        except Exception as exc:
            self._error(exc)
        finally:
            self.pvs["RFEnable"].set(self._rf_actual, process=False)

    async def _magic(self, sign):
        try:
            value = configured_magic(self.dopants, self.pvs["Dopant"].get(),
                                     sign, self.pvs["CentralFrequency"].get())
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
        if self.simulation: return
        timeout = float(self.settings.get("ca_timeout", 2))
        keys = ("uca_connected", "uca_voltage", "vaunix_connected",
                "vaunix_rf", "vaunix_frequency_readback")
        values = await asyncio.gather(*(
            aioca.caget(self.source[key], timeout=timeout) for key in keys))
        self._uca_connected, self._uca_voltage, self._vaunix_connected = (
            bool(values[0]), float(values[1]), bool(values[2]))
        self._rf_actual = bool(values[3])
        self._output_frequency = self.conversion.from_vaunix(float(values[4]))

    async def do_reads(self):
        try:
            await self._read_sources()
            await self._advance_sweep()
            self._publish()
            for name in ("SystemConnected", "UCAVoltage", "OutputFrequencyRBV"):
                self.pvs[name].set_alarm(severity=alarm.NO_ALARM, alarm=alarm.NO_ALARM)
            return True
        except Exception as exc:
            self._uca_connected = self._vaunix_connected = False
            if self._sweep_state == 1:
                self._sweep_state = 4
                self._set_string("SweepError", exc)
                self._error(f"Sweep stopped: device read failed: {exc}")
            else:
                self._error(f"Device read failed: {exc}")
            self._publish()
            for name in ("SystemConnected", "UCAVoltage", "OutputFrequencyRBV"):
                self.pvs[name].set_alarm(severity=alarm.MAJOR_ALARM, alarm=alarm.COMM_ALARM)
            return False
