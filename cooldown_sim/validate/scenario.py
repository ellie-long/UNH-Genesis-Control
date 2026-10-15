"""Evaluate a scenario's expectations against the run's records."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..clock import wall_to_sim
from ..scenario import OPS
from . import Check, loaders


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _evaluate(samples: list, op: str, value, mode: str) -> tuple[bool, str]:
    fn = OPS[op]
    hits = [s for s in samples if _safe(fn, s, value)]
    if mode == "always":
        ok = bool(samples) and len(hits) == len(samples)
    else:
        ok = bool(hits)
    seen = sorted({str(s) for s in samples})[:6]
    return ok, f"{len(hits)}/{len(samples)} samples satisfy {op} {value!r}; values seen {seen}"


def _safe(fn, a, b):
    try:
        return bool(fn(_num(a), _num(b)))
    except TypeError:
        return False


def checks(run: Path, spec: dict, t0: float, all_checks: list[Check],
           actions: dict | None = None) -> list[Check]:
    out = []
    if spec.get("actions"):
        if actions is None:
            out.append(Check("scenario: actions", "FAIL", "the scenario player did not run"))
        else:
            ok = actions["done"] == actions["total"] and not actions["errors"]
            out.append(Check("scenario: actions", "PASS" if ok else "FAIL",
                             f"{actions['done']}/{actions['total']} actions executed, "
                             f"{len(actions['errors'])} failed", list(actions["errors"])))
    cps = loaders.checkpoints(run)
    probe = loaders.probe(run)
    probe = probe[probe["connected"] == 1].copy()
    probe["sim"] = [wall_to_sim(cps, w) for w in probe["wall"]]
    nmr_path = Path(run) / "nmr.csv"
    nmr = pd.read_csv(nmr_path) if nmr_path.exists() else None
    if nmr is not None and "P_fit" in nmr:
        # derived truth columns: the fitter's error against the model (percentage points)
        nmr["fit_err_P"] = pd.to_numeric(nmr["P_fit"], errors="coerce") - nmr["true_P"]
        nmr["abs_fit_err_P"] = nmr["fit_err_P"].abs()
    names = {c.name: c for c in all_checks}
    for i, e in enumerate(spec.get("expect") or []):
        label = e.get("label") or f"expect {i + 1}"
        if "check" in e:
            c = names.get(e["check"])
            allowed = e["verdict"] if isinstance(e["verdict"], list) else [e["verdict"]]
            ok = c is not None and c.verdict in allowed
            got = c.verdict if c else "absent"
            out.append(Check(f"scenario: {label}", "PASS" if ok else "FAIL",
                             f"check '{e['check']}' verdict {got} (expected {'/'.join(allowed)})"))
            continue
        lo, hi = t0 + 60.0 * float(e.get("from", 0)), t0 + 60.0 * float(e.get("to", 1e9))
        mode = e.get("mode", "eventually")
        if "truth" in e:
            if nmr is None:
                out.append(Check(f"scenario: {label}", "FAIL", "no nmr.csv (needs the nmr leg)"))
                continue
            w = nmr[(nmr["sim"] >= lo) & (nmr["sim"] <= hi)]
            samples = list(pd.to_numeric(w[e["truth"]], errors="coerce").dropna())
            what = f"{e['truth']} in [{e.get('from', 0)}, {e.get('to', 'end')}] min"
        else:
            # the probe records changes only: the value held entering the window
            # counts as a sample, followed by every change inside it
            series = probe[probe["pv"] == e["pv"]]
            before = series[series["sim"] < lo].tail(1)
            w = pd.concat([before, series[(series["sim"] >= lo) & (series["sim"] <= hi)]])
            if "severity" in e:
                samples = list(pd.to_numeric(w["severity"], errors="coerce").dropna())
                e = dict(e, op=e["severity"])
            else:
                samples = list(w["value"])
            what = f"{e['pv']}{' severity' if 'severity' in e else ''} in [{e.get('from', 0)}, {e.get('to', 'end')}] min"
        ok, why = _evaluate(samples, e.get("op", "eq"), e["value"], mode)
        out.append(Check(f"scenario: {label}", "PASS" if ok else "FAIL", f"{what} ({mode}): {why}",
                         [e["note"]] if e.get("note") else []))
    return out


def apply_waivers(spec: dict, checks: list[Check]) -> None:
    """``waive: [{check: NAME, reason: ...}]`` turns that check's FAIL into INFO,
    with the reason in the report.  For general checks a scenario deliberately
    drives outside their premise (e.g. fitter accuracy through a quench); the
    scenario should then test what still holds with its own expectations."""
    for w in spec.get("waive") or []:
        for c in checks:
            if c.name == w["check"] and c.verdict == "FAIL":
                c.verdict = "INFO"
                c.summary = f"WAIVED by scenario ({w['reason']}): {c.summary}"
