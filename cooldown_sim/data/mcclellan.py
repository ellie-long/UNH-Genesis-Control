"""Polarization from the McClellan line fits (the Optimizied-McClellan-Fits repo).

The record's online P is the LabVIEW DAQ's live number; in some cool-downs it
was poorly calibrated (2024-09 and 2024-11 read ~32 points off the equilibrium
fits, cool-downs technote).  The McClellan fits re-analyse every NMR line and
are validated against the dissertation, so they are the better "truth" for
scoring the target model.

Read from ``<results>/<cooldown>/main/main_results.csv``: only lines the fit
classified ``equilibrium`` (hole-burnt and unclassified lines are skipped),
P = ``P_area`` (the calibrated area polarization), else ``P_fit``; percent.
Timestamps are lab-local, like the record.

``results`` defaults to ``$MCCLELLAN_RESULTS``, else ``../results`` beside the
cool-down records folder (``$GENESIS_REPLAY_DATA``).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from .records import parse_timestamp

ENV = "MCCLELLAN_RESULTS"
DATA_ENV = "GENESIS_REPLAY_DATA"


def results_root(data_root: str | Path | None = None) -> Path | None:
    env = os.environ.get(ENV)
    if env:
        return Path(env)
    data_root = data_root or os.environ.get(DATA_ENV)
    if not data_root:
        return None
    cand = Path(data_root).resolve().parent / "results"
    return cand if cand.is_dir() else None


def results_file(cooldown: str, root: Path | None = None) -> Path | None:
    root = root or results_root()
    if root is None:
        return None
    name = cooldown if cooldown.endswith("-Cooldown") else f"{cooldown.split('-Cooldown')[0]}-Cooldown"
    path = Path(root) / name / "main" / "main_results.csv"
    return path if path.exists() else None


def equilibrium_p(cooldown: str, root: Path | None = None) -> tuple[np.ndarray, np.ndarray] | None:
    """(t epoch s, P %) of the equilibrium-classified lines, time-ordered; None if unavailable."""
    path = results_file(cooldown, root)
    if path is None:
        return None
    d = pd.read_csv(path, usecols=lambda c: c in {"timestamp", "fit_type", "P_area", "P_fit"})
    d = d[d["fit_type"] == "equilibrium"]
    p = pd.to_numeric(d.get("P_area"), errors="coerce")
    if "P_fit" in d:
        p = p.fillna(pd.to_numeric(d["P_fit"], errors="coerce"))
    t = np.array([parse_timestamp(s) for s in d["timestamp"]], dtype=float)
    p = p.to_numpy(dtype=float)
    ok = np.isfinite(t) & np.isfinite(p) & (np.abs(p) < 100.0)
    order = np.argsort(t[ok])
    return t[ok][order], p[ok][order]


def on_grid(t_lines: np.ndarray, p_lines: np.ndarray, grid: np.ndarray, half_width_s: float) -> np.ndarray:
    """Median line P within +-half_width of each grid time (NaN where no line)."""
    out = np.full(len(grid), np.nan)
    lo = np.searchsorted(t_lines, grid - half_width_s, side="left")
    hi = np.searchsorted(t_lines, grid + half_width_s, side="right")
    for i, (a, b) in enumerate(zip(lo, hi)):
        if b > a:
            out[i] = float(np.median(p_lines[a:b]))
    return out
