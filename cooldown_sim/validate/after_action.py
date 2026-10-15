"""After-action report: what happened during a session, in order.

``after_action.md`` merges, on the sim-time axis:

* every operator/autopilot/scenario action, fault, note, mark and clock
  change (events.csv);
* every transition of the state PVs an operator watches -- safety permits
  and latch, target status, alarm summary, microwave RF state, ssRF
  execution state, fitter calibration (probe.csv);

and summarizes the NMR record (truth vs fitter).  ``after_action.html``
plots true P against the fitter's P when the NMR leg ran.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pandas as pd

from .. import SIMULATION_BANNER
from ..clock import wall_to_sim
from ..data.records import format_epoch, lab_tz
from . import loaders

WATCH = {
    "UNH:GEN:CRYO:SAFE:Safe_State": "safety state",
    "UNH:GEN:CRYO:SAFE:Safe_Fault": "safety latch",
    "UNH:GEN:CRYO:SAFE:Vac_Permit": "Vac_Permit",
    "UNH:GEN:CRYO:SAFE:CB_Permit": "CB_Permit",
    "UNH:GEN:CRYO:SAFE:Data_Permit": "Data_Permit",
    "UNH:GEN:CRYO:SAFE:Safe_Reason": "safety reason",
    "UNH:GEN:CRYO:STATE:production": "target production",
    "UNH:GEN:CRYO:ALARM:Alarm_Count": "alarm count",
    "UNH:GEN:MW:SUP:RFState": "microwave RF state (0 unk/1 off/2 on)",
    "UNH:GEN:MW:SUP:InterlockMessage": "microwave interlock",
    "UNH:GEN:SSRF:SEQ:EXECUTION_STATE": "ssRF execution",
    "UNH:GEN:SSRF:FIT:CALIBRATED": "fitter calibrated",
}
SKIP_EVENT_KINDS = {"autopilot"}       # individual caputs are summarized, not listed


def write(run: Path) -> tuple[Path, Path | None]:
    run = Path(run)
    cps = loaders.checkpoints(run)
    rows = []
    ev = loaders.events(run)
    autopilot_count = 0
    for _, e in ev.iterrows():
        if e["kind"] in SKIP_EVENT_KINDS:
            autopilot_count += 1
            continue
        rows.append((float(e["sim"]), e["kind"], str(e["detail"])))
    probe = loaders.probe(run)
    probe = probe[probe["connected"] == 1]
    for pv, label in WATCH.items():
        s = probe[probe["pv"] == pv]
        last = None
        for w, v in zip(s["wall"], s["value"]):
            if v != last:
                rows.append((wall_to_sim(cps, w), "state", f"{label}: {v}"))
                last = v
    rows.sort(key=lambda r: r[0])

    lines = [f"# After-action report", "", f"> {SIMULATION_BANNER}", "",
             f"* run: `{run.name}`",
             f"* sim span: {format_epoch(cps[0].sim)} -> {format_epoch(max(c.sim for c in cps))}",
             f"* autopilot command writes: {autopilot_count} (see events.csv)", ""]
    nmr_path = run / "nmr.csv"
    html = None
    if nmr_path.exists():
        nmr = pd.read_csv(nmr_path)
        nmr["P_fit"] = pd.to_numeric(nmr["P_fit"], errors="coerce")
        if len(nmr):
            last = nmr.iloc[-1]
            lines += ["## NMR", "",
                      f"* sweeps: {len(nmr)}; first truth P {nmr['true_P'].iloc[0]:.2f} %, "
                      f"last truth P {last['true_P']:.2f} %, last fitter P "
                      f"{'n/a' if pd.isna(last['P_fit']) else f'{last.P_fit:.2f} %'}", ""]
            html = _plot(run, nmr)
    lines += ["## Timeline (sim time, lab-local)", "", "| sim time | kind | what |", "|---|---|---|"]
    for t, kind, what in rows:
        lines.append(f"| {format_epoch(t)} | {kind} | {what.replace('|', '/')} |")
    md = run / "after_action.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md, html


def _plot(run: Path, nmr: pd.DataFrame) -> Path | None:
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    tz = lab_tz()
    x = [_dt.datetime.fromtimestamp(t, tz) for t in nmr["sim"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=nmr["true_P"], name="target model truth P (%)", mode="lines"))
    fig.add_trace(go.Scatter(x=x, y=nmr["P_fit"], name="online fitter P_fit (%)", mode="markers",
                             marker={"size": 4}))
    fig.update_layout(title=f"{SIMULATION_BANNER} -- deuteron P: truth vs fitter",
                      xaxis_title="sim time (lab-local)", yaxis_title="P (%)", template="plotly_white")
    out = run / "after_action.html"
    fig.write_html(out, include_plotlyjs=True, full_html=True)
    return out
