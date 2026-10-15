"""Hindcast: run the target model through a past cool-down and score it.

No IOCs, no clock: the model is stepped straight through the record with the
history inputs (microwaves from the record and the logbook UCA timeline, field
and temperatures from the record), the way a replay with the autopilot on
drives it.  Its polarization is compared with the record's online P (the
LabVIEW area method).

Two views:

* **free run** -- start from the record's P at the first valid sample and
  never look at the record's P again.  Every unmodelled event (TE, anneal,
  AFP, material change, burns not in the blast log) is carried forward, so
  this measures how long the model stays on track;
* **anchored** -- re-initialise from the record's P every ``anchor_h`` hours
  and score each stretch.  This measures the dynamics (spin-up rate, plateau,
  relaxation) independently of how errors accumulate.

Scores are in percentage points of P over samples where the record's P is
valid (|P| < 100 %) and the field is on (B > 0.5 T).

Units: s, %, T.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..data import records
from ..data.timeline import Timeline
from ..autopilot.ssrf import apply_burn
from ..data.blast_log import read_cooldown_burns
from .target import TargetModel, load_params

STEP_S = 60.0


@dataclass
class Series:
    t: np.ndarray                  # epoch s
    p_model: np.ndarray            # %
    p_record: np.ndarray           # %, NaN where invalid
    drive: np.ndarray              # history drive used by the model
    field_T: np.ndarray


@dataclass
class Score:
    cooldown: str
    view: str                      # "free" | "anchored Nh"
    n: int
    mae: float                     # pp
    median_abs: float              # pp
    rms: float                     # pp
    bias: float                    # model - record, pp
    p_record_max: float            # %
    p_model_max: float             # %
    stretches: list = field(default_factory=list)   # anchored: (start, mae) per stretch

    def line(self) -> str:
        return (f"{self.cooldown:24s} {self.view:12s} n={self.n:6d}  MAE {self.mae:6.2f}  "
                f"median {self.median_abs:6.2f}  RMS {self.rms:6.2f}  bias {self.bias:+6.2f}  "
                f"max P rec {self.p_record_max:6.1f} / model {self.p_model_max:6.1f}")


def load(cooldown_dir: Path) -> Timeline:
    return Timeline(records.read_cooldown(cooldown_dir))


def _valid(p: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.isfinite(p) & (np.abs(p) < 100.0) & (b > 0.5)


def despike(p: np.ndarray, half: int = 5, floor_pp: float = 3.0) -> np.ndarray:
    """NaN out isolated glitches in the record's P (Hampel filter).

    The DAQ's area P occasionally jumps to tens of percent for a few samples
    (or reads ~100 %).  A sample is dropped when it is more than
    max(4 MAD, floor_pp) from the median of its +-``half`` neighbours."""
    out = p.copy()
    n = len(p)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        w = p[lo:hi]
        w = w[np.isfinite(w)]
        if len(w) < 3 or not np.isfinite(p[i]):
            continue
        med = np.median(w)
        mad = np.median(np.abs(w - med))
        if abs(p[i] - med) > max(4.0 * 1.4826 * mad, floor_pp):
            out[i] = np.nan
    return out


def run(tl: Timeline, params: dict | None = None, *, anchor_h: float | None = None,
        step_s: float = STEP_S, cooldown: str | None = None, burns: list | None = None) -> Series:
    """Step the model through the whole record; re-anchor every ``anchor_h`` hours."""
    ts = np.arange(tl.start, tl.end, step_s)
    probe = TargetModel(tl, tl.start, params)
    pr = despike(np.array([tl.value_at("online_p", t) for t in ts]))
    b = np.array([probe.field_T(t) for t in ts])
    ok = _valid(pr, b)
    if not ok.any():
        return Series(ts, np.full_like(ts, np.nan), np.where(ok, pr, np.nan), np.zeros_like(ts), b)
    t0 = ts[np.argmax(ok)]
    model = TargetModel(tl, t0, params, cooldown)
    next_anchor = t0 + anchor_h * 3600.0 if anchor_h else math.inf
    pm, dr = np.full_like(ts, np.nan), np.zeros_like(ts)
    pending = sorted((b for b in (burns or []) if b.ok and b.end > t0), key=lambda b: b.end)
    for i, t in enumerate(ts):
        if t < t0:
            continue
        while pending and pending[0].end <= t:          # logged ssRF burns, as the ssRF leg applies them
            b = pending.pop(0)
            model.advance(b.end)
            apply_burn(model, b.start, b.end, b.sweep_max_dbm, b.centre_mhz)
        if t >= next_anchor:
            if ok[i]:
                model.reset(t)
                next_anchor = t + anchor_h * 3600.0
        s = model.advance(t)
        pm[i] = 100.0 * s.P
        dr[i] = model.hist_mw(t)[0]
    return Series(ts, pm, np.where(ok, pr, np.nan), dr, b)


def score(name: str, view: str, s: Series, anchor_h: float | None = None) -> Score:
    m = np.isfinite(s.p_record) & np.isfinite(s.p_model)
    err = s.p_model[m] - s.p_record[m]
    if not len(err):
        return Score(name, view, 0, *(float("nan"),) * 6)
    stretches = []
    if anchor_h:
        edges = np.arange(s.t[0], s.t[-1] + anchor_h * 3600.0, anchor_h * 3600.0)
        for a, b in zip(edges[:-1], edges[1:]):
            k = m & (s.t >= a) & (s.t < b)
            if k.sum() >= 10:
                stretches.append((float(a), float(np.mean(np.abs(s.p_model[k] - s.p_record[k])))))
    return Score(name, view, int(m.sum()), float(np.mean(np.abs(err))), float(np.median(np.abs(err))),
                 float(np.sqrt(np.mean(err ** 2))), float(np.mean(err)),
                 float(np.nanmax(np.abs(s.p_record))), float(np.nanmax(np.abs(s.p_model[m]))), stretches)


def evaluate(cooldown_dir: Path, params: dict | None = None, anchor_h: float = 6.0,
             tl: Timeline | None = None) -> tuple[list[Score], dict]:
    """Free-run and anchored scores for one cool-down, plus the series.

    ``params`` None: the defaults plus this cool-down's fitted DNP values."""
    tl = tl or load(cooldown_dir)
    name = cooldown_dir.name
    params = params if params is not None else load_params(cooldown=name)
    burns = read_cooldown_burns(cooldown_dir)
    free = run(tl, params, cooldown=name, burns=burns)
    anch = run(tl, params, anchor_h=anchor_h, cooldown=name, burns=burns)
    return ([score(name, "free", free), score(name, f"anchored {anchor_h:g}h", anch, anchor_h)],
            {"free": free, "anchored": anch})


# ------------------------------------------------------------- DNP fit
#: (key, lower, upper) bounds of the fitted DNP values
FIT_BOUNDS = (("P_max", 0.01, 0.80), ("tau_up_s", 200.0, 20000.0), ("T1_P_s", 1800.0, 60000.0),
              ("optimum_offset_GHz", -1.0, 1.0), ("lobe_width_GHz", 0.05, 0.6), ("neg_scale", 0.0, 1.5))
_LOG_KEYS = {"tau_up_s", "T1_P_s", "lobe_width_GHz"}


def fit_cost(tl: Timeline, params: dict, step_s: float = 180.0) -> float:
    """Mean of the free-run and 6 h-anchored mean absolute errors (pp)."""
    out = []
    for anchor in (None, 6.0):
        s = run(tl, params, anchor_h=anchor, step_s=step_s)
        m = np.isfinite(s.p_record) & np.isfinite(s.p_model)
        out.append(float(np.mean(np.abs(s.p_model[m] - s.p_record[m]))) if m.any() else math.nan)
    return float(np.mean(out))


def fit_dnp(tl: Timeline, base: dict | None = None, *, max_evals: int = 120,
            sides: tuple = (1, -1), step_s: float = 180.0) -> dict:
    """Fit this cool-down's DNP values (Nelder-Mead, bounded by clipping).

    Returns {values..., "neg_side", "cost_before", "cost_after", "evals"}."""
    import copy

    from scipy.optimize import minimize

    base = base or load_params()
    d0 = base["dnp"]

    def to_x(d):
        return np.array([math.log(d[k]) if k in _LOG_KEYS else float(d.get(k, 1.0))
                         for k, _lo, _hi in FIT_BOUNDS])

    def to_values(x):
        out = {}
        for (k, lo, hi), v in zip(FIT_BOUNDS, x):
            v = math.exp(v) if k in _LOG_KEYS else float(v)
            out[k] = float(min(max(v, lo), hi))
        return out

    def params_for(values, side):
        p = copy.deepcopy(base)
        p["dnp"].update(values)
        p["dnp"]["neg_side"] = side
        return p

    before = fit_cost(tl, base, step_s)
    best = None
    x0 = to_x({**d0, "neg_scale": d0.get("neg_scale", 1.0)})
    steps = np.array([-0.15, 0.6, 0.6, 0.2, 0.5, -0.5])
    for side in sides:
        simplex = [x0] + [x0 + np.eye(len(x0))[i] * steps[i] for i in range(len(x0))]
        r = minimize(lambda x: fit_cost(tl, params_for(to_values(x), side), step_s), x0,
                     method="Nelder-Mead",
                     options={"maxfev": max_evals, "xatol": 1e-3, "fatol": 0.02,
                              "initial_simplex": simplex})
        if best is None or r.fun < best[0]:
            best = (float(r.fun), to_values(r.x), side, int(r.nfev))
    cost, values, side, evals = best
    return {**values, "neg_side": side, "cost_before": before, "cost_after": cost, "evals": evals}


# ------------------------------------------------------------- report
def write_html(out: Path, results: list) -> Path:
    """One page: a score table, then per cool-down the record against the model.

    ``results``: [(name, [Score...], series_dict, default_scores or None)]."""
    import datetime as _dt

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    tz = records.lab_tz()
    rows = []
    for name, scores, _series, default in results:
        free = next(s for s in scores if s.view == "free")
        anch = next(s for s in scores if s.view != "free")
        d = next((s for s in (default or []) if s.view == "free"), None)
        default_mae = f"{d.mae:.2f}" if d else "-"
        rows.append(f"<tr><td>{name}</td><td>{free.n}</td><td>{default_mae}</td>"
                    f"<td><b>{free.mae:.2f}</b></td><td>{free.median_abs:.2f}</td><td>{anch.mae:.2f}</td>"
                    f"<td>{free.bias:+.2f}</td><td>{free.p_record_max:.1f}</td><td>{free.p_model_max:.1f}</td></tr>")
    parts = ["<html><head><meta charset='utf-8'><title>Hindcast</title>"
             "<style>body{font-family:sans-serif;margin:16px} table{border-collapse:collapse}"
             "td,th{border:1px solid #bbb;padding:3px 8px;text-align:right} td:first-child{text-align:left}"
             "</style></head><body>",
             "<h2>SIMULATION hindcast: target model vs recorded P</h2>",
             "<p>Model stepped through each past cool-down with the history inputs (no IOCs). "
             "Errors in percentage points of P over samples with valid recorded P and field on. "
             "<i>Free</i>: started from the record once; <i>anchored</i>: reset to the record every 6 h.</p>",
             "<table><tr><th>cool-down</th><th>n</th><th>free MAE, default values</th>"
             "<th>free MAE</th><th>free median</th><th>anchored MAE</th><th>bias</th>"
             "<th>max P record</th><th>max P model</th></tr>", *rows, "</table>"]
    first = True
    for name, _scores, series, _default in results:
        free, anch = series["free"], series["anchored"]
        x = [_dt.datetime.fromtimestamp(t, tz) for t in free.t]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25],
                            vertical_spacing=0.04)
        fig.add_trace(go.Scatter(x=x, y=free.p_record, mode="markers", marker={"size": 2, "color": "black"},
                                 name="record P"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=free.p_model, mode="lines", line={"color": "red"},
                                 name="model, free run"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=anch.p_model, mode="lines", line={"color": "royalblue", "width": 1},
                                 name="model, anchored 6 h"), 1, 1)
        fig.add_trace(go.Scatter(x=x, y=free.drive, mode="lines", line={"color": "green"},
                                 name="microwave drive (history)"), 2, 1)
        fig.update_layout(title=name, height=520, template="plotly_white",
                          yaxis_title="P (%)", yaxis2_title="drive")
        parts.append(fig.to_html(full_html=False, include_plotlyjs="cdn" if first else False))
        first = False
    parts.append("</body></html>")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
