"""Process, liveness, read-only and logic-IOC checks for a run."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..profile import GeneratedStack
from . import Check, loaders

KNOWN_FILE = Path(__file__).resolve().parents[1] / "known_issues.yaml"


def known_issues() -> dict:
    with open(KNOWN_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def processes(deaths: list[str]) -> Check:
    if deaths:
        return Check("processes", "FAIL", f"{len(deaths)} process(es) died during the run", deaths)
    return Check("processes", "PASS", "every launched process stayed up for the whole run")


def heartbeats(stack: GeneratedStack, run: Path, skipped: dict[str, str],
               absent: dict[str, str] | None = None) -> list[Check]:
    """``absent``: instruments not present in this era's record (their
    emulator refuses connections): the IOC must say so (COMM 0), not read."""
    probe = loaders.probe(run)
    absent = absent or {}
    out = []
    for spec in stack.iocs:
        if spec.name in skipped:
            out.append(Check(f"heartbeat {spec.name}", "SKIPPED", skipped[spec.name]))
            continue
        if spec.name in absent:
            comm = probe[(probe["pv"] == f"{spec.prefix}:COMM") & (probe["connected"] == 1)]["num"]
            if len(comm) and not (comm == 1).any():
                out.append(Check(f"heartbeat {spec.name}", "PASS",
                                 f"{absent[spec.name]}; the IOC reports COMM 0 as it should"))
            else:
                out.append(Check(f"heartbeat {spec.name}", "FAIL",
                                 f"{absent[spec.name]}, but COMM "
                                 f"{'was never seen' if not len(comm) else 'read 1'}"))
            continue
        pv = f"{spec.prefix}:MAN:{spec.ioc}_time"
        values = probe[(probe["pv"] == pv) & (probe["connected"] == 1)]["num"].dropna().unique()
        if len(values) >= 2:
            out.append(Check(f"heartbeat {spec.name}", "PASS", f"{len(values)} heartbeat updates"))
        elif spec.leg == "cryo" and spec.ioc in ("alarm_summary", "safety_interlock", "target_status",
                                                 "save_restore", "archiver") and len(values) == 1:
            out.append(Check(f"heartbeat {spec.name}", "PASS", "logic IOC heartbeat present"))
        else:
            out.append(Check(f"heartbeat {spec.name}", "FAIL",
                             f"{len(values)} heartbeat update(s): device loop not reading"))
    return out


def read_only(violations: dict[str, list[str]]) -> Check:
    if violations:
        return Check("read-only proof", "FAIL",
                     "writes reached instruments behind write_enabled: false IOCs",
                     [f"{k}: {v[:5]}" for k, v in violations.items()])
    return Check("read-only proof", "PASS",
                 "no output-changing command reached any instrument whose IOC is read-only")


def never_connected(never: list[str]) -> list[Check]:
    issues = known_issues()
    conn = issues.get("conn-pv-not-served", {})
    known = [pv for pv in never if pv.endswith("_Conn")]
    unknown = [pv for pv in never if pv not in known]
    out = []
    if known:
        out.append(Check("expected PVs never served (_Conn)", "KNOWN",
                         f"{len(known)} _Conn PVs listed by the live archiver/pv_map are not "
                         "served by any IOC (known issue conn-pv-not-served)",
                         known, {"issue": "conn-pv-not-served", "summary": conn.get("summary", "")}))
    if unknown:
        out.append(Check("expected PVs never served", "FAIL",
                         f"{len(unknown)} PVs the live configuration expects never connected",
                         unknown))
    if not never:
        out.append(Check("expected PVs served", "PASS", "every watched PV connected"))
    return out


def cryo_safety(run: Path) -> list[Check]:
    probe = loaders.probe(run)
    out = []
    p = probe[probe["connected"] == 1]
    data_permit = p[p["pv"] == "UNH:GEN:CRYO:SAFE:Data_Permit"]
    if not data_permit.empty:
        # ignore the start-up period: every permit is withheld until first read
        warm = data_permit.iloc[2:] if len(data_permit) > 2 else data_permit.iloc[0:0]
        stale = warm[warm["num"] == 0]
        if len(stale):
            reasons = sorted(set(p[p["pv"] == "UNH:GEN:CRYO:SAFE:Safe_Reason"]["value"]) - {""})
            out.append(Check("cryo Data_Permit", "KNOWN",
                             "Data_Permit dropped to Stale after start-up "
                             "(known issue stale-watchdog-on-constant-reading)",
                             reasons[:10], {"issue": "stale-watchdog-on-constant-reading"}))
        else:
            out.append(Check("cryo Data_Permit", "PASS", "readings behind the permits stayed fresh"))
    trips = p[p["pv"] == "UNH:GEN:CRYO:SAFE:Safe_Trips"]["num"].dropna()
    n_trips = int(trips.iloc[-1]) if len(trips) else 0
    reasons = list(dict.fromkeys(v for v in p[p["pv"] == "UNH:GEN:CRYO:SAFE:Safe_Reason"]["value"]
                                 if v not in ("", "starting")))
    limits = p[p["pv"] == "UNH:GEN:CRYO:SAFE:Safe_Limits"]["value"]
    which = limits.iloc[-1] if len(limits) else "?"
    out.append(Check("cryo safety trips", "INFO",
                     f"safety interlock tripped {n_trips} time(s) (Safe_Trips); limits: {which}",
                     reasons[:3] + ([f"... {len(reasons) - 3} more reason updates"] if len(reasons) > 3 else [])))
    for pv in ("UNH:GEN:CRYO:ALARM:Alarm_Worst", "UNH:GEN:CRYO:ALARM:Alarm_Missing",
               "UNH:GEN:CRYO:STATE:production", "UNH:GEN:CRYO:STATE:unmet",
               "UNH:GEN:CRYO:SAFE:Safe_State"):
        s = p[p["pv"] == pv]
        if not s.empty:
            out.append(Check(f"final {pv.split(':', 3)[-1]}", "INFO", f"last value {s['value'].iloc[-1]!r}"))
    return out


def clamps(counts: dict[str, dict[str, int]]) -> Check:
    if not counts:
        return Check("thermometry clamps", "PASS",
                     "every recorded temperature was representable on its sensor's calibration")
    details = [f"{inst}.{ch}: {n} reading(s)" for inst, chans in counts.items() for ch, n in chans.items()]
    return Check("thermometry clamps", "INFO",
                 "some recorded temperatures exceed their sensor curve's maximum "
                 "(warm-room readings); served at the curve maximum", details)
