"""Sample-and-hold lookups over a cool-down's slow-control record.

The DAQ logged a row every ~4-20 s; an instrument emulator is polled every
0.5-2 s of *wall* time at up to 600x speed, so it needs "the value the
instrument would have shown at sim time t".  That is the last finite sample at
or before t -- held for at most ``max_hold_s``.  Beyond that (a DAQ gap, or a
column that is NaN for a stretch) the value is NaN: the simulator never
interpolates or invents a reading the record does not contain.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .records import SlowTable

DEFAULT_MAX_HOLD_S = 300.0


@dataclass
class _Series:
    t: np.ndarray
    v: np.ndarray


class Timeline:
    def __init__(self, table: SlowTable, max_hold_s: float = DEFAULT_MAX_HOLD_S):
        self.table = table
        self.max_hold_s = float(max_hold_s)
        self._series: dict[str, _Series] = {}
        for name, values in table.numeric.items():
            ok = np.isfinite(values)
            self._series[name] = _Series(table.t[ok], values[ok])
        self._text: dict[str, _Series] = {}
        for name, values in table.text.items():
            ok = np.array([bool(s) for s in values], dtype=bool)
            self._text[name] = _Series(table.t[ok], values[ok])

    # ------------------------------------------------------------- coverage
    @property
    def start(self) -> float:
        return float(self.table.t[0]) if len(self.table) else float("nan")

    @property
    def end(self) -> float:
        return float(self.table.t[-1]) if len(self.table) else float("nan")

    def has(self, channel: str) -> bool:
        """Column present in this era AND at least one finite sample."""
        s = self._series.get(channel) or self._text.get(channel)
        return s is not None and len(s.t) > 0

    def coverage(self, channel: str) -> float:
        """Fraction of rows with a usable value (0 if the column is absent)."""
        if channel in self.table.numeric:
            return float(np.isfinite(self.table.numeric[channel]).mean()) if len(self.table) else 0.0
        if channel in self.table.text:
            return float(np.mean([bool(s) for s in self.table.text[channel]])) if len(self.table) else 0.0
        return 0.0

    # --------------------------------------------------------------- lookup
    def value_at(self, channel: str, t: float) -> float:
        s = self._series.get(channel)
        if s is None or not len(s.t):
            return float("nan")
        i = int(np.searchsorted(s.t, t, side="right")) - 1
        if i < 0 or t - s.t[i] > self.max_hold_s:
            return float("nan")
        return float(s.v[i])

    def values_at(self, channel: str, ts: np.ndarray) -> np.ndarray:
        ts = np.asarray(ts, float)
        s = self._series.get(channel)
        if s is None or not len(s.t):
            return np.full(ts.shape, np.nan)
        i = np.searchsorted(s.t, ts, side="right") - 1
        out = np.full(ts.shape, np.nan)
        ok = i >= 0
        held = np.zeros(ts.shape, dtype=bool)
        held[ok] = (ts[ok] - s.t[i[ok]]) <= self.max_hold_s
        out[held] = s.v[i[held]]
        return out

    def text_at(self, channel: str, t: float) -> str:
        s = self._text.get(channel)
        if s is None or not len(s.t):
            return ""
        i = int(np.searchsorted(s.t, t, side="right")) - 1
        if i < 0 or t - s.t[i] > self.max_hold_s:
            return ""
        return str(s.v[i])

    def samples(self, channel: str, t0: float = -np.inf, t1: float = np.inf):
        """(t, v) finite samples in [t0, t1] -- for validators and plots."""
        s = self._series.get(channel)
        if s is None:
            return np.array([]), np.array([])
        keep = (s.t >= t0) & (s.t <= t1)
        return s.t[keep], s.v[keep]

    # ----------------------------------------------------------------- gaps
    def gaps(self, min_gap_s: float = 120.0) -> list[tuple[float, float]]:
        """Stretches with no rows at all longer than ``min_gap_s``."""
        t = self.table.t
        if len(t) < 2:
            return []
        dt = np.diff(t)
        idx = np.nonzero(dt > min_gap_s)[0]
        return [(float(t[i]), float(t[i + 1])) for i in idx]
