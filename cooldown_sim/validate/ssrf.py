"""ssRF leg checks: what the real sequencer did with each replayed burn."""

from __future__ import annotations

from pathlib import Path

from . import Check, loaders


def checks(run: Path, runtime: dict) -> list[Check]:
    if "ssrf_burns" not in runtime:
        return []
    out = []
    if runtime.get("ssrf_skipped"):
        out.append(Check("ssrf burn replay", "SKIPPED", runtime["ssrf_skipped"]))
        return out
    burns = runtime["ssrf_burns"]
    if not burns:
        out.append(Check("ssrf burn replay", "INFO", "no logged burn fell inside this window"))
        return out
    by = {}
    for b in burns:
        by.setdefault(b.get("outcome"), []).append(b)
    details = [f"{o}: {len(v)} (e.g. {v[0].get('reason', '')})" for o, v in by.items()]
    blocked = [b for b in burns if b.get("outcome") in ("CLEANUP_FAILED", "FAILED")
               and "permit" in str(b.get("reason", "")).lower()]
    completed = [b for b in burns if b.get("outcome") == "COMPLETE"]
    other = [b for b in burns if b not in blocked and b not in completed]
    if other:
        out.append(Check("ssrf burn replay", "FAIL",
                         f"{len(other)} of {len(burns)} replayed burns failed before the interlock "
                         "permit check (staging, readiness or arm)", details))
    elif blocked:
        out.append(Check("ssrf burn replay", "KNOWN",
                         f"{len(blocked)} of {len(burns)} replayed burns were configured, loaded and "
                         "started, then stopped at the ssRF connect permit, which the current "
                         "interlock never grants (known issues ssrf-interlock-permits-withheld, "
                         "ssrf-safe-off-lockup)", details,
                         {"issue": "ssrf-interlock-permits-withheld"}))
    else:
        out.append(Check("ssrf burn replay", "PASS",
                         f"all {len(burns)} replayed burns completed", details))
    ev = loaders.events(run)
    rf_on = ev[(ev["kind"] == "ssrf") & ev["detail"].astype(str).str.startswith("SMC RF ON")]
    out.append(Check("ssrf RF observed", "INFO",
                     f"SMC100A RF was observed ON {len(rf_on)} time(s); burn heat and tensor holes "
                     "are applied to the target only while RF is really on"))
    return out
