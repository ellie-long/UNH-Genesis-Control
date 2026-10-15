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
    CCX   Lake Shore Chebyshev (calibrated Cernox), piecewise in R:
          Z = log10(R),  k = ((Z - ZL) - (ZU - Z)) / (ZU - ZL),
          T(K) = sum_i A_i cos(i * arccos(k))

  A CCX row holds up to four ranges - "ZU1"/"ZL1" (the A (ZU1) / B (ZL1)
  columns) then the coefficients, likewise from "ZU2", "ZU3", "ZU4" - followed
  by the resistance boundaries between ranges in the "Htemp", "Mtemp" and
  "Ltemp" columns (ohms, despite the names): R above Htemp uses range 1, above
  Mtemp range 2, and so on.  Outside the fitted span the reading is NaN.

  The calibration CSVs spell some headers differently ("Lakeshore Slot" /
  "Lakeshore_Slot", "A (ZU1)" / "a"), so the loader accepts both.  A row with
  no usable coefficients (blank, or an all-zero CCX placeholder) is skipped.

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
#: how many coefficients each curve form needs (CCX is piecewise; see _ccx_curve)
N_COEFFS = {"CCS": 3, "CCS2": 3, "CCCS": 7}
CCX = "CCX"
#: header labels that start each CCX range, and the range boundaries (ohms)
CCX_RANGE_HEADERS = ("ZU1", "ZU2", "ZU3", "ZU4")
CCX_BOUND_HEADERS = ("Htemp", "Mtemp", "Ltemp")


def _pick(row, keys):
    for key in keys:
        if key in row and (row[key] or "").strip():
            return row[key].strip()
    return ""


def _float(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _ccx_curve(header, cells):
    """{"ranges": [(ZU, ZL, [A0..An]), ...], "bounds": [ohms, ...]} or None.

    Positional within the row: each range starts at the column whose header
    contains "ZU1".."ZU4" and runs to the next range (or the boundary columns).
    """
    def col(label):
        return next((i for i, h in enumerate(header) if label in h), None)

    starts = [col(label) for label in CCX_RANGE_HEADERS]
    bounds_at = [col(label) for label in CCX_BOUND_HEADERS]
    if starts[0] is None or any(b is None for b in bounds_at):
        return None
    ends = [s for s in starts[1:] if s is not None] + [min(bounds_at)]
    ranges = []
    for start, end in zip([s for s in starts if s is not None], ends):
        values = [_float(c) for c in cells[start:end]]
        zu, zl = (values + [None, None])[:2]
        coeffs = [v for v in values[2:] if v is not None]
        if zu is None or zl is None or zu == zl or not any(coeffs):
            break
        if zu < zl:                       # Lake Shore always has ZU > ZL: a transposed pair
            log.warning("CCX range %d has ZU < ZL (%g, %g) - treating them as swapped",
                        len(ranges) + 1, zu, zl)
            zu, zl = zl, zu
        ranges.append((zu, zl, coeffs))
    bounds = [_float(cells[i]) if i < len(cells) else None for i in bounds_at]
    bounds = [b for b in bounds if b][:max(len(ranges) - 1, 0)]
    if not ranges or len(bounds) != len(ranges) - 1:
        return None
    return {"ranges": ranges, "bounds": bounds}


def load_calibration(path):
    """{slot: (type, coefficients, name)} from a thermometry CSV.

    Accepts either header style, and skips rows whose coefficients are blank -
    an uncalibrated placeholder has a slot but no curve, and publishing a
    temperature for it would be inventing one.  For CCX the "coefficients" are
    the piecewise Chebyshev curve from _ccx_curve.
    """
    import csv
    table = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader, [])]
        for cells in reader:
            row = {}
            for h, c in zip(header, cells):
                if h and h not in row:
                    row[h] = c
            name = _pick(row, HEADERS["name"])
            slot = _pick(row, HEADERS["slot"])
            ttype = _pick(row, HEADERS["type"]).upper()
            if ttype == CCX and name and slot:
                curve = _ccx_curve(header, cells)
                if curve is None:
                    log.warning("calibration row %s (slot %s, CCX) has no usable "
                                "Chebyshev ranges - skipped", name, slot)
                    continue
                table[slot] = (ttype, curve, name)
                continue
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


def _ccx_kelvin(curve, ohms):
    """Lake Shore Chebyshev, range chosen by the resistance boundaries."""
    index = sum(1 for b in curve["bounds"] if ohms <= b)
    zu, zl, coeffs = curve["ranges"][index]
    z = math.log10(ohms)
    k = ((z - zl) - (zu - z)) / (zu - zl)
    if not -1.0 <= k <= 1.0:              # outside the fitted span: no number
        return float("nan")
    theta = math.acos(k)
    return sum(a * math.cos(i * theta) for i, a in enumerate(coeffs))


def resistance_to_kelvin(entry, ohms):
    """The curve forms this lab's sensors use.  NaN on anything unphysical."""
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
        if ttype == CCX:
            return _ccx_kelvin(c, ohms)
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
        kelvin = resistance_to_kelvin(cal, ohms) if cal else self.t.kelvin(slot)
        if status:                                        # flagged = not a measurement
            kelvin = float("nan")
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
