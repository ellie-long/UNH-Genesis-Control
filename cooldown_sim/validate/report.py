"""report.md / report.json for a run."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .. import SIMULATION_BANNER
from . import Check

ORDER = {"FAIL": 0, "KNOWN": 1, "SKIPPED": 2, "PASS": 3, "INFO": 4}


def overall(checks: list[Check]) -> str:
    return "FAIL" if any(c.verdict == "FAIL" for c in checks) else "PASS"


def write(run: Path, checks: list[Check], meta: dict) -> tuple[Path, Path, str]:
    run = Path(run)
    verdict = overall(checks)
    counts = Counter(c.verdict for c in checks)
    (run / "report.json").write_text(json.dumps({
        "verdict": verdict, "counts": dict(counts), "meta": meta,
        "checks": [asdict(c) for c in checks]}, indent=2, default=str), encoding="utf-8")

    lines = [f"# Simulated cool-down report: **{verdict}**", "",
             f"> {SIMULATION_BANNER}", ""]
    for key in ("mode", "scenario", "source", "window", "speed", "legs", "platform", "git"):
        if key in meta:
            lines.append(f"* **{key}**: {meta[key]}")
    lines += ["", "| Verdict | Count |", "|---|---|"]
    lines += [f"| {v} | {counts.get(v, 0)} |" for v in ("PASS", "FAIL", "KNOWN", "SKIPPED", "INFO")]
    lines += ["", "## Checks", ""]
    grouped = sorted(checks, key=lambda c: (ORDER[c.verdict], c.name))
    # collapse the many per-PV roundtrip/archive PASS lines into one summary row each
    passes = [c for c in grouped if c.verdict == "PASS" and c.name.startswith(("roundtrip ", "archive "))]
    for c in grouped:
        if c in passes:
            continue
        lines.append(f"### {c.verdict}: {c.name}")
        lines.append("")
        lines.append(c.summary)
        for d in c.details[:20]:
            lines.append(f"* `{d}`")
        lines.append("")
    if passes:
        rt = [c for c in passes if c.name.startswith("roundtrip ")]
        ar = [c for c in passes if c.name.startswith("archive ")]
        lines += ["### PASS: per-PV round trips", "",
                  f"{len(rt)} device-channel PVs matched the emulator to wire precision; "
                  f"{len(ar)} archived PVs matched their PV streams.", ""]
        for c in rt:
            lines.append(f"* `{c.name[10:]}` -- {c.summary}")
        lines.append("")
    md = run / "report.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md, run / "report.json", verdict
