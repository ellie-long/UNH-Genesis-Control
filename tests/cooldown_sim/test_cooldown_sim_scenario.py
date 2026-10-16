"""Scenario files, the expectation evaluator, the scenario player and the
after-action report -- all offline (no IOCs, no sockets)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cooldown_sim import scenario as sc
from cooldown_sim.model.provider import INPUT_KEYS
from cooldown_sim.profile import SCENARIO_OVERRIDABLE
from cooldown_sim.sim_pvs import AUTOPILOT_LEGS, FAULT_INSTRUMENTS, FAULT_MODES
from cooldown_sim.validate import Check, after_action
from cooldown_sim.validate import scenario as scenario_checks

KNOWN_LEGS = {"cryo", "mw", "ssrf", "nmr"}
WALL0 = 1_000_000.0
SIM0 = 946_710_000.0          # sim epoch of the fake run


# ------------------------------------------------------------ scenario files
def test_available_lists_scenarios_not_limits():
    names = sc.available()
    assert "vacuum-trip" in names and "nominal" in names
    assert not any(n.startswith("limits") for n in names)


@pytest.mark.parametrize("name", sc.available())
def test_scenario_file_is_well_formed(name):
    spec = sc.load(name)
    assert spec["name"] == name
    assert spec.get("description")
    assert set(spec.get("legs", ["cryo"])) <= KNOWN_LEGS
    assert spec.get("source", "synthetic") in ("synthetic", "real")
    if spec.get("source") == "real":
        assert spec.get("cooldown") and spec.get("start")
    for ioc, settings in (spec.get("overrides") or {}).items():
        for key in settings:
            assert (ioc, key) in SCENARIO_OVERRIDABLE
        if "limits" in settings:
            limits = Path(settings["limits"])
            assert limits.is_absolute() and limits.exists()
            assert "SIM-ONLY" in limits.read_text(encoding="utf-8")
    duration = float(spec.get("duration_min", 30))
    for a in spec.get("actions") or []:
        assert 0 <= float(a["at"]) <= duration
        if a["do"] == "model":
            assert a["key"] in INPUT_KEYS or a["key"] == "take_baseline"
        elif a["do"] == "fault":
            assert a["instrument"] in FAULT_INSTRUMENTS and a["mode"] in FAULT_MODES
        elif a["do"] == "caput":
            assert a["pv"].startswith("UNH:GEN:")
        elif a["do"] == "autopilot":
            assert a["leg"] in AUTOPILOT_LEGS and isinstance(a["on"], bool)
        else:
            assert a["do"] in ("override", "note")
    for e in spec.get("expect") or []:
        assert sum(k in e for k in ("pv", "truth", "check")) == 1
        if "check" not in e:
            assert e.get("op", e.get("severity", "eq")) in sc.OPS
            assert e.get("mode", "eventually") in ("eventually", "always")
            assert float(e.get("from", 0)) <= float(e.get("to", duration)) <= duration
            if "truth" in e:
                assert "nmr" in spec["legs"]
    for w in spec.get("waive") or []:
        assert w.get("check") and w.get("reason")


def test_bare_on_key_survives_yaml_1_1(tmp_path):
    # YAML 1.1 reads `on:` as True; the action must still say on=False
    path = tmp_path / "x.yaml"
    path.write_text("actions:\n  - {at: 1, do: autopilot, leg: mw, on: false}\n", encoding="utf-8")
    assert sc.load(str(path))["actions"][0]["on"] is False


def test_load_resolves_limits_relative_to_scenario_dir():
    spec = sc.load("vacuum-trip")
    path = Path(spec["overrides"]["safety_interlock"]["limits"])
    assert path.parent == sc.SCENARIO_DIR.resolve()


# ------------------------------------------------------------ evaluator
def _fake_run(tmp_path: Path, probe_rows, events=(), nmr_rows=None) -> Path:
    """A run dir with a 10x clock: wall WALL0 -> sim SIM0."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "clock.json").write_text(json.dumps(
        [{"wall": WALL0, "sim": SIM0, "speed": 10.0, "running": True, "reason": "start"}]),
        encoding="utf-8")
    lines = ["wall,pv,value,severity,connected"]
    for sim_min, pv, value, sev in probe_rows:
        lines.append(f"{WALL0 + 6.0 * sim_min:.3f},{pv},{value},{sev},1")
    (tmp_path / "probe.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ev = ["wall,sim,sim_local,kind,detail"]
    for sim_min, kind, detail in events:
        ev.append(f"{WALL0 + 6.0 * sim_min:.3f},{SIM0 + 60.0 * sim_min:.3f},x,{kind},{detail}")
    (tmp_path / "events.csv").write_text("\n".join(ev) + "\n", encoding="utf-8")
    if nmr_rows is not None:
        rows = ["sim,true_P,P_fit"] + [f"{SIM0 + 60.0 * m},{p},{f}" for m, p, f in nmr_rows]
        (tmp_path / "nmr.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return tmp_path


PERMIT = "UNH:GEN:CRYO:SAFE:Vac_Permit"


def _verdicts(run, expect, all_checks=()):
    return [c.verdict for c in scenario_checks.checks(run, {"expect": expect}, SIM0, list(all_checks))]


def test_value_held_into_the_window_counts(tmp_path):
    # Vac_Permit dropped at minute 3 and did not change again: the probe has no
    # sample inside [4, 8], but the window held 0 throughout
    run = _fake_run(tmp_path, [(0.5, PERMIT, 1, 0), (3, PERMIT, 0, 0), (12, PERMIT, 1, 0)])
    assert _verdicts(run, [
        {"pv": PERMIT, "from": 4, "to": 8, "op": "eq", "value": 0},
        {"pv": PERMIT, "from": 4, "to": 8, "op": "eq", "value": 0, "mode": "always"},
        {"pv": PERMIT, "from": 4, "to": 8, "op": "eq", "value": 1},
    ]) == ["PASS", "PASS", "FAIL"]


def test_always_fails_on_a_change_inside_the_window(tmp_path):
    run = _fake_run(tmp_path, [(0.5, PERMIT, 0, 0), (6, PERMIT, 1, 0)])
    assert _verdicts(run, [{"pv": PERMIT, "from": 4, "to": 8, "op": "eq", "value": 0, "mode": "always"},
                           {"pv": PERMIT, "from": 4, "to": 8, "op": "eq", "value": 1}]) == ["FAIL", "PASS"]


def test_no_samples_at_all_fails_both_modes(tmp_path):
    run = _fake_run(tmp_path, [(0.5, "UNH:GEN:OTHER", 1, 0)])
    assert _verdicts(run, [{"pv": PERMIT, "op": "eq", "value": 0},
                           {"pv": PERMIT, "op": "eq", "value": 0, "mode": "always"}]) == ["FAIL", "FAIL"]


def test_severity_contains_and_numeric_ops(tmp_path):
    pv = "UNH:GEN:CRYO:FRIDGE:F5_TI"
    run = _fake_run(tmp_path, [(1, pv, "4.2", 0), (5, pv, "25.0", 2),
                               (5, "UNH:GEN:CRYO:SAFE:Safe_Reason", "OVC_PI 0.0027 >= 0.0011", 0)])
    assert _verdicts(run, [
        {"pv": pv, "from": 4, "to": 8, "op": "gt", "value": 20},
        {"pv": pv, "from": 4, "to": 8, "severity": "ge", "value": 2},
        {"pv": pv, "from": 0, "to": 3, "op": "lt", "value": 5, "mode": "always"},
        {"pv": "UNH:GEN:CRYO:SAFE:Safe_Reason", "from": 4, "to": 8, "op": "contains", "value": "OVC"},
    ]) == ["PASS", "PASS", "PASS", "PASS"]


def test_expectation_naming_a_known_issue_reports_known(tmp_path):
    pv = "UNH:GEN:CRYO:ION:Ion_PI"
    run = _fake_run(tmp_path, [(1, pv, "4e-7", 0), (5, pv, "5e-4", 0)])
    assert _verdicts(run, [
        {"pv": pv, "from": 4, "to": 8, "severity": "ge", "value": 1, "known": "ionivac-alarm-limits"},
        {"pv": pv, "from": 4, "to": 8, "op": "gt", "value": 1e-4, "known": "ionivac-alarm-limits"},
        {"pv": pv, "from": 4, "to": 8, "severity": "ge", "value": 1},
    ]) == ["KNOWN", "PASS", "FAIL"]


def test_truth_and_check_expectations(tmp_path):
    run = _fake_run(tmp_path, [(0.5, PERMIT, 1, 0)], nmr_rows=[(1, 30.0, 29.9), (10, 2.0, 2.5)])
    existing = [Check("read-only proof", "PASS", "")]
    assert _verdicts(run, [
        {"truth": "true_P", "from": 8, "to": 12, "op": "lt", "value": 5},
        {"truth": "true_P", "from": 0, "to": 2, "op": "lt", "value": 5},
        {"check": "read-only proof", "verdict": "PASS"},
        {"check": "ssrf burn replay", "verdict": ["KNOWN", "SKIPPED"]},
    ], existing) == ["PASS", "FAIL", "PASS", "FAIL"]


# ------------------------------------------------------------ player
class _Clock:
    def __init__(self):
        self.t = SIM0

    def now(self):
        return self.t


class _Events:
    def __init__(self):
        self.rows = []

    def write(self, t, kind, detail):
        self.rows.append((t, kind, detail))


class _Engine:
    def __init__(self):
        self.clock, self.events, self.requests = _Clock(), _Events(), []

    def handle(self, request):
        self.requests.append(request)


class _CA:
    def __init__(self):
        self.puts = []

    async def caput(self, pv, value):
        self.puts.append((pv, value))


def test_player_fires_actions_in_sim_order():
    engine, ca = _Engine(), _CA()
    spec = {"actions": [
        {"at": 8, "do": "override", "instrument": "tpg262", "channel": "OVC", "value": None},
        {"at": 3, "do": "override", "instrument": "tpg262", "channel": "OVC", "value": 2e-3},
        {"at": 12, "do": "caput", "pv": PERMIT, "value": 1},
    ]}
    player = sc.ScenarioPlayer(spec, engine, ca, SIM0)

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(player.run(stop))
        for minute in (1, 4, 9, 13):
            engine.clock.t = SIM0 + 60.0 * minute
            await asyncio.sleep(0.25)
            if minute == 1:
                assert engine.requests == []
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(drive())
    assert [r["value"] for r in engine.requests] == [2e-3, None]
    assert ca.puts == [(PERMIT, 1)]
    assert [k for _, k, _ in engine.events.rows] == ["scenario"] * 3


def test_player_logs_a_failing_action_and_carries_on():
    engine, ca = _Engine(), _CA()
    spec = {"actions": [{"at": 0, "do": "teleport"}, {"at": 0, "do": "caput", "pv": PERMIT, "value": 1}]}
    player = sc.ScenarioPlayer(spec, engine, ca, SIM0)

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(player.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(drive())
    assert player.done == 2 and ca.puts == [(PERMIT, 1)]
    assert len(player.errors) == 1 and "teleport" in player.errors[0]
    assert "scenario-error" in [k for _, k, _ in engine.events.rows]


def test_actions_check(tmp_path):
    run = _fake_run(tmp_path, [(0.5, PERMIT, 1, 0)])
    spec = {"actions": [{"at": 1}, {"at": 2}]}
    verdict = lambda a: scenario_checks.checks(run, spec, SIM0, [], actions=a)[0].verdict  # noqa: E731
    assert verdict({"total": 2, "done": 2, "errors": []}) == "PASS"
    assert verdict({"total": 2, "done": 1, "errors": []}) == "FAIL"
    assert verdict({"total": 2, "done": 2, "errors": ["boom"]}) == "FAIL"
    assert verdict(None) == "FAIL"


def test_player_rejects_unknown_action():
    player = sc.ScenarioPlayer({"actions": [{"at": 0, "do": "teleport"}]}, _Engine(), _CA(), SIM0)
    with pytest.raises(ValueError):
        asyncio.run(player._do(player.actions[0]))


# ------------------------------------------------------------ after-action
def test_after_action_merges_events_and_state_changes(tmp_path):
    run = _fake_run(
        tmp_path,
        [(0.5, PERMIT, 1, 0), (3, PERMIT, 0, 0), (3.1, PERMIT, 0, 0), (12, PERMIT, 1, 0),
         (3, "UNH:GEN:CRYO:SAFE:Safe_Reason", "OVC_PI high", 0)],
        events=[(3, "override", "tpg262.OVC forced to 0.002"), (4, "autopilot", "caput x"),
                (12, "note", "operator reset the latch")])
    md, _ = after_action.write(run)
    text = md.read_text(encoding="utf-8")
    assert "SIMULATION" in text
    assert "autopilot command writes: 1" in text
    assert "tpg262.OVC forced" in text and "operator reset the latch" in text
    assert "caput x" not in text
    # one row per transition, not per repeated sample
    assert text.count("Vac_Permit: 0") == 1 and text.count("Vac_Permit: 1") == 2
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "sim time" not in ln]
    kinds = [r.split("|")[2].strip() for r in rows]
    assert kinds.index("override") < kinds.index("note")


# ------------------------------------------------------------ archive round trip
def test_archive_rows_outside_the_probe_span_are_unverifiable(tmp_path):
    import datetime as dt

    from cooldown_sim.validate import loaders, roundtrip

    pv = "UNH:GEN:CRYO:ALARM:MAN:alarm_summary_time"
    lines = ["wall,pv,value,severity,connected"]
    for k in range(5):                                  # probe saw 100..104 at WALL0 + 5k
        lines.append(f"{WALL0 + 5 * k:.3f},{pv},{100 + k},0,1")
    (tmp_path / "probe.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = tmp_path / "archive" / "data"
    data.mkdir(parents=True)

    def stamp(wall):
        return dt.datetime.fromtimestamp(wall).strftime("%Y-%m-%d %H:%M:%S.%f")

    rows = [(WALL0 - 30, 99),          # before the probe subscribed: unverifiable
            (WALL0 + 5.2, 101),        # matches
            (WALL0 + 10.4, 102),       # matches
            (WALL0 + 15.1, 777),       # never on the PV: a real mismatch
            (WALL0 + 25.5, 105)]       # after the probe stopped: unverifiable
    name = loaders.safe_name(pv) + "_" + dt.datetime.fromtimestamp(WALL0).strftime("%Y-%m-%d")
    (data / f"{name}.csv").write_text(
        "Timestamp,Value\n" + "".join(f"{stamp(w)},{v}\n" for w, v in rows), encoding="utf-8")
    checks = {c.name: c for c in roundtrip.archive(tmp_path, [pv])}
    c = checks[f"archive {pv}"]
    assert c.verdict == "FAIL"
    assert c.summary.startswith("2/3 archived rows match") and "2 recorded outside" in c.summary
    assert any("777" in d for d in c.details)


def test_waiver_turns_only_the_named_fail_into_info():
    checks = [Check("nmr fitter vs model truth", "FAIL", "median 1.4 pp"),
              Check("read-only proof", "FAIL", "1 write"),
              Check("heartbeat x", "PASS", "")]
    scenario_checks.apply_waivers(
        {"waive": [{"check": "nmr fitter vs model truth", "reason": "Q lag"}]}, checks)
    assert [c.verdict for c in checks] == ["INFO", "FAIL", "PASS"]
    assert checks[0].summary.startswith("WAIVED by scenario (Q lag)")


def test_fit_error_truth_column(tmp_path):
    run = _fake_run(tmp_path, [(0.5, PERMIT, 1, 0)], nmr_rows=[(1, 30.0, 29.5), (2, 30.0, 31.6)])
    assert _verdicts(run, [
        {"truth": "abs_fit_err_P", "from": 0, "to": 1.5, "op": "le", "value": 1.0, "mode": "always"},
        {"truth": "abs_fit_err_P", "from": 0, "to": 3, "op": "le", "value": 1.0, "mode": "always"},
    ]) == ["PASS", "FAIL"]
