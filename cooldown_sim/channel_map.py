"""Load ``channel_map.yaml`` and evaluate it against a cool-down timeline.

``ChannelMap.recorded(instrument, channel, timeline, t)`` returns the value
the record says that Genesis channel had at sim time ``t`` (recorded units),
or NaN when the record has nothing usable.  Wire conversions happen in the
emulators; validity filtering happens here so every consumer (emulators,
validators, the cross-check report) agrees on what counts as a real reading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .data import aliases
from .data.timeline import Timeline

MAP_FILE = Path(__file__).with_name("channel_map.yaml")
CONFIDENCE = ("high", "medium", "guess")
EQUIPMENT_KEYS = ("genesis", "equipment", "role", "link", "control", "sim", "confidence")
ABSENT_POLICIES = ("invalid", "refuse", "output_off")
WIRE = ("none", "torr_to_mbar", "ionivac_count", "ls224_ohms")


@dataclass
class ChannelSpec:
    instrument: str
    name: str
    source: str | None
    select: dict | None
    fallback: str | None
    units: str
    valid: tuple[float, float] | None
    confidence: str
    note: str
    wire: str = "none"
    absent: str = "invalid"

    @property
    def source_columns(self) -> list[str]:
        cols = []
        if self.source:
            cols.append(self.source)
        if self.select:
            cols.extend(v for k, v in self.select.items() if k != "by")
            cols.append(self.select["by"])
        if self.fallback:
            cols.append(self.fallback)
        return list(dict.fromkeys(cols))

    def _valid(self, value: float) -> float:
        if not math.isfinite(value):
            return float("nan")
        if self.valid and not (self.valid[0] <= value <= self.valid[1]):
            return float("nan")
        return value

    def recorded(self, timeline: Timeline, t: float) -> float:
        if self.select:
            key = timeline.text_at(self.select["by"], t)
            column = self.select.get(key, self.select.get("default"))
            value = timeline.value_at(column, t) if column else float("nan")
            if not math.isfinite(value) and self.fallback:
                value = timeline.value_at(self.fallback, t)
            return self._valid(value)
        if self.source is None:
            return float("nan")
        value = timeline.value_at(self.source, t)
        if not math.isfinite(value) and self.fallback:
            value = timeline.value_at(self.fallback, t)
        return self._valid(value)

    def recorded_many(self, timeline: Timeline, ts) -> np.ndarray:
        return np.array([self.recorded(timeline, float(t)) for t in ts])

    def available(self, timeline: Timeline) -> bool:
        return any(timeline.has(c) for c in self.source_columns
                   if not self.select or c != self.select.get("by"))


@dataclass
class ChannelMap:
    instruments: dict[str, dict[str, ChannelSpec]] = field(default_factory=dict)
    history: dict[str, ChannelSpec] = field(default_factory=dict)
    equipment: list[dict] = field(default_factory=list)       # everything Genesis talks to
    software_iocs: list[dict] = field(default_factory=list)   # Genesis IOCs with no hardware

    def channel(self, instrument: str, name: str) -> ChannelSpec:
        return self.instruments[instrument][name]

    def all_specs(self):
        for inst in self.instruments.values():
            yield from inst.values()
        yield from self.history.values()


def load(path: Path = MAP_FILE) -> ChannelMap:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cmap = ChannelMap()
    for inst, block in raw.get("instruments", {}).items():
        wire = block.get("wire", "none")
        absent = block.get("absent", "invalid")
        cmap.instruments[inst] = {
            name: _spec(inst, name, spec, wire, absent)
            for name, spec in block["channels"].items()}
    for name, spec in raw.get("history", {}).items():
        cmap.history[name] = _spec("history", name, spec, "none", "invalid")
    cmap.equipment = list(raw.get("equipment") or [])
    cmap.software_iocs = list(raw.get("software_iocs") or [])
    return cmap


def _spec(inst, name, spec, wire, absent) -> ChannelSpec:
    valid = spec.get("valid")
    return ChannelSpec(
        instrument=inst, name=name, source=spec.get("source"), select=spec.get("select"),
        fallback=spec.get("fallback"), units=str(spec.get("units", "")),
        valid=(float(valid[0]), float(valid[1])) if valid else None,
        confidence=spec.get("confidence", "guess"), note=" ".join(str(spec.get("note", "")).split()),
        wire=spec.get("wire", wire), absent=spec.get("absent", absent))


def validate(cmap: ChannelMap) -> list[str]:
    """Structural problems in the map (empty list = OK)."""
    problems = []
    for spec in cmap.all_specs():
        where = f"{spec.instrument}.{spec.name}"
        if spec.confidence not in CONFIDENCE:
            problems.append(f"{where}: confidence {spec.confidence!r}")
        if spec.wire not in WIRE:
            problems.append(f"{where}: wire {spec.wire!r}")
        if spec.absent not in ABSENT_POLICIES:
            problems.append(f"{where}: absent {spec.absent!r}")
        for col in spec.source_columns:
            if col not in aliases.CHANNELS:
                problems.append(f"{where}: unknown source column {col!r}")
        if spec.valid and not spec.valid[0] < spec.valid[1]:
            problems.append(f"{where}: empty valid range")
    for item in cmap.equipment:
        for key in EQUIPMENT_KEYS:
            if not item.get(key):
                problems.append(f"equipment {item.get('genesis', '?')}: missing {key}")
        if item.get("confidence") not in CONFIDENCE:
            problems.append(f"equipment {item.get('genesis', '?')}: confidence {item.get('confidence')!r}")
    return problems


# ------------------------------------------------------------- review report
def report_markdown(cmap: ChannelMap, coverage: dict[str, dict[str, float]] | None = None) -> str:
    """The cross-check document (docs/COOLDOWN_SIM_CHANNEL_MAP.md).

    ``coverage``: optional {cooldown: {column: fraction finite}} from real data.
    """
    lines = [
        "# Simulated cool-down channel map (for review)",
        "",
        "Generated by `python -m cooldown_sim channel-map-report` from "
        "`cooldown_sim/channel_map.yaml`. Edit the YAML, not this file.",
        "",
        "This is a **best-effort** mapping from the LabVIEW `data_record` columns to the "
        "Genesis instrument channels. Rows marked **guess** need a lab cross-check. After a "
        "real-data run, `<run>/channel_map_check.html` overlays each recorded column on the "
        "value Genesis actually archived, so a wrong mapping shows up as a mismatch.",
        "",
        "## Equipment Genesis talks to",
        "",
        "Every instrument the Genesis control software reads or drives, by its Genesis "
        "name, plus the hardware between them that has no IOC of its own. "
        "*Map* names the channel-map rows below that are fed from the past record.",
        "",
        "| Genesis name | Equipment | Role | Link | PV prefix | What Genesis may do | Map | Sim stand-in | Confidence | Check |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in cmap.equipment:
        lines.append(_equipment_row(item))
    lines += [
        "",
        "### Genesis IOCs without hardware",
        "",
        "| Genesis name | PV prefix | Role |",
        "|---|---|---|",
    ]
    for item in cmap.software_iocs:
        lines.append(f"| `{item['genesis']}` | {_cell(item.get('prefix'))} | {_cell(item.get('role'))} |")
    lines += [
        "",
        "## Instrument channels",
        "",
        "| Genesis instrument . channel | Source column(s) (header titles, newest first) | Units | Wire | Valid range | If absent | Confidence | Rationale / check |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for inst, chans in cmap.instruments.items():
        for spec in chans.values():
            lines.append(_row(spec))
    lines += ["", "## History inputs (target model and autopilot)", "",
              "| Input | Source column(s) | Units | Wire | Valid range | If absent | Confidence | Rationale / check |",
              "|---|---|---|---|---|---|---|---|"]
    for spec in cmap.history.values():
        lines.append(_row(spec))
    if coverage:
        cols = sorted({c for spec in cmap.all_specs() for c in spec.source_columns})
        lines += ["", "## Column coverage in the past record", "",
                  "Fraction of rows with a usable value, per cool-down (`-` = column absent in that era).",
                  "", "| Cool-down | " + " | ".join(cols) + " |",
                  "|---|" + "---|" * len(cols)]
        for cd, cov in coverage.items():
            cells = [("-" if c not in cov else f"{cov[c]:.2f}") for c in cols]
            lines.append(f"| {cd} | " + " | ".join(cells) + " |")
    lines += ["", "## Not mapped", "",
              "* `T1`/`T3` target-cup thermometers: CCX placeholders in "
              "`calibration/magnet_thermometry.csv`, not read by any Genesis IOC. "
              "`T1` feeds only the target model's lattice temperature.",
              "* Reliquefier PLC channels (`reliquefierAnalysis/data`): no Genesis IOC yet.",
              "* Flows (including the Roots flow meters, used for boil-off), helium levels, the "
              "magnet power supply and pump temperatures: in the LabVIEW record but no Genesis "
              "IOC yet; kept in the record for future legs.", ""]
    return "\n".join(lines)


def _cell(value) -> str:
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _equipment_row(item: dict) -> str:
    conf = item.get("confidence", "")
    conf = f"**{conf}**" if conf == "guess" else conf
    return (f"| `{item['genesis']}` | {_cell(item.get('equipment'))} | {_cell(item.get('role'))} | "
            f"{_cell(item.get('link'))} | {_cell(item.get('prefix'))} | {_cell(item.get('control'))} | "
            f"{_cell(item.get('map'))} | {_cell(item.get('sim'))} | {conf} | {_cell(item.get('note'))} |")


def _row(spec: ChannelSpec) -> str:
    def titles(col):
        return " / ".join(f"`{t}`" for t in aliases.CHANNELS.get(col, (col,)))
    if spec.select:
        by = spec.select["by"]
        picks = ", ".join(f"{k} -> {titles(v)}" for k, v in spec.select.items() if k != "by")
        src = f"chosen by {titles(by)}: {picks}"
        if spec.fallback:
            src += f"; else {titles(spec.fallback)}"
    elif spec.source:
        src = titles(spec.source)
    else:
        src = "(none in any era)"
    valid = f"{spec.valid[0]:g} to {spec.valid[1]:g}" if spec.valid else ""
    conf = f"**{spec.confidence}**" if spec.confidence == "guess" else spec.confidence
    note = spec.note.replace("|", "\\|")
    return (f"| `{spec.instrument}.{spec.name}` | {src} | {spec.units} | {spec.wire} | {valid} | "
            f"{spec.absent} | {conf} | {note} |")
