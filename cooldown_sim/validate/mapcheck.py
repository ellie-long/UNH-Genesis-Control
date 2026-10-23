"""``channel_map_check.html``: recorded column vs what Genesis published.

One panel per mapped instrument channel, on the sim-time axis: the past
record (through the channel map) as markers, the Genesis PV (mapped from wall
to sim time with the clock checkpoints, converted back to recorded units) as
a line.  A wrong column mapping or unit shows up as a visible mismatch; a
right one overlays exactly.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import yaml

from .. import SIMULATION_BANNER, conversions
from ..channel_map import ChannelMap
from ..clock import wall_to_sim
from ..data.records import lab_tz
from ..data.timeline import Timeline
from ..emulators.cryo import TPG262
from ..profile import GeneratedStack
from ..pvlist import device_channel_pvs
from . import loaders

#: kind -> factor converting the PV value back to recorded units
BACK = {"tpg262": 1.0 / TPG262.UNIT_FROM_TORR[TPG262.DEFAULT_UNIT]}   # Torr on the wire: 1


def write(run: Path, stack: GeneratedStack, cmap: ChannelMap, timeline: Timeline,
          t0: float, t1: float) -> Path | None:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        return None
    probe = loaders.probe(run)
    probe = probe[probe["connected"] == 1]
    cps = loaders.checkpoints(run)
    tz = lab_tz()

    panels = []
    for spec in stack.iocs:
        kind = (spec.emulator or {}).get("kind")
        if not kind or spec.name not in cmap.instruments:
            continue
        with open(Path(spec.folder) / "settings.yaml", encoding="utf-8") as fh:
            settings = yaml.safe_load(fh)[spec.ioc]
        for pv, (inst, ch) in device_channel_pvs(spec, settings).items():
            panels.append((pv, inst, ch, kind))
    if not panels:
        return None

    fig = make_subplots(rows=len(panels), cols=1, shared_xaxes=False,
                        subplot_titles=[f"{inst}.{ch}  ({pv})  [{cmap.channel(inst, ch).confidence}]"
                                        for pv, inst, ch, _k in panels],
                        vertical_spacing=min(0.02, 0.6 / max(len(panels), 1)))
    for row, (pv, inst, ch, kind) in enumerate(panels, start=1):
        spec = cmap.channel(inst, ch)
        ts = np.unique(np.concatenate([timeline.samples(c, t0, t1)[0] for c in spec.source_columns
                                       if c in timeline.table.numeric] or [np.array([])]))
        rec = spec.recorded_many(timeline, ts) if len(ts) else np.array([])
        fig.add_trace(go.Scatter(x=[_dt.datetime.fromtimestamp(t, tz) for t in ts], y=rec,
                                 mode="markers", marker={"size": 4}, name=f"record {ch}",
                                 showlegend=row == 1, legendgroup="record"), row=row, col=1)
        s = probe[probe["pv"] == pv]
        sim = [wall_to_sim(cps, w) for w in s["wall"]]
        y = s["num"].to_numpy() * BACK.get(kind, 1.0)
        fig.add_trace(go.Scatter(x=[_dt.datetime.fromtimestamp(t, tz) for t in sim], y=y,
                                 mode="lines", name=f"Genesis {ch}", showlegend=row == 1,
                                 legendgroup="genesis"), row=row, col=1)
        fig.update_yaxes(title_text=spec.units, row=row, col=1)
    fig.update_layout(height=260 * len(panels), title=f"{SIMULATION_BANNER} -- channel map cross-check",
                      template="plotly_white")
    out = Path(run) / "channel_map_check.html"
    fig.write_html(out, include_plotlyjs=True, full_html=True)
    return out
