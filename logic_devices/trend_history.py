"""
logic_devices/trend_history.py - recent history of trended PVs, served as arrays.

Why this exists
---------------
A Phoebus strip chart only plots values that arrive while its display is
open, so every trend on the Panopticon starts blank whenever the screen is
opened or reloaded.  Phoebus can back-fill a strip chart only from an archive
data source it knows (an Archiver Appliance, an RDB); the Genesis archiver
(logic_devices/archiver.py) writes per-PV CSV files, which Phoebus cannot read.

This IOC keeps the last few hours of each configured PV in a ring buffer and
publishes it as waveform PVs, so an XY plot shows the history the moment it
connects.  At start-up it back-fills the buffers from the archiver's CSV files,
so a restart of Genesis or of Phoebus still shows the history.

PVs (prefix from settings.yaml, e.g. UNH:GEN:HIST)

    <GROUP>:TIME       waveform  sample times relative to T_REF, in the group's
                                 time_unit (min or h): negative, newest = 0
    <GROUP>:<KEY>      waveform  the source PV's samples (x scale), same length
    <GROUP>:T_REF      aIn       epoch seconds of TIME = 0 (the newest sample)
    <GROUP>:<KEY>_NOW  aIn       the latest converted value, for sources with a
                                 unit_pv (a read-back in the shown unit)
    Hist_Status        mbbIn     Starting / Running / Error
    Hist_Sources       longIn    source PVs configured
    Hist_Connected     longIn    source PVs currently connected
    Hist_Backfilled    longIn    samples loaded from the archive at start-up

Every array of a group has the same, fixed length (window / interval, at most
MAX_POINTS) and is ordered oldest first.  Slots not filled yet are at the
START of the arrays: their TIME continues the sample grid backwards and their
values are NaN, so a plot draws nothing there and no line runs back across the
plot from a padded tail.  A gap in the samples (the IOC or the archiver was not
running, the clock jumped forward) gets one NaN sample, which breaks the line.

Time base
---------
Wall-clock epoch seconds (time.time()), the same clock the archiver stamps its
rows with (datetime.now(), local time).  Under the cool-down simulator the
IOCs, the archiver and this service all run on wall time while the simulated
cool-down advances at N x speed, so at speed N a 15-minute window spans
N x 15 simulated minutes -- exactly as the strip charts did.

Back-fill
---------
Archiver rows are written on a relative deadband or after ``time_increment``
seconds, so they are irregular.  Each sample time of the window takes the last
archived value at or before it (sample-and-hold), unless that row is older than
``max_hold`` seconds, in which case the sample is NaN (a gap).  Rows whose value
is not a number (the archiver writes the disconnect object when a PV drops
out) are NaN, so disconnects show as gaps too.

Values are float32 and arrays at most MAX_POINTS long, so every waveform stays
below the default EPICS_CA_MAX_ARRAY_BYTES (16 kB) of clients that never set it.

settings.yaml

    trend_history:
      module: logic_devices.trend_history
      delay: 1                  # loop period (s); samples are taken on this tick
      archiver_settings: configs/target_physical/archiver/settings.yaml
      max_hold: 900             # s, longest an archived value is held when back-filling
      window: 21600             # s, default history length per group
      interval: 12              # s, default sample interval
      groups:
        MAG:
          time_unit: min
          sources:
            M1_TI: UNH:GEN:CRYO:MAG:M1_TI
        PRESS:
          sources:
            # the TPG 262 reads in its display unit (mbar, Torr or Pa, in
            # Pump_Unit): converted to Torr whatever the controller is set to
            Pump_Torr: {pv: UNH:GEN:CRYO:VAC:Pump_PI, unit_pv: UNH:GEN:CRYO:VAC:Pump_Unit,
                        to: Torr, egu: Torr}

Units
-----
A source with ``unit_pv`` follows that text PV (e.g. the TPG 262's Pump_Unit)
and converts every sample to ``to``: live from the unit PV's monitor, and in
the back-fill from the unit PV's own archive (the unit in force at each
sample).  Until the unit is known the values are NaN: a number in an unknown
unit is not shown.
"""

import csv
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml
from softioc import builder

from genesis_common.upstream_base_device import BaseDevice

#: longest array served (float32: 8 kB, under the 16 kB CA default)
MAX_POINTS = 2000
TIME_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0}
#: a sample further than this many intervals after the previous one starts a new line
GAP_INTERVALS = 2.5
ARCHIVE_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")
NAN = float("nan")
#: reading in <unit> -> the shown unit (keys lower case)
UNIT_FACTORS = {
    "torr": {"torr": 1.0, "mbar": 0.750062, "hpa": 0.750062, "pa": 0.00750062, "micron": 1e-3},
    "mbar": {"mbar": 1.0, "hpa": 1.0, "torr": 1.333224, "pa": 0.01},
}


def unit_factor(unit, to: str) -> float:
    """Factor taking a reading in ``unit`` (text, e.g. 'mbar') to ``to``; NaN if unknown."""
    if unit is None or getattr(unit, 'ok', True) is False:
        return NAN
    return UNIT_FACTORS.get(str(to).strip().lower(), {}).get(str(unit).strip().lower(), NAN)


# --------------------------------------------------------------- archive files
def safe_pv_name(pv: str) -> str:
    """File-name form of a PV name, exactly as logic_devices.archiver writes it."""
    return pv.replace(':', '_').replace('/', '_')


def archive_file(archive_path, pv: str, day: date) -> Path:
    """The archiver's daily CSV of ``pv``: {safe_pv_name}_{YYYY-MM-DD}.csv."""
    return Path(archive_path) / f"{safe_pv_name(pv)}_{day:%Y-%m-%d}.csv"


def to_float(value) -> float:
    """A plottable float, or NaN (disconnected, text, inf)."""
    if value is None or getattr(value, 'ok', True) is False:
        return NAN
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def parse_archive_time(text: str) -> float | None:
    """Epoch seconds of an archiver timestamp (naive local time), or None."""
    text = text.strip()
    for fmt in ARCHIVE_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def read_archive(archive_path, pv: str, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
    """Archived (times, values) of ``pv`` with t0 <= time <= t1, sorted by time.

    Missing files, a missing header, short or garbled rows are skipped; a value
    that is not a number becomes NaN (the archiver writes the disconnect object
    when a PV drops out)."""
    t, text = read_archive_text(archive_path, pv, t0, t1)
    return t, np.asarray([to_float(x) for x in text], dtype=float)


def read_archive_text(archive_path, pv: str, t0: float, t1: float) -> tuple[np.ndarray, list]:
    """Archived (times, value texts) of ``pv``, as read_archive but not parsed."""
    times, values = [], []
    day = datetime.fromtimestamp(t0).date() - timedelta(days=1)     # rows near midnight
    last = datetime.fromtimestamp(t1).date()
    while day <= last:
        path = archive_file(archive_path, pv, day)
        day += timedelta(days=1)
        try:
            fh = open(path, newline='', encoding='utf-8', errors='replace')
        except OSError:
            continue
        with fh:
            for row in csv.reader(fh):
                if len(row) < 2:
                    continue
                t = parse_archive_time(row[0])
                if t is None or t < t0 or t > t1:
                    continue
                times.append(t)
                values.append(row[1])
    if not times:
        return np.empty(0), []
    t = np.asarray(times, dtype=float)
    order = np.argsort(t, kind='stable')
    return t[order], [values[i] for i in order]


def hold_at(row_t: np.ndarray, row_v: np.ndarray, grid: np.ndarray, max_hold: float) -> np.ndarray:
    """Sample-and-hold: at each grid time, the last row at or before it, unless
    that row is older than ``max_hold`` (then NaN)."""
    out = np.full(len(grid), np.nan)
    if not len(row_t):
        return out
    idx = np.searchsorted(row_t, grid, side='right') - 1
    ok = idx >= 0
    age = np.where(ok, grid - row_t[np.clip(idx, 0, None)], np.inf)
    ok &= age <= max_hold
    out[ok] = row_v[idx[ok]]
    return out


def archiver_archive_path(settings_file) -> Path | None:
    """archive_path of the archiver configured in ``settings_file`` (an archiver
    settings.yaml), or None if it cannot be read."""
    try:
        with open(settings_file, encoding='utf-8') as fh:
            config = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    for key, block in config.items():
        if key == 'general' or not isinstance(block, dict):
            continue
        if str(block.get('module', '')).endswith('archiver') or key == 'archiver':
            # resolved exactly as the archiver resolves it (relative paths live
            # under the Genesis data root, archive/data_paths.py)
            requested = block.get('archive_path', 'archive/data')
            try:
                from archive.data_paths import legacy_archive_path
            except ImportError:
                return Path(requested)
            return Path(legacy_archive_path(requested))
    return None


# ---------------------------------------------------------------- the buffers
class RingBuffer:
    """Fixed-capacity buffer of (time, one value per source) samples."""

    def __init__(self, n_sources: int, capacity: int):
        self.capacity = int(capacity)
        self._t = np.full(self.capacity, np.nan)
        self._v = np.full((n_sources, self.capacity), np.nan)
        self._next = 0
        self.count = 0

    def append(self, t: float, values) -> None:
        i = self._next
        self._t[i] = t
        self._v[:, i] = values
        self._next = (i + 1) % self.capacity
        self.count = min(self.count + 1, self.capacity)

    def last_time(self) -> float:
        return NAN if not self.count else float(self._t[(self._next - 1) % self.capacity])

    def drop_after(self, t: float) -> int:
        """Forget samples newer than ``t`` (the clock stepped back).  Returns how many."""
        dropped = 0
        while self.count and self.last_time() > t:
            self._next = (self._next - 1) % self.capacity
            self._t[self._next] = np.nan
            self._v[:, self._next] = np.nan
            self.count -= 1
            dropped += 1
        return dropped

    def ordered(self) -> tuple[np.ndarray, np.ndarray]:
        """(times, values[source, sample]), oldest first, only the filled part."""
        idx = (np.arange(self.count) + self._next - self.count) % self.capacity
        return self._t[idx], self._v[:, idx]


@dataclass
class Source:
    key: str                    # record name inside the group, e.g. M1_TI
    pv: str
    scale: float = 1.0
    egu: str = ''
    unit_pv: str = ''           # text PV naming the source's unit (converted to ``to``)
    to: str = ''


@dataclass
class Group:
    name: str
    sources: list
    window: float = 21600.0
    interval: float = 12.0
    time_unit: str = 'min'
    buffer: RingBuffer = None
    latest: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)          # latest value before the unit factor
    factor: dict = field(default_factory=dict)       # current unit factor per source
    next_due: float = 0.0

    def __post_init__(self):
        if self.time_unit not in TIME_UNITS:
            raise ValueError(f"group {self.name}: time_unit must be one of {sorted(TIME_UNITS)}")
        if self.interval <= 0 or self.window <= 0:
            raise ValueError(f"group {self.name}: window and interval must be positive")
        if self.window / self.interval > MAX_POINTS:
            # keep the window, coarsen the sampling
            self.interval = self.window / MAX_POINTS
            print(f"Trend history: group {self.name} interval raised to {self.interval:g} s "
                  f"({MAX_POINTS} points max)")
        self.buffer = RingBuffer(len(self.sources), math.ceil(self.window / self.interval))
        self.latest = {s.key: NAN for s in self.sources}
        self.raw = {s.key: NAN for s in self.sources}
        self.factor = {s.key: (NAN if s.unit_pv else 1.0) for s in self.sources}

    @property
    def unit_seconds(self) -> float:
        return TIME_UNITS[self.time_unit]

    def update(self, key: str, value) -> None:
        """A monitor update (or disconnect) of one source."""
        src = next(s for s in self.sources if s.key == key)
        self.raw[key] = to_float(value) * src.scale
        self.latest[key] = self.raw[key] * self.factor[key]

    def set_unit(self, unit_pv: str, unit) -> None:
        """The unit PV ``unit_pv`` now reads ``unit``: re-convert its sources."""
        for s in self.sources:
            if s.unit_pv == unit_pv:
                self.factor[s.key] = unit_factor(unit, s.to)
                self.latest[s.key] = self.raw[s.key] * self.factor[s.key]

    def sample(self, now: float) -> None:
        """Append the current value of every source at ``now``."""
        last = self.buffer.last_time()
        if not math.isnan(last):
            if now < last:                                      # clock stepped back
                self.buffer.drop_after(now)
                last = self.buffer.last_time()
            if not math.isnan(last) and now - last > GAP_INTERVALS * self.interval:
                self.buffer.append(last + self.interval, [NAN] * len(self.sources))
        self.buffer.append(now, [self.latest[s.key] for s in self.sources])

    def due(self, now: float) -> bool:
        if now < self.next_due - self.interval:                 # clock stepped back
            self.next_due = now
        if now < self.next_due:
            return False
        # next sample one interval on; after a stall, re-phase on now
        self.next_due = (self.next_due + self.interval
                         if now - self.next_due < self.interval else now + self.interval)
        return True

    def backfill(self, archive_path, now: float, max_hold: float) -> int:
        """Fill the buffer from the archiver's CSVs for the window before ``now``.
        Returns the number of samples loaded (leading samples with no data at
        all are skipped)."""
        n = self.buffer.capacity - 1                            # leave the newest slot for 'now'
        grid = now - self.interval * np.arange(n, 0, -1, dtype=float)
        columns = []
        for s in self.sources:
            row_t, row_v = read_archive(archive_path, s.pv, now - self.window - max_hold, now)
            factor = np.ones(len(grid))
            last_factor = 1.0
            if s.unit_pv:
                # the unit in force at each sample (written on change, held indefinitely)
                ut, utext = read_archive_text(archive_path, s.unit_pv, 0.0, now)
                codes = np.asarray([unit_factor(u, s.to) for u in utext], dtype=float)
                factor = hold_at(ut, codes, grid, math.inf) if len(ut) else np.full(len(grid), np.nan)
                last_factor = float(codes[-1]) if len(codes) else NAN
            columns.append(hold_at(row_t, row_v, grid, max_hold) * s.scale * factor)
            if len(row_v) and not math.isnan(row_v[-1]) and now - row_t[-1] <= max_hold:
                self.raw[s.key] = row_v[-1] * s.scale           # until the monitor's first update
                if s.unit_pv and math.isnan(self.factor[s.key]):
                    self.factor[s.key] = last_factor
                self.latest[s.key] = self.raw[s.key] * self.factor[s.key]
        values = np.vstack(columns) if columns else np.empty((0, n))
        have = np.flatnonzero(~np.all(np.isnan(values), axis=0))
        if not len(have):
            return 0
        for i in range(have[0], n):
            self.buffer.append(float(grid[i]), values[:, i])
        return n - int(have[0])

    def arrays(self, t_ref: float) -> tuple[np.ndarray, list]:
        """(TIME, [values per source]) as float32 arrays of the buffer's full
        capacity, oldest first, the unfilled slots first with NaN values."""
        cap = self.buffer.capacity
        t, v = self.buffer.ordered()
        pad = cap - len(t)
        oldest = t[0] if len(t) else t_ref
        times = np.empty(cap)
        times[pad:] = t
        times[:pad] = oldest - self.interval * np.arange(pad, 0, -1)
        rel = ((times - t_ref) / self.unit_seconds).astype(np.float32)
        out = []
        for i in range(len(self.sources)):
            col = np.full(cap, np.nan, dtype=np.float32)
            col[pad:] = v[i]
            out.append(col)
        return rel, out


def parse_groups(settings: dict) -> list:
    """Groups from the settings: defaults window/interval/time_unit, per-group
    overrides; a source is 'KEY: PV' or 'KEY: {pv, scale, egu, unit_pv, to}'."""
    window = float(settings.get('window', 21600))
    interval = float(settings.get('interval', 12))
    unit = settings.get('time_unit', 'min')
    groups = []
    for name, spec in (settings.get('groups') or {}).items():
        spec = spec or {}
        sources = []
        for key, src in (spec.get('sources') or {}).items():
            if isinstance(src, dict):
                if bool(src.get('unit_pv')) != bool(src.get('to')):
                    raise ValueError(f"trend history {name}:{key}: unit_pv and to go together")
                sources.append(Source(str(key), str(src['pv']), float(src.get('scale', 1.0)),
                                      str(src.get('egu', '')), str(src.get('unit_pv') or ''),
                                      str(src.get('to') or '')))
            else:
                sources.append(Source(str(key), str(src)))
        if not sources:
            raise ValueError(f"trend history group {name} has no sources")
        groups.append(Group(str(name), sources, float(spec.get('window', window)),
                            float(spec.get('interval', interval)), spec.get('time_unit', unit)))
    return groups


def published_pvs(prefix: str, settings: dict) -> dict:
    """{history PV: source PV or None} for every PV this IOC serves (no MAN: heartbeat)."""
    out = {f"{prefix}:{r}": None for r in ('Hist_Status', 'Hist_Sources', 'Hist_Connected',
                                           'Hist_Backfilled')}
    for g in parse_groups(settings):
        out[f"{prefix}:{g.name}:TIME"] = None
        out[f"{prefix}:{g.name}:T_REF"] = None
        out.update({f"{prefix}:{g.name}:{s.key}": s.pv for s in g.sources})
        out.update({f"{prefix}:{g.name}:{s.key}_NOW": s.pv for s in g.sources if s.unit_pv})
    return out


# --------------------------------------------------------------------- device
class Device(BaseDevice):
    """Trend history: monitors the sources, samples them, serves the arrays."""

    def __init__(self, device_name, settings):
        self.groups = parse_groups(settings)
        self.max_hold = float(settings.get('max_hold', 900))
        if settings.get('archive_path'):
            self.archive_path = Path(settings['archive_path'])
        elif settings.get('archiver_settings'):
            self.archive_path = archiver_archive_path(settings['archiver_settings'])
            if self.archive_path is None:
                print(f"Trend history: cannot read {settings['archiver_settings']}; no back-fill")
        else:
            self.archive_path = None
        self.clock = time.time
        self.monitors = []
        self.connected = set()
        self._started = False
        self.backfilled = 0
        super().__init__(device_name, settings)
        if settings.get('backfill', True) and self.archive_path is not None:
            self.backfill()

    def _create_pvs(self):
        self.pvs['Hist_Status'] = builder.mbbIn('Hist_Status', 'Starting', 'Running', 'Error')
        self.pvs['Hist_Sources'] = builder.longIn('Hist_Sources')
        self.pvs['Hist_Connected'] = builder.longIn('Hist_Connected')
        self.pvs['Hist_Backfilled'] = builder.longIn('Hist_Backfilled')
        for g in self.groups:
            rel, cols = g.arrays(self.clock())
            self.pvs[f'{g.name}:TIME'] = builder.WaveformIn(
                f'{g.name}:TIME', initial_value=rel, EGU=g.time_unit,
                DESC=f'{g.name} sample time rel. to T_REF')
            self.pvs[f'{g.name}:T_REF'] = builder.aIn(
                f'{g.name}:T_REF', initial_value=0.0, EGU='s', PREC=3,
                DESC=f'{g.name} epoch of TIME = 0')
            for s, col in zip(g.sources, cols):
                self.pvs[f'{g.name}:{s.key}'] = builder.WaveformIn(
                    f'{g.name}:{s.key}', initial_value=col, EGU=s.egu[:15],
                    DESC=f'History of {s.pv}'[:40])
                if s.unit_pv:
                    self.pvs[f'{g.name}:{s.key}_NOW'] = builder.aIn(
                        f'{g.name}:{s.key}_NOW', initial_value=NAN, EGU=s.egu[:15], PREC=4,
                        DESC=f'{s.pv} in {s.to}'[:40])
        self.pvs['Hist_Status'].set(0)
        self.pvs['Hist_Sources'].set(sum(len(g.sources) for g in self.groups))
        self.pvs['Hist_Connected'].set(0)
        self.pvs['Hist_Backfilled'].set(0)

    def _create_connection(self):
        """No instrument: the sources are monitored over Channel Access."""
        return None

    # ------------------------------------------------------------ back-fill
    def backfill(self):
        now = self.clock()
        total = 0
        for g in self.groups:
            try:
                total += g.backfill(self.archive_path, now, self.max_hold)
            except Exception as e:  # noqa: BLE001 - a bad archive must not stop the IOC
                print(f"Trend history: back-fill of {g.name} failed: {e}")
            g.next_due = now                                    # first live sample at once
            self.publish(g, now)
        self.backfilled = total
        self.pvs['Hist_Backfilled'].set(total)
        print(f"Trend history: back-filled {total} samples from {self.archive_path}")

    # ------------------------------------------------------------- monitors
    def _start_monitors(self):
        import aioca
        for g in self.groups:
            for s in g.sources:
                try:
                    self.monitors.append(aioca.camonitor(
                        s.pv, self._callback(g, s), notify_disconnect=True))
                except Exception as e:  # noqa: BLE001
                    print(f"Trend history: cannot monitor {s.pv}: {e}")
        for unit_pv in sorted({s.unit_pv for g in self.groups for s in g.sources if s.unit_pv}):
            try:
                self.monitors.append(aioca.camonitor(
                    unit_pv, self._unit_callback(unit_pv), notify_disconnect=True))
            except Exception as e:  # noqa: BLE001
                print(f"Trend history: cannot monitor {unit_pv}: {e}")
        self._started = True
        print(f"Trend history: monitoring {sum(len(g.sources) for g in self.groups)} PVs")

    def _callback(self, group, source):
        def callback(value):
            group.update(source.key, value)
            ok = getattr(value, 'ok', True) is not False
            (self.connected.add if ok else self.connected.discard)(source.pv)
        return callback

    def _unit_callback(self, unit_pv):
        def callback(value):
            for g in self.groups:
                g.set_unit(unit_pv, value)
        return callback

    # ----------------------------------------------------------------- loop
    def publish_now(self, group):
        for s in group.sources:
            if s.unit_pv:
                self.pvs[f'{group.name}:{s.key}_NOW'].set(group.latest[s.key])

    def publish(self, group, t_ref):
        rel, cols = group.arrays(t_ref)
        self.pvs[f'{group.name}:TIME'].set(rel)
        for s, col in zip(group.sources, cols):
            self.pvs[f'{group.name}:{s.key}'].set(col)
        self.pvs[f'{group.name}:T_REF'].set(t_ref)

    async def do_reads(self):
        try:
            if not self._started:
                self._start_monitors()
            now = self.clock()
            for g in self.groups:
                if g.due(now):
                    g.sample(now)
                    self.publish(g, now)
                self.publish_now(g)
            self.pvs['Hist_Connected'].set(len(self.connected))
            self.pvs['Hist_Status'].set(1)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"Trend history error: {e}")
            self.pvs['Hist_Status'].set(2)
            return False
