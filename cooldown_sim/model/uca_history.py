"""History UCA voltage: what the microwave attenuator was set to in past cool-downs.

RF is switched ON with the UCA (user-controlled attenuator) at 5 V -- no
power -- and the operator later lowers it BY HAND (0 V = full power), often
only after alignment or tests; it goes back to 5 V before RF OFF.  The UCA
has been driven by a manual supply since about 2022, so the record's
``UCA (V)`` column is meaningless there; the setting is taken from the
cool-down logbooks instead (``uca_logbook.yaml``, one time-ordered list of
settings per cool-down, each ``stated`` or ``inferred``).

The history setting at time t (only meaningful while the record shows RF on):

1. RF OFF resets: every RF-on period starts at 5 V (``UCA_SAFE_V``).
2. Within the period the last logbook setting at or before t holds (an entry
   up to ``ENTRY_SKEW_S`` before the record's RF ON counts as in the period).
3. Before the first logbook setting of the period the UCA is at 5 V -- the
   procedure; the logbook says when it was lowered -- UNLESS that first
   setting is itself "off" (>= ``UCA_OFF_V``: nobody switches off what was not
   on) or the period has no logbook setting at all.  Then the fallback below
   applies.
4. Fallback for an unlogged RF-on period (and every period of a cool-down with
   no entries, or of a record outside every logged cool-down): 5 V for
   ``UNLOGGED_DELAY_S`` after RF ON, then ``UNLOGGED_UCA_V`` (0 V, full power).
   The 5 min is the typical logged gap between RF ON and the UCA reaching
   0 V (3-15 min in 2021-09, 2024-12 and 2026-06); full power is what an RF-on
   period without any note has always meant in practice -- the record's
   polarization builds in those periods.  A period already on at the start of
   the loaded record takes the last logbook setting before t, else full power.
5. In cool-downs whose ``uca_source`` is ``record`` (2020-2021: the DAQ drove
   the UCA and logged it; its steps match the logbook) the record's ``uca_v``
   column is used wherever it has a value.

Times in the YAML are lab local time (America/New_York), like the record.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ..data import records
from ..data.timeline import Timeline

LOG_FILE = Path(__file__).with_name("uca_logbook.yaml")
#: UCA voltage with no microwave output: RF is enabled at it and it is set before RF OFF
UCA_SAFE_V = 5.0
#: settings at or above this give no output (vendor curve): such an entry switches power off
UCA_OFF_V = 4.0
#: fallback for an RF-on period the logbook says nothing about: UNLOGGED_UCA_V after this delay
UNLOGGED_DELAY_S = 300.0
UNLOGGED_UCA_V = 0.0
#: an entry this little before the record's RF ON still belongs to that period (logbook
#: times are whole minutes; the DAQ and logbook clocks differ by up to a minute)
ENTRY_SKEW_S = 60.0
#: record-era UCA steps closer than this are one operator action (the DAQ stepped every ~20 s)
RECORD_MERGE_S = 120.0
#: smallest record-era UCA change that counts as a change (V)
RECORD_STEP_V = 0.05
BASES = ("stated", "inferred")


@dataclass(frozen=True)
class Entry:
    t: float                    # epoch s
    uca_V: float
    basis: str                  # stated | inferred
    note: str = ""


@dataclass(frozen=True)
class CooldownLog:
    name: str
    start: float
    end: float
    source: str                 # logbook | record
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Setting:
    """History UCA voltage at some t and where it comes from."""

    uca_V: float
    source: str                 # stated | inferred | procedure | fallback | record | rf_off
    since: float                # when it took effect (entry time, RF ON, fallback switch); NaN if unknown


@dataclass(frozen=True)
class Change:
    """One UCA change while RF is on, with its neighbouring changes (for step fits)."""

    t: float
    v_before: float
    v_after: float
    basis: str                  # stated | inferred | record
    before: str                 # source of v_before (Setting.source)
    prev_t: float               # previous change in the period (RF ON included)
    next_t: float               # next change in the period (RF OFF included)
    exact: bool                 # time known to the record's sampling (record era)
    note: str = ""


def parse_time(text: str) -> float:
    """'YYYY-MM-DD HH:MM' lab local -> epoch s."""
    d = _dt.datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M")
    return d.replace(tzinfo=records.lab_tz()).timestamp()


_CACHE: dict[Path, dict[str, CooldownLog]] = {}


def load(path: Path = LOG_FILE) -> dict[str, CooldownLog]:
    """The logbook UCA timeline, per cool-down (cached)."""
    path = Path(path)
    if path not in _CACHE:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        out = {}
        for name, cd in (raw.get("cooldowns") or {}).items():
            entries = tuple(sorted(
                (Entry(parse_time(e["t"]), float(e["uca_V"]), str(e["basis"]), str(e.get("note", "")))
                 for e in cd.get("entries") or []), key=lambda e: e.t))
            for e in entries:
                if e.basis not in BASES:
                    raise ValueError(f"{path.name}: {name} entry basis {e.basis!r} not in {BASES}")
            out[name] = CooldownLog(name, parse_time(cd["span"][0]), parse_time(cd["span"][1]),
                                    str(cd.get("uca_source", "logbook")), entries)
        _CACHE[path] = out
    return _CACHE[path]


class UcaHistory:
    """History UCA setting over one Timeline (see module doc for the rules)."""

    def __init__(self, tl: Timeline, logs: dict[str, CooldownLog] | None = None):
        self.tl = tl
        logs = load() if logs is None else logs
        lo, hi = tl.start, tl.end
        self.logs = [c for c in logs.values()
                     if math.isfinite(lo) and c.start <= hi and c.end >= lo]
        self.entries = sorted((e for c in self.logs for e in c.entries), key=lambda e: e.t)
        self._et = [e.t for e in self.entries]
        self.record_uca = any(c.source == "record" for c in self.logs) and tl.has("uca_v")
        self.runs = self._rf_runs()
        self._on = [r[0] for r in self.runs]

    # ------------------------------------------------------------ RF runs
    def _rf_runs(self) -> list[tuple[float, float, bool]]:
        """(t_on, t_off, open_start) for each RF-on period of the record; open_start
        when the record already starts with RF on (the real RF ON is earlier)."""
        t, v = self.tl.samples("mw_rf_status")
        if not len(t):
            return []
        on = v > 0.5
        out = []
        i, n = 0, len(t)
        while i < n:
            if not on[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and on[j + 1]:
                j += 1
            t_off = float(t[j + 1]) if j + 1 < n else float(t[j]) + self.tl.max_hold_s
            out.append((float(t[i]), t_off, i == 0))
            i = j + 1
        return out

    def run_at(self, t: float) -> tuple[float, float, bool] | None:
        k = bisect.bisect_right(self._on, t) - 1
        if k < 0:
            return None
        run = self.runs[k]
        return run if t < run[1] else None

    # ------------------------------------------------------------ lookup
    def setting(self, t: float) -> Setting:
        run = self.run_at(t)
        if run is None:
            return Setting(UCA_SAFE_V, "rf_off", float("nan"))
        t_on, t_off, open_start = run
        if self.record_uca:
            v = self.tl.value_at("uca_v", t)
            if math.isfinite(v):
                return Setting(v, "record", float("nan"))
        lo = -math.inf if open_start else t_on - ENTRY_SKEW_S
        i = bisect.bisect_right(self._et, t)
        if i > 0 and self._et[i - 1] >= lo:
            e = self.entries[i - 1]
            return Setting(e.uca_V, e.basis, e.t)
        j = bisect.bisect_left(self._et, lo)
        first = self.entries[j] if j < len(self.entries) and self._et[j] < t_off else None
        if first is not None and first.uca_V < UCA_OFF_V:
            return Setting(UCA_SAFE_V, "procedure", t_on)        # the logbook says when it was lowered
        t_full = -math.inf if open_start else t_on + UNLOGGED_DELAY_S
        if t >= t_full:
            return Setting(UNLOGGED_UCA_V, "fallback", t_full)
        return Setting(UCA_SAFE_V, "procedure", t_on)

    def uca_V(self, t: float) -> float:
        return self.setting(t).uca_V

    # ------------------------------------------------------------ changes
    def changes(self) -> list[Change]:
        """Every UCA change while RF is on: logbook entries (stated and inferred)
        and, in the record era, the record's own steps; with their neighbours."""
        out: list[Change] = []
        for t_on, t_off, _open in self.runs:
            events = self._run_events(t_on, t_off)
            times = [t_on] + [e[0] for e in events] + [t_off]
            for k, (ts, v_after, basis, exact, note) in enumerate(events):
                before = self.setting(ts - 1.0)
                if not self.run_at(ts - 1.0):
                    continue
                out.append(Change(ts, before.uca_V, v_after, basis, before.source,
                                  times[k], times[k + 2], exact, note))
        return out

    def _run_events(self, t_on: float, t_off: float) -> list[tuple]:
        if self.record_uca:
            ts, vs = self.tl.samples("uca_v", t_on, t_off)
            if len(ts):
                return _record_steps(ts, vs)
        return [(max(e.t, t_on), e.uca_V, e.basis, False, e.note) for e in self.entries
                if t_on - ENTRY_SKEW_S <= e.t < t_off]


def _record_steps(ts: np.ndarray, vs: np.ndarray) -> list[tuple]:
    """Record-era UCA steps, merging a run of DAQ ticks into one operator action."""
    idx = np.nonzero(np.abs(np.diff(vs)) > RECORD_STEP_V)[0]
    groups: list[list[int]] = []
    for i in idx:
        if groups and ts[i + 1] - ts[groups[-1][-1] + 1] <= RECORD_MERGE_S:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [(float(ts[g[0] + 1]), float(vs[g[-1] + 1]), "record", True, "")
            for g in groups if abs(vs[g[-1] + 1] - vs[g[0]]) > RECORD_STEP_V]


def for_timeline(tl: Timeline) -> UcaHistory:
    """The (cached) UcaHistory of a Timeline."""
    h = getattr(tl, "_uca_history", None)
    if h is None:
        h = UcaHistory(tl)
        tl._uca_history = h
    return h
