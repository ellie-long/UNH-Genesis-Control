"""Microwave leg checks: did the real RF chain do what the operator (or autopilot) asked?"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import Check, loaders


def checks(run: Path, runtime: dict) -> list[Check]:
    accepted, refused = runtime.get("mw_rf_on_accepted"), runtime.get("mw_rf_on_refused")
    if accepted is None:
        return []
    out = []
    ev = loaders.events(run)
    observed = ev[ev["kind"] == "microwave"]
    on_events = observed[observed["detail"].astype(str).str.startswith("RF ON")]
    if refused:
        details = list(ev[ev["kind"] == "autopilot-refused"]["detail"].astype(str))[:10]
        out.append(Check("microwave RF ON requests", "FAIL",
                         f"{refused} RF ON request(s) were not confirmed by the real RF chain "
                         f"({accepted} confirmed)", details))
    elif accepted:
        out.append(Check("microwave RF ON requests", "PASS",
                         f"{accepted} RF ON request(s) passed the supervisor interlocks and were "
                         "confirmed by the source read-back"))
    else:
        out.append(Check("microwave RF ON requests", "INFO",
                         "no RF ON was requested in this window"))
    probe = loaders.probe(run)
    state = probe[(probe["pv"] == "UNH:GEN:MW:SUP:RFState") & (probe["connected"] == 1)]
    out.append(Check("microwave RF transitions", "INFO",
                     f"{len(on_events)} RF ON observation(s); supervisor RFState took values "
                     f"{sorted(set(state['value']))} (0 UNKNOWN, 1 OFF, 2 ON)",
                     list(observed["sim_local"].astype(str) + " " + observed["detail"].astype(str))[:20]))
    return out
