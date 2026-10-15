"""NMR leg checks: the real online fitter against the target model's truth."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import Check

#: calibrated |P_fit - truth| median must be within this (percentage points)
CALIBRATED_TOL_PP = 1.0
#: ignore this much sim time after a baseline change (fitter settling)
SETTLE_S = 120.0


def model_vs_record(df, timeline, synthetic: bool) -> Check | None:
    """Target-model P (truth) against the record's online P at each sweep."""
    if timeline is None or "online_p" not in timeline.table.numeric:
        return None
    rec = timeline.values_at("online_p", df["sim"].to_numpy())
    d = (df["true_P"].to_numpy() - rec)
    d = d[np.isfinite(d)]
    if not len(d):
        return None
    med = float(np.median(np.abs(d)))
    if synthetic:
        return Check("model vs record P", "PASS" if med <= 1.0 else "FAIL",
                     f"synthetic record: model P tracks the designed trajectory, median "
                     f"|diff| {med:.2f} pp over {len(d)} sweeps (limit 1 pp)")
    return Check("model vs record P", "INFO",
                 f"model fidelity against the real record: median |diff| {med:.2f} pp, "
                 f"90th percentile {float(np.percentile(np.abs(d), 90)):.2f} pp over {len(d)} sweeps "
                 "(the record includes operator actions the model does not see)")


def protected_checks(df, run: Path) -> list[Check]:
    """Protected PyNMR mode: spectra arriving on the PyNMR port without an
    armed, pinned PyNMR producer must never be fitted."""
    from . import loaders
    fitted = pd.to_numeric(df["P_fit"], errors="coerce").notna().sum()
    probe = loaders.probe(run)
    p = probe[probe["connected"] == 1]

    def last(pv):
        s = p[p["pv"] == f"UNH:GEN:SSRF:FIT:{pv}"]["value"]
        return s.iloc[-1] if len(s) else "?"
    detail = [f"NMR_PROTECTED={last('NMR_PROTECTED')}", f"NMR_SOURCE_RBV={last('NMR_SOURCE_RBV')}",
              f"NMR_ROUTE_OK={last('NMR_ROUTE_OK')}", f"NMR_FIT_ELIGIBLE={last('NMR_FIT_ELIGIBLE')}",
              f"NMR_STATUS={last('NMR_STATUS')}"]
    out = []
    if fitted:
        out.append(Check("protected NMR admission", "FAIL",
                         f"{fitted} of {len(df)} unattested spectra were FITTED in protected mode",
                         detail))
    else:
        out.append(Check("protected NMR admission", "PASS",
                         f"protected mode fitted none of {len(df)} unattested spectra on the "
                         "PyNMR port (no armed, pinned PyNMR producer)", detail))
    out.append(Check("protected NMR end-to-end fitting", "KNOWN",
                     "fitting in protected mode needs the real PyNMR as a pinned producer; the "
                     "pin is hardcoded to /home/utsav paths (known issue "
                     "pynmr-producer-paths-hardcoded), so it cannot be rehearsed elsewhere",
                     data={"issue": "pynmr-producer-paths-hardcoded"}))
    return out


def checks(run: Path, runtime: dict, timeline=None, synthetic: bool = False) -> list[Check]:
    path = Path(run) / "nmr.csv"
    if not path.exists():
        return []
    out = []
    df = pd.read_csv(path, keep_default_na=True)
    sent, errors = runtime.get("nmr_sweeps", len(df)), runtime.get("nmr_errors", 0)
    if sent == 0:
        out.append(Check("nmr feeder", "FAIL", "no sweeps reached the online fitter"))
        return out
    verdict = "PASS" if errors <= max(2, 0.01 * sent) else "FAIL"
    out.append(Check("nmr feeder", verdict, f"{sent} sweeps exchanged with the online fitter, "
                                            f"{errors} exchange error(s)"))
    if runtime.get("nmr_protected"):
        return out + protected_checks(df, run)
    for col in ("P_fit", "P_area", "true_P", "Q_fit", "true_Q"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # sweeps right after a baseline change are excluded (and a baseline taken
    # with the line on-window is an operator error the fitter cannot fix)
    note_change = df["baseline_note"].ne(df["baseline_note"].shift())
    change_t = df.loc[note_change, "sim"].to_numpy()
    settled = np.ones(len(df), bool)
    for t in change_t[1:]:
        settled &= ~((df["sim"] >= t) & (df["sim"] < t + SETTLE_S)).to_numpy()
    good_base = ~df["baseline_note"].astype(str).str.contains("WITH the line", regex=False)
    pre = df[df["P_area"].isna() & settled & good_base]
    cal = df[df["P_area"].notna() & settled & good_base]
    if len(pre):
        bias = float(np.nanmedian(pre["P_fit"] - pre["true_P"]))
        out.append(Check("nmr fitter before Update Constants", "INFO",
                         f"P_fit - truth median {bias:+.2f} pp over {len(pre)} sweeps (preset "
                         "constants; the operator calibrates with Update Constants)"))
    if len(cal):
        d = (cal["P_fit"] - cal["true_P"]).to_numpy()
        da = (cal["P_area"] - cal["true_P"]).to_numpy()
        dq = (cal["Q_fit"] - cal["true_Q"]).to_numpy()
        med = float(np.nanmedian(np.abs(d)))
        verdict = "PASS" if med <= CALIBRATED_TOL_PP else "FAIL"
        out.append(Check("nmr fitter vs model truth", verdict,
                         f"after Update Constants: |P_fit - truth| median {med:.2f} pp "
                         f"(P_area {float(np.nanmedian(np.abs(da))):.2f} pp, "
                         f"Q_fit {float(np.nanmedian(np.abs(dq))):.2f} pp) over {len(cal)} sweeps; "
                         f"limit {CALIBRATED_TOL_PP} pp",
                         data={"median_abs_pp": med, "n": int(len(cal))}))
    else:
        out.append(Check("nmr fitter vs model truth", "INFO",
                         "the fitter was never calibrated in this run (Update Constants not "
                         "pressed; the fitter autopilot does this in regression runs)"))
    mvr = model_vs_record(df, timeline, synthetic)
    if mvr is not None:
        out.append(mvr)
    bad = df[~good_base]
    if len(bad):
        out.append(Check("nmr baseline taken on-window", "INFO",
                         f"{len(bad)} sweeps used a baseline that contained the line "
                         "(operator action recorded in events.csv)"))
    return out
