"""Scripted scenarios: timed actions + expectations on top of a simulated cool-down.

A scenario file (``cooldown_sim/scenarios/<name>.yaml``)::

    name: quench
    description: ...
    source: synthetic            # or real (needs cooldown:, start:, $GENESIS_REPLAY_DATA)
    start: "2000-01-01 02:00"
    duration_min: 60
    speed: 20
    legs: [cryo]
    faults: []                   # synthetic record faults
    overrides:                   # replay-only profile overrides (profile.SCENARIO_OVERRIDABLE)
      safety_interlock: {limits: limits_replay.yaml}      # relative to scenarios/
    autopilot: [mw]              # legs whose autopilot starts ON (default: regression defaults)
    actions:                     # at = sim minutes after start
      - {at: 5, do: model, key: quench, value: 1}
      - {at: 10, do: fault, instrument: ls224_fridge, mode: silent}
      - {at: 12, do: override, instrument: tpg262, channel: OVC, value: 2.0e-3}   # Torr
      - {at: 15, do: caput, pv: "UNH:GEN:CRYO:SAFE:Safe_Reset", value: 1}
      - {at: 20, do: autopilot, leg: mw, on: false}
    expect:
      - {pv: "UNH:GEN:CRYO:SAFE:Vac_Permit", from: 6, to: 12, op: eq, value: 0}
      - {pv: "UNH:GEN:CRYO:FRIDGE:F5_TI", from: 6, to: 12, severity: ge, value: 1}
      - {pv: "...", from: 1, to: 30, op: eq, value: 1, mode: always}
      - {truth: true_P, from: 30, to: 40, op: lt, value: 45}
      - {check: "ssrf burn replay", verdict: [KNOWN, SKIPPED]}
      - {truth: abs_fit_err_P, from: 10, to: 15, op: le, value: 1.0, mode: always}
    waive:                       # a general check this scenario drives outside its premise
      - {check: "nmr fitter vs model truth", reason: "..."}

``mode`` is ``eventually`` (default: at least one sample in the window
satisfies) or ``always`` (every sample does, and there is at least one).

YAML 1.1 reads a bare ``on:`` key as the boolean True; ``load`` turns it back
into ``"on"`` so ``{do: autopilot, leg: mw, on: false}`` means what it says.
"""

from __future__ import annotations

import asyncio
import operator
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).with_name("scenarios")
OPS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "ge": operator.ge,
       "lt": operator.lt, "le": operator.le, "contains": lambda a, b: str(b) in str(a)}


def available() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml") if not p.stem.startswith("limits"))


def load(name: str) -> dict:
    path = Path(name)
    if not path.exists():
        path = SCENARIO_DIR / f"{name}.yaml"
    with open(path, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    spec.setdefault("name", path.stem)
    for a in spec.get("actions") or []:
        if True in a:                       # YAML 1.1: bare `on:` key -> True
            a["on"] = a.pop(True)
    for ioc, settings in (spec.get("overrides") or {}).items():
        if "limits" in settings and not Path(settings["limits"]).is_absolute():
            settings["limits"] = str((SCENARIO_DIR / settings["limits"]).resolve())
    return spec


class ScenarioPlayer:
    """Runs a scenario's actions at their sim times through the engine's own
    control channel (and the restricted operator CA for caputs)."""

    def __init__(self, spec: dict, engine, ca, t0: float):
        self.engine = engine
        self.ca = ca
        self.actions = sorted(spec.get("actions") or [], key=lambda a: float(a["at"]))
        self.t0 = t0
        self.done = 0
        self.errors: list[str] = []

    async def run(self, stop_event) -> None:
        while not stop_event.is_set() and self.done < len(self.actions):
            a = self.actions[self.done]
            if self.engine.clock.now() >= self.t0 + 60.0 * float(a["at"]):
                try:
                    await self._do(a)
                except Exception as exc:  # noqa: BLE001 - one bad action must not stop the rest
                    msg = f"t+{float(a['at']):g} min {a.get('do')}: {type(exc).__name__}: {exc}"
                    self.errors.append(msg)
                    self.engine.events.write(self.engine.clock.now(), "scenario-error", msg)
                self.done += 1
                continue
            await asyncio.sleep(0.2)

    async def _do(self, a: dict) -> None:
        kind = a["do"]
        self.engine.events.write(self.engine.clock.now(), "scenario",
                                 f"t+{float(a['at']):g} min: {kind} "
                                 + ", ".join(f"{k}={v}" for k, v in a.items() if k not in ("at", "do")))
        if kind == "model":
            self.engine.handle({"cmd": "model", "key": a["key"], "value": a["value"]})
        elif kind == "fault":
            self.engine.handle({"cmd": "fault", "instrument": a["instrument"], "mode": a["mode"]})
        elif kind == "autopilot":
            self.engine.handle({"cmd": "autopilot", "leg": a["leg"], "on": bool(a["on"])})
        elif kind == "override":
            self.engine.handle({"cmd": "override", "instrument": a["instrument"],
                                "channel": a["channel"], "value": a.get("value")})
        elif kind == "caput":
            await self.ca.caput(a["pv"], a["value"])
        elif kind == "note":
            self.engine.handle({"cmd": "note", "text": a.get("text", "")})
        else:
            raise ValueError(f"unknown scenario action {kind!r}")
