"""Run every validator over a finished run directory and write the report."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from . import channel_map as cmap_mod
from .profile import GeneratedStack, LaunchSpec, Rewrite
from .pvlist import archiver_explicit_pvs
from .validate import Check, logic, mapcheck, report, roundtrip


def save_stack(stack: GeneratedStack) -> Path:
    path = stack.run_dir / "state" / "stack.json"
    path.write_text(json.dumps({"iocs": [asdict(s) for s in stack.iocs],
                                "overrides": [asdict(r) for r in stack.overrides]}, indent=2),
                    encoding="utf-8")
    return path


def load_stack(run: Path) -> GeneratedStack:
    data = json.loads((Path(run) / "state" / "stack.json").read_text(encoding="utf-8"))
    stack = GeneratedStack(Path(run))
    stack.iocs = [LaunchSpec(**s) for s in data["iocs"]]
    stack.overrides = [Rewrite(**r) for r in data.get("overrides", [])]
    return stack


def run_all(run: Path, stack: GeneratedStack, *, runtime: dict, timeline=None,
            t0: float | None = None, t1: float | None = None, meta: dict | None = None):
    """``runtime``: facts only the live run knows (deaths, skipped legs,
    read-only violations, clamp counts, never-connected PVs)."""
    run = Path(run)
    cmap = cmap_mod.load()
    legs = {s.leg for s in stack.iocs}
    checks: list[Check] = []
    checks.append(logic.processes(runtime.get("deaths", [])))
    checks += logic.heartbeats(stack, run, runtime.get("skipped", {}))
    checks.append(logic.read_only(runtime.get("read_only_violations", {})))
    checks += logic.never_connected(runtime.get("never_connected", []))
    checks.append(logic.clamps(runtime.get("clamps", {})))
    for name, reason in runtime.get("skipped_legs", {}).items():
        checks.append(Check(f"leg {name}", "SKIPPED", reason))
    checks += roundtrip.device_channels(stack, run)
    if "cryo" in legs:
        checks += logic.cryo_safety(run)
        checks += roundtrip.archive(run, archiver_explicit_pvs())
    md, js, verdict = report.write(run, checks, meta or {})
    html = None
    if timeline is not None and t0 is not None:
        html = mapcheck.write(run, stack, cmap, timeline, t0, t1)
    return verdict, md, html, checks
