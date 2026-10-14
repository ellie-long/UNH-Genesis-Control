"""Stream the slow-control columns out of LabVIEW ``data_record_*.csv`` files.

Each row of a data_record is one full slow-control snapshot plus one raw NMR
sweep (hundreds of trailing columns).  The simulator only needs the named
columns here, so rows are split just far enough to reach the last wanted
column -- reading a 145 MB day takes a couple of seconds, not tens.

Timestamps are lab-local wall time (America/New_York); they are converted to
epoch seconds so the sim clock and the data share one axis.  Rows whose
timestamp cannot be parsed are skipped and counted.

The NMR sweeps themselves are read with the vendored McClellan
``cooldown_io`` (see ``online_fitter/replay_source.py``), not here.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import aliases

LAB_TZ_NAME = "America/New_York"
_TS_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def lab_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(LAB_TZ_NAME)
    except Exception:          # noqa: BLE001 - no tz database: fall back to UTC-5
        return _dt.timezone(_dt.timedelta(hours=-5), "EST")


def parse_timestamp(text: str, tz=None) -> float:
    """Lab-local timestamp string -> epoch seconds (NaN if unparseable)."""
    s = str(text).strip()
    tz = tz or lab_tz()
    for fmt in _TS_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).replace(tzinfo=tz).timestamp()
        except ValueError:
            continue
    return float("nan")


def format_epoch(t: float, tz=None) -> str:
    """Epoch seconds -> 'YYYY-MM-DD HH:MM:SS' lab-local (for displays/reports)."""
    if not np.isfinite(t):
        return "--"
    return _dt.datetime.fromtimestamp(t, tz or lab_tz()).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ files
def sniff_delimiter(first_line: str) -> str:
    return "\t" if first_line.count("\t") >= first_line.count(",") else ","


def read_header(path: Path) -> tuple[list[str], str]:
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        first = fh.readline().rstrip("\r\n")
    delim = sniff_delimiter(first)
    return [h.strip() for h in first.split(delim)], delim


_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})-(\d{1,2})-(\d{4})")


def day_files(cooldown_dir: Path) -> list[Path]:
    """The data_record files of one cool-down, chronological.

    ``-fixed.csv`` repairs supersede their originals; ``.corrupt`` files are
    ignored.  Ordering is by the first data row's timestamp, because file
    names use several date spellings (9-10-2020, 2025-12-12, 2025-12-15-16).
    """
    cooldown_dir = Path(cooldown_dir)
    files = sorted(p for p in cooldown_dir.glob("data_record_*.csv"))
    fixed = {p.name.replace("-fixed.csv", ".csv") for p in files if p.name.endswith("-fixed.csv")}
    chosen = [p for p in files if p.name not in fixed]

    def first_time(p: Path) -> float:
        hdr, delim = read_header(p)
        idx = aliases.resolve(hdr, ["timestamp"]).get("timestamp", 0)
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            fh.readline()
            for line in fh:
                parts = line.rstrip("\r\n").split(delim, idx + 1)
                if len(parts) > idx:
                    t = parse_timestamp(parts[idx])
                    if np.isfinite(t):
                        return t
        return float("inf")

    return sorted(chosen, key=first_time)


def resolve_cooldown(name: str, data_root: Path | None = None) -> Path:
    """'2026-06' / '2026-06-Cooldown' / an explicit directory -> directory."""
    p = Path(name)
    if p.is_dir():
        return p
    root = Path(data_root or os.environ.get("GENESIS_REPLAY_DATA", ""))
    if not str(root) or not root.is_dir():
        raise FileNotFoundError(
            "set GENESIS_REPLAY_DATA to the directory of past cool-downs "
            "(e.g. .../Optimizied-McClellan-Fits/data) or pass a cool-down directory")
    for candidate in (root / name, root / f"{name}-Cooldown"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"no cool-down {name!r} under {root}")


def list_cooldowns(data_root: Path) -> list[Path]:
    return sorted(p for p in Path(data_root).iterdir()
                  if p.is_dir() and any(p.glob("data_record_*.csv")))


# ------------------------------------------------------------------ table
@dataclass
class SlowTable:
    """Parallel arrays for one or more data_record files."""

    t: np.ndarray                                   # epoch seconds, ascending
    numeric: dict[str, np.ndarray] = field(default_factory=dict)
    text: dict[str, np.ndarray] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.t)

    @property
    def channels(self) -> set[str]:
        return set(self.numeric) | set(self.text)

    def concat(self, other: "SlowTable") -> "SlowTable":
        keys_n = set(self.numeric) | set(other.numeric)
        keys_s = set(self.text) | set(other.text)

        def pad_n(tbl, k):
            return tbl.numeric.get(k, np.full(len(tbl), np.nan))

        def pad_s(tbl, k):
            return tbl.text.get(k, np.full(len(tbl), "", dtype=object))

        t = np.concatenate([self.t, other.t])
        order = np.argsort(t, kind="stable")
        out = SlowTable(
            t=t[order],
            numeric={k: np.concatenate([pad_n(self, k), pad_n(other, k)])[order] for k in keys_n},
            text={k: np.concatenate([pad_s(self, k), pad_s(other, k)])[order] for k in keys_s},
            sources=self.sources + other.sources,
            skipped={k: self.skipped.get(k, 0) + other.skipped.get(k, 0)
                     for k in set(self.skipped) | set(other.skipped)},
        )
        return out


def read_slow(path: Path, channels=None) -> SlowTable:
    """Read the named slow-control columns of one data_record file."""
    path = Path(path)
    header, delim = read_header(path)
    wanted = list(channels) if channels is not None else list(aliases.CHANNELS)
    if "timestamp" not in wanted:
        wanted.insert(0, "timestamp")
    cols = aliases.resolve(header, wanted)
    if "timestamp" not in cols:
        raise ValueError(f"{path}: no Timestamp/Time column")
    maxcol = max(cols.values())
    tz = lab_tz()

    times: list[float] = []
    nums = {c: [] for c in cols if c not in aliases.STRING_CHANNELS and c != "timestamp"}
    strs = {c: [] for c in cols if c in aliases.STRING_CHANNELS and c != "timestamp"}
    skipped = {"bad-timestamp": 0, "short-row": 0}
    ts_cache: dict[str, float] = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        fh.readline()
        for line in fh:
            parts = line.rstrip("\r\n").split(delim, maxcol + 1)
            if len(parts) <= maxcol:
                if line.strip():
                    skipped["short-row"] += 1
                continue
            raw_ts = parts[cols["timestamp"]].strip()
            t = ts_cache.get(raw_ts)
            if t is None:
                t = parse_timestamp(raw_ts, tz)
                if len(ts_cache) < 200_000:
                    ts_cache[raw_ts] = t
            if not np.isfinite(t):
                skipped["bad-timestamp"] += 1
                continue
            times.append(t)
            for c, lst in nums.items():
                cell = parts[cols[c]]
                if c == "mw_rf_status":
                    lst.append(_rf_status(cell))
                else:
                    lst.append(aliases.parse_number(cell))
            for c, lst in strs.items():
                lst.append(_status_text(parts[cols[c]]))

    t = np.asarray(times, float)
    order = np.argsort(t, kind="stable")
    table = SlowTable(
        t=t[order],
        numeric={c: np.asarray(v, float)[order] for c, v in nums.items()},
        text={c: np.asarray(v, dtype=object)[order] for c, v in strs.items()},
        sources=[str(path)],
        skipped={k: v for k, v in skipped.items() if v},
    )
    if "mw_freq_ghz" in table.numeric:          # the DAQ's RF-off sentinel is not a frequency
        f = table.numeric["mw_freq_ghz"]
        f[np.isclose(f, aliases.MW_FREQ_OFF_SENTINEL, atol=1e-3)] = np.nan
    return table


def read_cooldown(cooldown_dir: Path, channels=None, start: float | None = None,
                  stop: float | None = None) -> SlowTable:
    """All days of one cool-down (optionally clipped to [start, stop] epoch s)."""
    table = None
    for path in day_files(cooldown_dir):
        part = read_slow(path, channels)
        table = part if table is None else table.concat(part)
    if table is None:
        raise FileNotFoundError(f"no data_record files in {cooldown_dir}")
    if start is not None or stop is not None:
        lo = -np.inf if start is None else start
        hi = np.inf if stop is None else stop
        keep = (table.t >= lo) & (table.t <= hi)
        table = SlowTable(table.t[keep], {k: v[keep] for k, v in table.numeric.items()},
                          {k: v[keep] for k, v in table.text.items()}, table.sources, table.skipped)
    return table


def _rf_status(cell: str) -> float:
    s = str(cell).strip().lower()
    if s in ("on", "true"):
        return 1.0
    if s in ("off", "false"):
        return 0.0
    v = aliases.parse_number(s)
    return v if v in (0.0, 1.0) else float("nan")


def _status_text(cell: str) -> str:
    """Status strings; the DAQ's numeric placeholders ('0', '0.0') mean 'not logged'."""
    s = str(cell).strip()
    if s in ("0", "0.0", "0.000000", "---", "NaN", "Null"):
        return ""
    return s
