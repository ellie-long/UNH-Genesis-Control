"""Sim Control: engine control channel, controller IOC logic, and the screen.

No EPICS network: the engine runs without emulators, the controller IOC's
Device is built against the softioc stub, and both talk over a real
loopback control socket.
"""

import asyncio
import importlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from cooldown_sim import channel_map, run_dir
from cooldown_sim.clock import SimClock
from cooldown_sim.control import ControlClient, ControlServer
from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.engine import SimEngine
from cooldown_sim.profile import GeneratedStack
from cooldown_sim.sim_pvs import AUTOPILOT_LEGS, FAULT_INSTRUMENTS
from cooldown_sim.testing import softioc_stub
from cooldown_sim.timeparse import parse_lab_time

ROOT = Path(__file__).resolve().parents[2]
T0 = parse_lab_time("2026-06-17 09:00")


@pytest.fixture
def engine(tmp_path):
    run = run_dir.create("interactive", "test", root=tmp_path)
    table = SlowTable(t=np.array([T0 - 60, T0 + 3600.0]), numeric={"F1": np.array([4.0, 4.1])})
    eng = SimEngine(run, GeneratedStack(run.path), Timeline(table), channel_map.load(),
                    SimClock(T0, 60, running=False))
    eng.info.update({"mode": "interactive", "scenario": "test"})
    eng.start()
    yield eng
    eng.stop()


@pytest.fixture
def server(engine):
    srv = ControlServer(0, engine.handle).start()
    yield srv
    srv.stop()


def test_engine_commands_over_the_control_socket(engine, server):
    c = ControlClient(server.port)
    try:
        st = c.call("status")["status"]
        assert st["sim_local"] == "2026-06-17 09:00:00" and not st["running"] and st["mode"] == "interactive"
        assert c.call("play")["status"]["running"]
        assert c.call("speed", value=300)["status"]["speed"] == 300
        st = c.call("seek", value=parse_lab_time("2026-06-17 09:30"))["status"]
        assert st["sim_local"].startswith("2026-06-17 09:30")
        c.call("pause")
        st = c.call("jump", minutes=-10)["status"]
        assert st["sim_local"].startswith("2026-06-17 09:20")
        assert c.call("autopilot", leg="mw", on=True)["status"]["autopilot"]["mw"] is True
        assert c.call("note", text="cold-board looks fine")["status"]["last_event"].startswith("note")
        assert c.call("frobnicate")["ok"] is False                 # unknown commands are refused
        assert c.call("fault", instrument="nope", mode="silent")["ok"] is False
        assert c.call("model", key="quench", value=1)["ok"] is False  # replay-only session
        assert not engine.stop_requested.is_set()
        c.call("stop")
        assert engine.stop_requested.is_set()
    finally:
        c.close()
    events = (engine.run.path / "events.csv").read_text()
    for kind in ("clock,play", "clock,speed 300", "note,cold-board looks fine", "autopilot,mw on",
                 "session,end requested"):
        assert kind in events, kind


def test_control_socket_is_loopback_only(engine):
    with pytest.raises(ValueError):
        ControlServer(0, engine.handle, host="0.0.0.0")


def _controller(port):
    stub_ctx = softioc_stub.installed()
    stub = stub_ctx.__enter__()
    mod = importlib.import_module("cooldown_sim.controller")
    mod = importlib.reload(mod)
    dev = mod.Device("UNH:GEN:SIM", {"control_port": port, "history_points": 16})
    return dev, stub, stub_ctx


def test_controller_relays_commands_and_publishes_status(engine, server):
    dev, stub, ctx = _controller(server.port)
    rec = stub.builder.records

    async def session():                       # one event loop, as in the real IOC
        dev.connect()
        assert await dev.do_reads() is True
        assert rec["ENGINE_OK"].value == 1
        assert rec["SIM_DATETIME"].value == "2026-06-17 09:00:00"
        assert rec["MODE"].value == "interactive" and rec["RUNNING"].value == 0
        # operator writes, as Phoebus would cause them
        rec["RUN_CMD"].on_update(1)
        rec["SPEED_CMD"].on_update(120.0)
        rec["FAULT_LS224_FRIDGE_CMD"].on_update(3)            # silent
        rec["AUTOPILOT_SSRF_CMD"].on_update(1)
        rec["SEEK_CMD"].on_update("2026-06-17 10:00")
        rec["NOTE_CMD"].on_update("operator note")
        assert await dev.do_reads() is True
        assert rec["RUNNING"].value == 1 and rec["SPEED_RBV"].value == 120.0
        assert rec["SIM_DATETIME"].value.startswith("2026-06-17 10:00")
        assert rec["AUTOPILOT_SSRF_RBV"].value == 1
        assert rec["NOTE_RBV"].value == "operator note"
        assert rec["MODEL_ACTIVE"].value == 0                 # replay-only session
        assert engine.autopilot["ssrf"] is True
        # a bad seek string is reported, not sent
        rec["SEEK_CMD"].on_update("tomorrow-ish")
        assert "YYYY-MM-DD" in rec["LAST_EVENT"].value
        dev._writer.close()

    try:
        asyncio.run(session())
    finally:
        ctx.__exit__(None, None, None)


def test_controller_reports_lost_engine():
    dev, stub, ctx = _controller(1)                          # nothing listens on port 1
    try:
        assert asyncio.run(dev.do_reads()) is False
        assert stub.builder.records["ENGINE_OK"].value == 0
    finally:
        ctx.__exit__(None, None, None)


# ----------------------------------------------------------------- the screen
BOB = ROOT / "cooldown_sim" / "gui" / "genesis_sim_control.bob"


def test_committed_screen_is_current():
    from cooldown_sim.gui import make_sim_control
    assert BOB.read_text(encoding="utf-8") == make_sim_control.build(), \
        "regenerate: python -m cooldown_sim.gui.make_sim_control"


def test_screen_pvs_exist_and_links_resolve():
    dev, stub, ctx = _controller(1)
    try:
        served = {f"UNH:GEN:SIM:{name}" for name in stub.builder.records}
    finally:
        ctx.__exit__(None, None, None)
    root = ET.parse(BOB).getroot()
    text = BOB.read_text(encoding="utf-8")
    pvs = set(re.findall(r"ca://([A-Za-z0-9:_]+)", text))
    assert pvs and pvs <= served, sorted(pvs - served)
    for f in root.iter("file"):
        assert (BOB.parent / f.text).resolve().exists(), f.text
    for inst in FAULT_INSTRUMENTS:
        assert f"UNH:GEN:SIM:FAULT_{inst.upper()}_CMD" in pvs
    for leg in AUTOPILOT_LEGS:
        assert f"UNH:GEN:SIM:AUTOPILOT_{leg.upper()}_CMD" in pvs
    assert "SIMULATION - NOT LIVE HARDWARE" in text


def test_destructive_buttons_ask_first():
    # Phoebus's property is show_confirm_dialog; an unknown element is silently ignored
    root = ET.parse(BOB).getroot()
    asking = {w.findtext("text") for w in root.iter("widget")
              if w.get("type") == "action_button" and w.findtext("show_confirm_dialog") == "true"}
    assert {"End session", "QUENCH"} <= asking, asking
    assert "<confirm_dialog>" not in BOB.read_text(encoding="utf-8")


def test_screen_is_not_among_live_displays():
    assert not (ROOT / "gui" / "genesis_sim_control.bob").exists()


def test_phoebus_ini_pins_both_ca_clients(tmp_path):
    from cooldown_sim import phoebus
    ini = phoebus.write_ini(tmp_path, [6116, 6150]).read_text()
    assert "org.phoebus.pv.ca/addr_list=127.0.0.1:6116 127.0.0.1:6150" in ini
    assert "org.phoebus.pv.jackie/ca_address_list=127.0.0.1:6116 127.0.0.1:6150" in ini
    assert "org.phoebus.pv.jackie/use_env=false" in ini
    assert "auto_addr_list=false" in ini and "5116" not in ini
    assert "pva" not in ini


def test_phoebus_ini_pins_pva_name_servers(tmp_path):
    # pva:// screens (Microwave) need each IOC's PVA server named explicitly
    from cooldown_sim import phoebus
    ini = phoebus.write_ini(tmp_path, [6116], [6250, 6216]).read_text()
    assert "org.phoebus.pv.pva/epics_pva_name_servers=127.0.0.1:6216 127.0.0.1:6250" in ini
    assert "org.phoebus.pv.pva/epics_pva_auto_addr_list=false" in ini
