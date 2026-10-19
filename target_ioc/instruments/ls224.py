r"""
devices/instruments/ls224.py - Lake Shore Model 224 temperature monitor.

Two of these run the lab: the fridge thermometry (F1-F11) and the magnet
thermometry (M1-M5).  Both have their own Ethernet port, so the transport is a
plain socket on TCP 7777 - Lake Shore's own port, not SCPI's 5025 - with CR/LF
framing.  That is what devices/serial_tcp_base.py provides, so this driver
reuses it rather than inventing a fourth socket class.

    *IDN?           -> LSCI,MODEL224,LSA19QU/OCD19QU/OCC19QU,1.1
    KRDG? <input>   -> temperature in kelvin, using the curve LOADED IN THE UNIT
    SRDG? <input>   -> the raw sensor reading (ohms for a resistance thermometer)
    RDGST? <input>  -> a bit mask saying whether that reading means anything
    INNAME? <input> -> the label typed into the instrument for that input

Inputs on a 224 are named A, B, C1-C5, D1-D5 (twelve of them).

PVs per channel (channel 'F1' on input 'B' -> UNH:F1_TI, ...):

    _TI      K      temperature
    _RI      ohm    the raw sensor reading
    _Status  text   OK, or what the instrument says is wrong with that reading

and once per IOC: _IDN.

Where the temperature comes from
--------------------------------
Two sources, and the difference matters:

* with no calibration file, _TI is the instrument's own KRDG? - correct only if
  the sensor's curve has been loaded into the 224;
* with `calibration:` pointing at the lab's thermometry CSV, the driver reads
  SRDG? (ohms) and applies the same CCS / CCCS equations the group's
  fridge_temp_map.py uses, which is how these sensors have always been read
  here.  The instrument needs no curves at all in that case.

    CCS   T(K) = 1 / (a + b*ln(R) + c*ln(R)^3)
    CCS2  T(K) = a + b*exp(c * 1000/R)
    CCCS  T(K) = a + b*x + c*x^2 + d*x^3 + e*x^4 + f*x^5 + g*x^6,  x = 1000/R

  The two calibration CSVs spell their headers differently (the fridge file uses
  "Lakeshore Slot" / "A (ZU1)", the magnet file "Lakeshore_Slot" / "a"), so the
  loader accepts both.  A row with no coefficients - the CCX placeholders in the
  magnet file - is skipped, exactly as magnet_temp_reader.py skips them.

A reading the instrument flags as invalid, over- or under-range is published as
NaN with the reason in _Status - never as a number, because on a temperature
screen a wrong number is worse than no number.

settings.yaml
    ls224_fridge:
      module: 'target_ioc.instruments.ls224'
      ip: '192.168.1.19'
      port: 7777
      timeout: 3
      delay: 2
      calibration: 'calibration/fridge_thermometry.csv'   # optional
      channels: [F1, F2, F3]        # PV names
      slots:    [A,  B,  C1]        # the 224 input each one is wired to
"""

import logging
import math
import os

from softioc import builder

from genesis_common.serial_tcp_base import SerialTcpConnection, SerialTcpDevice
from genesis_common.cernox_ccx import load_ccx_bands, ccx_kelvin

log = logging.getLogger(__name__)

INPUTS = ["A", "B"] + [f"C{i}" for i in range(1, 6)] + [f"D{i}" for i in range(1, 6)]

# RDGST? bit weights (Lake Shore 224 manual).  Anything set means the number
# beside it is not a measurement.
STATUS_BITS = [(1, "invalid reading"), (16, "temp underrange"), (32, "temp overrange"),
               (64, "sensor units zero"), (128, "sensor units overrange")]


# The same information, spelled two ways in the two CSVs.
HEADERS = {
    "name": ("Name",),
    "slot": ("Lakeshore Slot", "Lakeshore_Slot", "Slot"),
    "type": ("Thermistor Type", "Thermistor_Type", "Type"),
}
COEFF_COLUMNS = [("A (ZU1)", "a", "A"), ("B (ZL1)", "b", "B"), ("C", "c"),
                 ("D", "d"), ("E", "e"), ("F", "f"), ("G", "g")]
#: how many coefficients each curve form needs
N_COEFFS = {"CCS": 3, "CCS2": 3, "CCCS": 7}


def _pick(row, keys):
    for key in keys:
        if key in row and (row[key] or "").strip():
            return row[key].strip()
    return ""


def load_calibration(path):
    """{slot: (type, coefficients, name)} from a thermometry CSV.

    Accepts either header style, and skips rows whose coefficients are blank -
    the CCX placeholders in magnet_thermometry.csv have a slot but no curve, and
    publishing a temperature for those would be inventing one.
    """
    import csv
    table = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = _pick(row, HEADERS["name"])
            slot = _pick(row, HEADERS["slot"])
            ttype = _pick(row, HEADERS["type"]).upper()
            if not (name and slot and ttype in N_COEFFS):
                continue
            coeffs = []
            for names in COEFF_COLUMNS[:N_COEFFS[ttype]]:
                raw = _pick(row, names)
                try:
                    coeffs.append(float(raw))
                except ValueError:
                    coeffs = []
                    break
            if len(coeffs) != N_COEFFS[ttype] or not all(math.isfinite(v) for v in coeffs):
                log.warning("calibration row %s (slot %s, %s) has no usable "
                            "coefficients - skipped", name, slot, ttype)
                continue
            table[slot] = (ttype, coeffs, name)
    return table


def resistance_to_kelvin(entry, ohms):
    """The two curve forms this lab's sensors use.  NaN on anything unphysical."""
    if entry is None or not math.isfinite(ohms) or ohms <= 0:
        return float("nan")
    ttype, c, _name = entry
    try:
        if ttype == "CCS":
            ln_r = math.log(ohms)
            denom = c[0] + c[1] * ln_r + c[2] * ln_r ** 3
            return 1.0 / denom if denom else float("nan")
        if ttype == "CCS2":
            return c[0] + c[1] * math.exp(c[2] * 1000.0 / ohms)
        if ttype == "CCCS":
            x = 1000.0 / ohms
            return sum(coef * x ** i for i, coef in enumerate(c))
    except (ValueError, OverflowError, ZeroDivisionError):
        return float("nan")
    return float("nan")


def decode_status(code):
    """RDGST? -> human text.  '' means the reading is good."""
    try:
        code = int(float(code))
    except (TypeError, ValueError):
        return "unreadable status"
    return ", ".join(text for bit, text in STATUS_BITS if code & bit)


class Device(SerialTcpDevice):
    """One IOC per 224: a temperature, a raw reading and a status per channel."""

    def _create_pvs(self):
        for ch in self._skip_none_channels():
            self.pvs[ch + "_TI"] = builder.aIn(ch + "_TI", EGU="K", PREC=4, **self.analog)
            self.pvs[ch + "_RI"] = builder.aIn(ch + "_RI", EGU="ohm", PREC=3, **self.analog)
            self.pvs[ch + "_Status"] = builder.stringIn(ch + "_Status")
        first = next(iter(self._skip_none_channels()), "LS")
        self.pvs[first + "_IDN"] = builder.stringIn(first + "_IDN")
        self._info_channel = first

    connection_class = None                     # set below

    # ------------------------------------------------------------- start-up
    def _slot_of(self, channel, index):
        """Which 224 input this PV name is wired to."""
        slots = self.settings.get("slots")
        if slots and index - 1 < len(slots):
            return str(slots[index - 1])
        cal = getattr(self, "_cal", None) or {}
        for slot, (_t, _c, name) in cal.items():          # fall back to the CSV
            if name == channel:
                return slot
        return INPUTS[index - 1] if index - 1 < len(INPUTS) else "A"

    def read_outs(self):
        if self.t is None:
            return
        # calibration is optional: without it the unit's own KRDG? is used
        self._cal = {}
        cal_path = self.settings.get("calibration")
        if cal_path:
            path = cal_path if os.path.isabs(cal_path) else os.path.join(os.getcwd(), cal_path)
            try:
                self._cal = load_calibration(path)
                log.info("%s: calibration for %d slots from %s",
                         self.device_name, len(self._cal), path)
            except OSError as e:
                log.error("calibration file %s could not be read (%s); falling back to "
                          "the instrument's own KRDG?", path, e)
        # Explicit CCX source row selection. A missing/bad calibration must never
        # silently fall back to the 224's unknown KRDG curve for T1/C2 or T3/C4.
        self._ccx = {}
        ccx_file = self.settings.get("ccx_calibration")
        if ccx_file:
            path = ccx_file if os.path.isabs(ccx_file) else os.path.join(os.getcwd(), ccx_file)
            try:
                self._ccx = load_ccx_bands(path)
                log.info("%s: %d CCX source curves loaded", self.device_name, len(self._ccx))
            except (OSError, ValueError) as error:
                log.error("%s: CCX calibration unavailable; required channels invalid: %s",
                          self.device_name, error)
        for name, curve in self.settings.get("ccx_sensors", {}).items():
            if curve not in self._ccx:
                log.error("%s: %s requested missing CCX source %s", self.device_name, name, curve)

        try:
            self.pvs[self._info_channel + "_IDN"].set(self.t.idn()[:39])
            log.info("%s", self.pvs[self._info_channel + "_IDN"].get())
            # the instrument's own labels, so a mis-wired slot shows up at start-up
            for i, ch in enumerate(self._skip_none_channels(), start=1):
                slot = self._slot_of(ch, i)
                log.info("  %-4s -> input %-3s labelled %r", ch, slot, self.t.input_name(slot))
        except OSError as e:
            log.error("%s: start-up read failed: %s", self.device_name, e)

    # ---------------------------------------------------------------- reads
    def read_all(self, channel, index):
        slot = self._slot_of(channel, index)
        ohms = self.t.sensor_reading(slot)
        status = decode_status(self.t.reading_status(slot))
        cal = getattr(self, "_cal", {}).get(slot)
        ccx_map = self.settings.get("ccx_sensors", {})
        required_ccx = set(self.settings.get("ccx_required", []))
        if channel in required_ccx or channel in ccx_map:
            source = ccx_map.get(channel)
            bands = getattr(self, "_ccx", {}).get(source)
            limits = self.settings.get("ccx_kelvin_bounds", {}).get(channel)
            kelvin = ccx_kelvin(bands, ohms, kelvin_bounds=limits)
            if not status:
                if not source:
                    status = "CCX UNASSIGNED (NO TEMP)"
                elif not bands:
                    status = "CCX CALIBRATION MISSING"
                elif not math.isfinite(kelvin):
                    status = "CCX OUT OF CALIBRATION RANGE"
                else:
                    label = self.settings.get("ccx_provisional", {}).get(channel)
                    status = "OK PROVISIONAL " + str(label) if label else "OK"
        else:
            kelvin = resistance_to_kelvin(cal, ohms) if cal else self.t.kelvin(slot)
        if status and status != "OK" and not status.startswith("OK PROVISIONAL"):
            kelvin = float("nan")
        if not math.isfinite(kelvin) and (not status or status == "OK"):
            status = "INVALID TEMPERATURE"
        return {channel + "_TI": kelvin,
                channel + "_RI": ohms,
                channel + "_Status": (status or "OK")[:39]}


class DeviceConnection(SerialTcpConnection):
    """Lake Shore ASCII on TCP 7777 (CR/LF, one question at a time)."""

    eol = b"\r\n"

    def idn(self):
        return self.query_line("*IDN?")

    def _number(self, command):
        raw = self.query_line(command)
        try:
            return float(raw.split(",")[0])
        except ValueError as e:
            raise OSError(f"{self.host}: bad reply to '{command}': {raw!r}") from e

    def kelvin(self, slot):
        return self._number(f"KRDG? {slot}")

    def sensor_reading(self, slot):
        return self._number(f"SRDG? {slot}")

    def reading_status(self, slot):
        return self.query_line(f"RDGST? {slot}")

    def input_name(self, slot):
        return self.query_line(f"INNAME? {slot}").strip('"')


Device.connection_class = DeviceConnection
