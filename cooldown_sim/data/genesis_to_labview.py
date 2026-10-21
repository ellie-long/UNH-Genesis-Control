"""Genesis recordings -> LabVIEW-style ``data_record_YYYY-MM-DD.csv`` copies.

Reads the Genesis master archive (``archive/master/<session>/<UTC day>/
genesis_master.csv``) and, optionally, pyNMR eventfiles, and writes NEW day
files in the 2026-06 LabVIEW layout, so code built for the old record (the
sim's reader, the McClellan fits) reads Genesis-era cool-downs unchanged.

It never deletes, edits or overwrites anything: inputs are opened read-only,
every output file is created exclusively (an existing file stops the run
before anything is written), and the output folder may not sit inside an
input folder.

Layout written (one file per lab-local day, like the old DAQ)

* TAB-delimited, CRLF, latin-1; the 93 2026-06 header titles in order.
* One row per Genesis master row (1 s).  The old DAQ wrote one 20 s average;
  reducing the row rate is a later stretch goal (see ``docs/COOLDOWN_SIM_
  PROGRESS.md``).
* ``Timestamp`` ``6/15/2026 1:00:05 PM`` and ``Seconds from Midnight`` in
  lab time, from Genesis's ``timestamp_local``.
* A reading is used when its quality is ``valid`` or ``stale`` (stale only
  means "unchanged for 60 s": a steady value stops sending updates);
  ``strict=True`` takes ``valid`` only.  Anything else -> ``NaN``, as the old
  DAQ wrote for an absent reading.
* Columns Genesis does not record yet hold :data:`NOT_IN_GENESIS`, so what is
  still to be built is obvious in every file.  Readers that parse numbers
  turn it into NaN.
* ``Vapor Pressure Range`` names the manometer the temperature came from:
  ``Lo: <phase>`` (Genesis's own ITS-90 temperature from Manometer-Lo) or
  ``Hi: <phase>`` (the converter applies the same ITS-90 function to
  Manometer-Hi, which Genesis does not convert yet).
* ``Area/Polarization`` follows the old DAQ: before calibration it is the
  bare area sum(V * df) of the processed sweep; once a TE has been taken or a
  calibration constant set it is the vector polarization P, written as a
  percentage with a ``%`` sign (``-0.38%``).  "Calibrated" is the Genesis
  fitter's ``SSRF:FIT:CALIBRATED``; when the fitter was not recorded, a
  finite non-zero pyNMR ``cc`` counts as calibrated.
* NMR: each pyNMR sweep (its raw ``phase`` signal) goes on the first master
  row at or after the sweep's end, within ``max_sweep_lag_s``; ``NMR Number``
  is the sweep length, the frequency window comes from pyNMR's ``freq_list``.
  Rows without a new sweep have ``NMR Number`` 0 and no sweep.

How to run it: ``cooldown_sim/data/GENESIS_TO_LABVIEW.md``.  In the repo,
``python -m cooldown_sim genesis-to-labview``; on its own, this file plus
Genesis's ``genesis_common/its90_helium.py`` (Python 3.9+, no other packages):
``python genesis_to_labview.py ...``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: written where Genesis has no source for an old column yet
NOT_IN_GENESIS = "NOT_IN_GENESIS"
#: written where Genesis has the channel but no usable reading at that time
NO_READING = "NaN"

LAYOUT = "2026-06"
_P = "UNH:GEN:"
_REPO = Path(__file__).resolve().parents[2]

_TORR_PER = {"torr": 1.0, "mbar": 0.750061683, "hpa": 0.750061683,
             "pa": 0.00750061683, "micron": 1.0e-3}
_ON = {"1", "1.0", "on", "true", "enabled", "enable", "yes"}
_OFF = {"0", "0.0", "off", "false", "disabled", "disable", "no"}


def _pv(*names):
    return ("pv",) + tuple(_P + n for n in names)


_NA = ("na",)

#: (old title, source).  Source kinds: timestamp, seconds, pv (first usable of
#: the PVs), scaled (pv x factor), onoff, roots, vp_temp, vp_range, pynmr
#: (a field of the latest sweep), online_p, nmr_number, sweep, na.
COLUMNS: tuple[tuple[str, tuple], ...] = (
    ("Timestamp", ("timestamp",)),
    ("Seconds from Midnight", ("seconds",)),
    ("mmWave Freq Setting 1 (GHz)", _pv("MW:SUP:OutputFrequency")),
    ("mmWaves Span (GHz)", _NA),
    ("Mag Field Span (T)", _NA),
    ("Central Mag Field (T)", _NA),
    ("mmWave Freq Setting 2 (GHz)", _NA),
    ("Sweep/Hop Time (s)", _NA),
    ('Fridge He Level (")', _pv("CRYO:FRIDGE:LHE:Fridge_LI")),
    ('Magnet LHe Level (")', _pv("CRYO:MAG:LHE:Magnet_LI")),
    ("EPR Function", _NA),
    ("EPR Freq (Hz)", _NA),
    ("EPR Amplitude (V)", _NA),
    ("EPR Amp Type", _NA),
    ("EPR Sig Gen", _NA),
    ("EPR Load Z", _NA),
    ("EPR Amp Unit", _NA),
    ("Lifetime Setting", _NA),
    ("Slope Setting", _NA),
    ("Sensitivity (V rms)", _NA),
    ("Input Signal Impedance Setting", _NA),
    ("Wide Reserve Setting", _NA),
    ("Ch 1", _NA),
    ("Ch 1 Display Setting", _NA),
    ("Ch 1 Ratio Setting", _NA),
    ("Ch 1 Expand Setting", _NA),
    ("Ch 1 Offset Setting", _NA),
    ("Ch 1 Output Setting", _NA),
    ("Ch 2", _NA),
    ("Ch 2 Display Setting", _NA),
    ("Ch 2 Expand Setting", _NA),
    ("Ch 2 Offset Setting", _NA),
    ("Ref Freq (Hz)", _NA),
    ("NMR Domain", _NA),
    ("Ref Phase", _NA),
    ("Ref Source Setting", _NA),
    ("Input Ref Z Setting", _NA),
    ("Set Phase", _NA),
    ("Set Freq (kHz)", _NA),
    ("Ionivac (Torr)", _pv("CRYO:ION:Ion_PI")),
    ("ColdCathode.Pres (Torr)", _NA),
    ("M1 (K)", _pv("CRYO:MAG:M1_TI")),
    ("T2 (K)", _NA),
    ("T3 (K)", _pv("CRYO:MAG:T3_TI")),
    ("M2 (K)", _pv("CRYO:MAG:M2_TI")),
    ("M3 (K)", _pv("CRYO:MAG:M3_TI")),
    ("M4 (K)", _pv("CRYO:MAG:M4_TI")),
    ("M5 (K)", _pv("CRYO:MAG:M5_TI")),
    ("PS Current (A)", _NA),
    ("F1 (K)", _pv("CRYO:FRIDGE:F1_TI")),
    ("Magnet Current (A)", _NA),
    *((f"F{i} (K)", _pv(f"CRYO:FRIDGE:F{i}_TI")) for i in range(2, 12)),
    ("UCA (V)", _pv("MW:PS:VMEAS", "MW:SUP:UCAVoltage")),
    ("Holeburning Frequency (MHz)", _pv("SSRF:GEN:FREQ_RBV")),
    ("SIG (V)", _NA),
    ("Holeburning Power (dBm)", _pv("SSRF:GEN:LEVEL_RBV")),
    ("Fringe Field (g)", ("scaled", _P + "CRYO:FIELD:Fringe_FieldT", 1.0e4)),
    ("Manometer-Hi (Torr)", _pv("CRYO:CELL:Cell_PI")),
    ("Manometer-Lo (Torr)", _pv("CRYO:VAC:LOW:Mano_PI")),
    ("Vapor Pressure Range", ("vp_range",)),
    ("Vapor Pressure Temp (K)", ("vp_temp",)),
    ("NMR Central Freq (MHz)", ("pynmr", "centfreq")),
    ("NMR Freq Span (MHz)", ("pynmr", "span")),
    ("MMwaves RF Status", ("onoff", _P + "MW:SRC:MW_RF_RBV", _P + "MW:SUP:RFEnable")),
    ("MMwaves Freq (GHz)", _pv("MW:SRC:MW_OutFI", "MW:SUP:OutputFrequencyRBV")),
    ("MMwaves Power (dB)", _pv("MW:SRC:MW_PWRI")),
    ("Seperator Flow (SLM)", _pv("CRYO:FLOW:SEP:SepFlow_SLM")),
    ("Roots Flow L (SLM)", _pv("CRYO:FLOW:LEFT:LPump_SLM")),
    ("Roots Flow R (SLM)", _pv("CRYO:FLOW:RIGHT:RPump_SLM")),
    ("Roots Pressure (Torr)", ("roots",)),
    ("Pirani (Voltage)", _NA),
    ("Backing Pump L Temp (C)", _NA),
    ("Backing Pump R Temp (C)", _NA),
    ("SepHeater Temp (C)", _NA),
    ("VME Board Temp (C)", ("pynmr", "chassis_temp")),
    ("Diode Tune (V)", ("pynmr", "diode_vout")),
    ("Phase Tune (V)", ("pynmr", "phase_vout")),
    ("IF Attenuation (dB)", _NA),
    ("VME Mode", _NA),
    ("NMR Status", ("pynmr", "label")),
    ("Area/Polarization", ("online_p",)),
    ("T1 (K)", _pv("CRYO:MAG:T1_TI")),
    ("NMR Number", ("nmr_number",)),
    ("NMR", ("sweep",)),
)
HEADER = tuple(t for t, _ in COLUMNS)

_ROOTS_PV = _P + "CRYO:VAC:Pump_PI"
_ROOTS_UNIT = _P + "CRYO:VAC:Pump_Unit"
_LO_P = _P + "CRYO:VAC:LOW:Mano_PI"
_LO_T = _P + "CRYO:VAC:LOW:Mano_VapT"
_LO_PHASE = _P + "CRYO:VAC:LOW:Mano_VapPhase"
_HI_P = _P + "CRYO:CELL:Cell_PI"
_P_AREA = _P + "SSRF:FIT:P_AREA"
_CALIBRATED = _P + "SSRF:FIT:CALIBRATED"


def source_text(src: tuple) -> str:
    """Human description of a column's source, for the report."""
    kind = src[0]

    def pvs(names):
        return " or ".join(f"`{p[len(_P):] if p.startswith(_P) else p}`" for p in names)

    if kind == "pv":
        return pvs(src[1:])
    if kind == "scaled":
        return f"{pvs(src[1:2])} x {src[2]:g}"
    if kind == "onoff":
        return pvs(src[1:]) + " as 1/0"
    if kind == "pynmr":
        return "pyNMR `freq_list`" if src[1] in ("centfreq", "span") else f"pyNMR `{src[1]}`"
    return {
        "timestamp": "`timestamp_local` (lab time)",
        "seconds": "`timestamp_local` (lab time)",
        "roots": "`CRYO:VAC:Pump_PI` in Torr, using `Pump_Unit`",
        "vp_temp": "`Mano_VapT` (Lo), else ITS-90 of `Cell_PI` (Hi, by the converter)",
        "vp_range": "`Lo: <Mano_VapPhase>` or `Hi: <phase>`",
        "online_p": "area sum(V*df) of pyNMR `fitsub` until calibrated (`SSRF:FIT:CALIBRATED`), "
                    "then P as `x.xxxx%` from `SSRF:FIT:P_AREA` (pyNMR `pol` if no fitter)",
        "nmr_number": "pyNMR sweep length (0 = no new sweep on this row)",
        "sweep": "pyNMR `phase` sweep",
        "na": NOT_IN_GENESIS,
    }[kind]


def needed_pvs() -> list[str]:
    pvs: list[str] = []
    for _, src in COLUMNS:
        if src[0] in ("pv", "onoff"):
            pvs += src[1:]
        elif src[0] == "scaled":
            pvs.append(src[1])
    pvs += [_ROOTS_PV, _ROOTS_UNIT, _LO_P, _LO_T, _LO_PHASE, _HI_P, _P_AREA, _CALIBRATED]
    return list(dict.fromkeys(pvs))


# ------------------------------------------------------------------ formatting
def fmt_num(x) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return NO_READING
    return format(float(x), ".12g")


def fmt_text(s) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
    return s if s else NO_READING


def labview_timestamp(t: dt.datetime) -> str:
    h12 = t.hour % 12 or 12
    ampm = "AM" if t.hour < 12 else "PM"
    return f"{t.month}/{t.day}/{t.year} {h12}:{t.minute:02d}:{t.second:02d} {ampm}"


def seconds_from_midnight(t: dt.datetime) -> float:
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def parse_iso(text: str) -> dt.datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _float(text) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


# ------------------------------------------------------------------ pyNMR
@dataclass
class Sweep:
    stop: float                       # epoch seconds (UTC)
    values: list[float]
    centfreq: float
    span: float
    label: str
    chassis_temp: float
    diode_vout: float
    phase_vout: float
    pol: float
    cc: float = math.nan
    area: float = math.nan            # sum(V * df) of pyNMR's processed sweep (fitsub)


_EVENTFILE = re.compile(r"^(?:current_)?(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:__.*)?\.txt$")


def pynmr_eventfiles(dirs) -> list[Path]:
    """pyNMR eventfiles (``<start>__<stop>.txt``, ``current_<start>.txt``) in
    ``dirs``, in start-time order."""
    found = []
    for d in dirs or ():
        for p in Path(d).glob("*.txt"):
            m = _EVENTFILE.match(p.name)
            if m:
                found.append((m.group(1), p.name, p))
    return [p for _, _, p in sorted(found)]


def window_of(freqs) -> tuple[float, float]:
    """(centre, span) such that ``centre + span*(k/n - 0.5)`` is bin k, the
    old reader's frequency axis."""
    n = len(freqs)
    if n < 2:
        return math.nan, math.nan
    f0, f1 = float(freqs[0]), float(freqs[-1])
    span = (f1 - f0) * n / (n - 1)
    return f0 + span / 2.0, span


def sweep_from_event(ev: dict) -> Sweep | None:
    vals = ev.get("phase")
    if not isinstance(vals, list) or len(vals) < 2:
        return None
    try:
        vals = [float(v) for v in vals]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vals):
        return None
    stop = ev.get("stop_stamp")
    if not isinstance(stop, (int, float)) or not math.isfinite(stop):
        t = parse_iso(str(ev.get("stop_time", "")))
        if t is None or t.tzinfo is None:
            return None
        stop = t.timestamp()
    freqs = ev.get("freq_list") or []
    cent, span = window_of(freqs)
    return Sweep(stop=float(stop), values=vals, centfreq=cent, span=span,
                 label=str(ev.get("label", "")), chassis_temp=_float(ev.get("chassis_temp")),
                 diode_vout=_float(ev.get("diode_vout")), phase_vout=_float(ev.get("phase_vout")),
                 pol=_float(ev.get("pol")), cc=_float(ev.get("cc")),
                 area=area_v_df(ev.get("fitsub"), freqs))


def area_v_df(signal, freqs) -> float:
    """sum(V * df), df the frequency step (MHz) of each bin: the area the old
    DAQ wrote before calibration."""
    try:
        v = [float(x) for x in signal or ()]
        f = [float(x) for x in freqs or ()]
    except (TypeError, ValueError):
        return math.nan
    n = len(v)
    if n < 2 or len(f) != n:
        return math.nan
    total = 0.0
    for k in range(n):                  # centred step, one-sided at the ends
        lo, hi = max(k - 1, 0), min(k + 1, n - 1)
        total += v[k] * (f[hi] - f[lo]) / (hi - lo)
    return total if math.isfinite(total) else math.nan


def iter_sweeps(files, counts: dict):
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    counts["pynmr_bad_lines"] = counts.get("pynmr_bad_lines", 0) + 1
                    continue
                sw = sweep_from_event(ev)
                if sw is None:
                    counts["pynmr_unusable"] = counts.get("pynmr_unusable", 0) + 1
                    continue
                counts["pynmr_sweeps_read"] = counts.get("pynmr_sweeps_read", 0) + 1
                yield sw


# ------------------------------------------------------------------ Genesis master
def master_files(inputs) -> list[Path]:
    out = []
    for i in inputs:
        p = Path(i)
        if p.is_file() and p.name == "genesis_master.csv":
            out.append(p)
        elif p.is_dir():
            out += sorted(p.rglob("genesis_master.csv"))
    return list(dict.fromkeys(out))


def _local_index(header) -> int:
    return header.index("timestamp_local")


def first_last_local(path: Path) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Lab-local time of a master file's first and last rows (reads only the
    file's two ends)."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        header = next(csv.reader([fh.readline()]), [])
        if "timestamp_local" not in header:
            return None, None
        li = _local_index(header)
        first = None
        for line in fh:
            row = next(csv.reader([line]), [])
            if len(row) > li and (first := parse_iso(row[li])):
                break
    last = None
    with open(path, "rb") as fb:
        fb.seek(0, 2)
        size = fb.tell()
        fb.seek(max(0, size - 1_000_000))
        tail = fb.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(tail[1:] if len(tail) > 1 else tail):
        row = next(csv.reader([line]), [])
        if len(row) > li and (last := parse_iso(row[li])):
            break
    return first, last


def session_info(master_csv: Path) -> dict:
    for parent in master_csv.parents:
        sj = parent / "session.json"
        if sj.is_file():
            try:
                info = json.loads(sj.read_text(encoding="utf-8"))
            except ValueError:
                info = {}
            info["_dir"] = parent.name
            return info
    return {"_dir": master_csv.parent.parent.name}


class _Row:
    """One master row's readings, by PV."""

    def __init__(self, row, cols, accept):
        self.row, self.cols, self.accept = row, cols, accept

    def text(self, pv):
        c = self.cols.get(pv)
        if c is None:
            return None
        vi, qi = c
        if qi >= len(self.row) or self.row[qi] not in self.accept:
            return None
        v = self.row[vi].strip() if vi < len(self.row) else ""
        return v if v else None

    def num(self, pv):
        return _float(self.text(pv))


# ------------------------------------------------------------------ conversion
@dataclass
class Report:
    started: str = ""
    inputs: list = field(default_factory=list)
    pynmr_dirs: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    days: dict = field(default_factory=dict)          # day -> [rows, sweeps]
    counts: dict = field(default_factory=dict)
    missing_pvs: set = field(default_factory=set)
    options: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def bump(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n


class Refused(RuntimeError):
    """The conversion would overwrite something, or write into its inputs."""


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def plan(inputs, out_dir, pynmr_dirs=()) -> tuple[list[Path], list[str]]:
    """Master files to read and the day files they will make; raises
    :class:`Refused` if any output already exists or the output folder is
    inside an input folder."""
    out_dir = Path(out_dir)
    for src in list(inputs) + list(pynmr_dirs or ()):
        s = Path(src)
        if s.is_dir() and _inside(out_dir, s):
            raise Refused(f"output folder {out_dir} is inside input {s}; choose a separate folder")
    files = master_files(inputs)
    if not files:
        raise Refused("no genesis_master.csv found under the inputs")
    days = set()
    for f in files:
        a, b = first_last_local(f)
        if a is None:
            continue
        b = b or a
        d = a.date()
        while d <= b.date():
            days.add(d.isoformat())
            d += dt.timedelta(days=1)
    names = sorted(f"data_record_{d}.csv" for d in days)
    clash = [n for n in names + [REPORT_NAME] if (out_dir / n).exists()]
    if clash:
        raise Refused(f"{len(clash)} output file(s) already exist in {out_dir} "
                      f"(e.g. {clash[0]}); nothing was written. Use a new, empty folder.")
    return files, names


REPORT_NAME = "genesis_to_labview_report.md"


def convert(inputs, out_dir, pynmr_dirs=(), *, strict=False, max_sweep_lag_s=5.0,
            nmr_hold_s=300.0, log=print) -> Report:
    """Write new LabVIEW-layout day files for the Genesis master recordings
    under ``inputs``.  Returns the :class:`Report` (also written beside them)."""
    its90 = _its90()
    out_dir = Path(out_dir)
    files, _ = plan(inputs, out_dir, pynmr_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)
    accept = {"valid"} if strict else {"valid", "stale"}
    rep = Report(started=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 inputs=[str(i) for i in inputs], pynmr_dirs=[str(d) for d in pynmr_dirs or ()],
                 options={"strict": strict, "max_sweep_lag_s": max_sweep_lag_s,
                          "nmr_hold_s": nmr_hold_s, "layout": LAYOUT})

    # master files in time order (sessions restart; UTC day folders)
    timed = []
    for f in files:
        a, b = first_last_local(f)
        if a is None:
            rep.bump("master_files_without_rows")
            continue
        timed.append((a.timestamp(), (b or a).timestamp(), f))
    timed.sort(key=lambda x: x[0])

    sweeps = iter_sweeps(pynmr_eventfiles(pynmr_dirs), rep.counts)
    pending = next(sweeps, None)
    held: Sweep | None = None              # latest attached sweep, for the settings columns
    handles: dict[str, object] = {}
    last_epoch = -math.inf
    want = needed_pvs()

    try:
        for _, _, path in timed:
            info = session_info(path)
            rep.sessions.append({"file": str(path), "session": info.get("_dir", ""),
                                 "git_sha": str(info.get("git_sha", ""))[:12],
                                 "git_dirty": info.get("git_dirty", "")})
            log(f"reading {path}")
            with open(path, encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, [])
                width = len(header)
                index = {n: i for i, n in enumerate(header)}
                li = index.get("timestamp_local")
                if li is None:
                    rep.bump("master_files_without_rows")
                    continue
                cols = {}
                for pv in want:
                    vi, qi = index.get(pv + "|value"), index.get(pv + "|quality")
                    if vi is None or qi is None:
                        rep.missing_pvs.add(pv)
                    else:
                        cols[pv] = (vi, qi)
                for row in reader:
                    if len(row) != width:
                        rep.bump("rows_skipped_incomplete")
                        continue
                    t = parse_iso(row[li])
                    if t is None or t.tzinfo is None:
                        rep.bump("rows_skipped_bad_time")
                        continue
                    epoch = t.timestamp()
                    if epoch < last_epoch:
                        rep.bump("rows_out_of_order")
                    last_epoch = max(last_epoch, epoch)

                    attach = None
                    while pending is not None and pending.stop <= epoch:
                        if epoch - pending.stop <= max_sweep_lag_s:
                            if attach is not None:
                                rep.bump("sweeps_superseded_same_row")
                            attach = pending
                        else:
                            rep.bump("sweeps_without_genesis_row")
                        pending = next(sweeps, None)
                    if attach is not None:
                        held = attach
                    settings = held if (held is not None and epoch - held.stop <= nmr_hold_s) else None

                    r = _Row(row, cols, accept)
                    fields = _render(r, t, attach, settings, its90, rep)
                    day = t.date().isoformat()
                    fh_out = handles.get(day)
                    if fh_out is None:
                        fh_out = open(out_dir / f"data_record_{day}.csv", "x",
                                      encoding="latin-1", errors="replace", newline="")
                        fh_out.write("\t".join(HEADER) + "\r\n")
                        handles[day] = fh_out
                        rep.days[day] = [0, 0]
                    fh_out.write("\t".join(fields) + "\r\n")
                    rep.days[day][0] += 1
                    if attach is not None:
                        rep.days[day][1] += 1
        while pending is not None:
            rep.bump("sweeps_without_genesis_row")
            pending = next(sweeps, None)
    finally:
        for h in handles.values():
            h.close()

    with open(out_dir / REPORT_NAME, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(report_markdown(rep))
    return rep


def _its90():
    """Genesis's ITS-90 helium module: from the repo, or beside this file."""
    try:
        from genesis_common import its90_helium
        return its90_helium
    except ImportError:
        pass
    beside = Path(__file__).with_name("its90_helium.py")
    if not beside.is_file():
        raise SystemExit("needs Genesis's genesis_common/its90_helium.py: run inside "
                         "UNH-Genesis-Control, or put its90_helium.py beside this file")
    import importlib.util
    spec = importlib.util.spec_from_file_location("its90_helium", beside)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(r: _Row, t, attach, settings, its90, rep) -> list[str]:
    out: list[str] = []
    for title, src in COLUMNS:
        kind = src[0]
        if kind == "timestamp":
            out.append(labview_timestamp(t))
        elif kind == "seconds":
            out.append(f"{seconds_from_midnight(t):.6f}")
        elif kind == "pv":
            v = math.nan
            for pv in src[1:]:
                v = r.num(pv)
                if math.isfinite(v):
                    break
            out.append(fmt_num(v))
        elif kind == "scaled":
            out.append(fmt_num(r.num(src[1]) * src[2]))
        elif kind == "onoff":
            v = math.nan
            for pv in src[1:]:
                s = (r.text(pv) or "").strip().lower()
                v = 1.0 if s in _ON else 0.0 if s in _OFF else _float(s)
                if math.isfinite(v):
                    break
            out.append(fmt_num(v))
        elif kind == "roots":
            p = r.num(_ROOTS_PV)
            unit = (r.text(_ROOTS_UNIT) or "").strip().lower()
            if math.isfinite(p) and unit not in _TORR_PER:
                rep.bump("roots_pressure_unknown_unit")
                p = math.nan
            out.append(fmt_num(p * _TORR_PER.get(unit, math.nan) if math.isfinite(p) else p))
        elif kind == "vp_temp":
            out.append(fmt_num(_vapor(r, its90)[0]))
        elif kind == "vp_range":
            out.append(fmt_text(_vapor(r, its90)[1]))
        elif kind == "pynmr":
            if settings is None:
                out.append(NO_READING)
            elif src[1] == "label":
                out.append(fmt_text(settings.label))
            else:
                out.append(fmt_num(getattr(settings, src[1])))
        elif kind == "online_p":
            out.append(_area_or_p(r, settings, rep))
        elif kind == "nmr_number":
            out.append(str(len(attach.values)) if attach is not None else "0")
        elif kind == "sweep":
            if attach is not None:
                out.extend(format(v, ".9g") for v in attach.values)
            else:
                out.append(NO_READING)
        else:
            out.append(NOT_IN_GENESIS)
    return out


def fmt_pct(p: float) -> str:
    return f"{p:.4f}%" if math.isfinite(p) else NO_READING


def _area_or_p(r: _Row, sweep, rep) -> str:
    """Area before calibration, P (%) after, like the old DAQ's column."""
    cal = (r.text(_CALIBRATED) or "").strip().lower()
    if cal in ("1", "1.0", "calibrated"):
        rep.bump("rows_P_genesis_fitter")
        return fmt_pct(r.num(_P_AREA))
    if not cal and sweep is not None and math.isfinite(sweep.cc) and sweep.cc != 0:
        rep.bump("rows_P_pynmr_cc")              # no fitter recorded: pyNMR's own constant
        return fmt_pct(sweep.pol * 100.0)
    if sweep is not None:
        rep.bump("rows_area")
        return fmt_num(sweep.area)
    return NO_READING


def _vapor(r: _Row, its90) -> tuple[float, str]:
    lo = r.num(_LO_P)
    if math.isfinite(lo):
        phase = r.text(_LO_PHASE) or its90.phase_of(lo)
        return r.num(_LO_T), f"Lo: {phase}"
    hi = r.num(_HI_P)
    if math.isfinite(hi):
        kelvin, _ = its90.torr_to_kelvin(hi)
        return kelvin, f"Hi: {its90.phase_of(hi)}"
    return math.nan, ""


# ------------------------------------------------------------------ report
def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(_REPO), "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def report_markdown(rep: Report) -> str:
    L = [f"# Genesis -> LabVIEW ({LAYOUT} layout) conversion", "",
         f"Written {rep.started} by `python -m cooldown_sim genesis-to-labview` "
         f"(dev-sim {_git_sha() or '?'}). These files are a converted COPY; the Genesis "
         "recordings they came from are unchanged and remain the original data.", "",
         "## Inputs", ""]
    L += [f"- Genesis: `{i}`" for i in rep.inputs]
    L += [f"- pyNMR: `{d}`" for d in rep.pynmr_dirs] or ["- pyNMR: none given (NMR columns empty)"]
    L += ["", f"Options: {rep.options}", "", "## Sessions read", "",
          "| session | git | dirty | file |", "|---|---|---|---|"]
    L += [f"| {s['session']} | {s['git_sha']} | {s['git_dirty']} | `{s['file']}` |" for s in rep.sessions]
    L += ["", "## Day files", "", "| file | rows | rows with an NMR sweep |", "|---|---|---|"]
    L += [f"| data_record_{d}.csv | {n} | {k} |" for d, (n, k) in sorted(rep.days.items())]
    if rep.counts:
        L += ["", "## Counts", ""] + [f"- {k}: {v}" for k, v in sorted(rep.counts.items())]
    if rep.missing_pvs:
        L += ["", "## PVs not in these recordings (their columns are NaN)", ""]
        L += [f"- `{p}`" for p in sorted(rep.missing_pvs)]
    L += ["", "## Columns", "",
          f"`{NOT_IN_GENESIS}` = Genesis has no source for this column yet. "
          f"`{NO_READING}` = the channel exists but had no usable reading "
          "(quality not valid/stale, or not recorded).", "",
          "| # | old column | from |", "|---|---|---|"]
    L += [f"| {i} | {t.replace('|', '/')} | {source_text(s)} |" for i, (t, s) in enumerate(COLUMNS)]
    L += ["", "## Notes", "",
          "- One row per Genesis master row (1 s). The old DAQ wrote 20 s averages; "
          "reducing the row rate is a later stretch goal.",
          "- Stale readings are used (a steady value stops updating and turns stale after 60 s)"
          + (" -- not in this run (strict)." if rep.options.get("strict") else "."),
          "- Start-up placeholder zeros that Genesis records as valid are not filtered yet "
          "(open question 73).",
          "- `Vapor Pressure Range` names the manometer: `Lo:` is Genesis's ITS-90 "
          "temperature from Manometer-Lo; `Hi:` is the same ITS-90 function applied by the "
          "converter to Manometer-Hi.",
          "- `Area/Polarization`: a bare number is the area sum(V*df); a value ending in `%` "
          "is the calibrated vector polarization P.",
          "- NMR sweeps are pyNMR's raw `phase` signal; each sweep appears once, on the first "
          "row at or after it ended."]
    L += [f"- {n}" for n in rep.notes]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ command line
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("inputs", nargs="+",
                   help="Genesis data root, archive/master folder, session folder or genesis_master.csv")
    p.add_argument("--out", required=True, help="NEW folder for the converted copies")
    p.add_argument("--pynmr", action="append", default=[], metavar="DIR",
                   help="pyNMR event_dir to take NMR sweeps from (repeatable)")
    p.add_argument("--strict", action="store_true",
                   help="use only 'valid' readings (default also uses 'stale' = unchanged)")
    p.add_argument("--max-sweep-lag", type=float, default=5.0, metavar="S",
                   help="attach a pyNMR sweep only to a row within S seconds after it ended")


def run(args) -> int:
    """0 written, 2 refused (nothing written)."""
    try:
        rep = convert(args.inputs, args.out, args.pynmr, strict=args.strict,
                      max_sweep_lag_s=args.max_sweep_lag)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    for day, (rows, sweeps) in sorted(rep.days.items()):
        print(f"  data_record_{day}.csv  {rows} rows, {sweeps} with an NMR sweep")
    print(f"wrote {len(rep.days)} day file(s) and {REPORT_NAME} in {args.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="genesis_to_labview.py",
        description="Write LabVIEW-style data_record copies of Genesis recordings "
                    "(never changes or overwrites anything).")
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
