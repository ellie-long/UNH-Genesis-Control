"""The optional cryo IOCs (flowmeters, LHe level meters, Manometer-Lo): the REAL
Genesis drivers against the cooldown_sim emulators, plus their model coupling.

Same pattern as test_cooldown_sim_emulators.py: the unchanged driver ``Device``
from target_ioc.instruments, pointed at an emulator on an ephemeral 127.0.0.1
port, with the softioc stub standing in for EPICS records.
"""

import math
import time

import numpy as np
import pytest

from cooldown_sim import channel_map, ports, profile
from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.emulators import host
from cooldown_sim.emulators.cryo_optional import AMI135, AMI1700, HastingsFlowmeter, KJLSparcStream
from cooldown_sim.model.provider import ModelProvider
from cooldown_sim.model.target import MANO_LO_FULL_SCALE_TORR, TargetModel, load_params

from test_cooldown_sim_emulators import ROOT, assert_read_failed, profile as live_profile, read, \
    real_driver, value

NEW_IOCS = ("flowmeter_pi", "flowmeter_left", "flowmeter_sep", "lhe_fridge", "lhe_magnet",
            "manometer_low")


# ------------------------------------------------------------- flowmeters
def test_flowmeter_pi_server_round_trip_and_error_reply():
    settings = live_profile("flowmeter_pi")
    corr = settings["correction"]
    state = {"RPump": 8.8778}
    emu = HastingsFlowmeter("RPump", lambda ch: state[ch], correction=corr, via_pi=True)
    with real_driver("flowmeter_pi", settings, emu) as (dev, srv, _mod):
        assert value(dev, "COMM") == 1
        assert read(dev) is True
        assert value(dev, "RPump_SLM") == pytest.approx(8.8778, abs=1e-6)     # = the record
        assert value(dev, "RPump_Raw") == pytest.approx(8.8778 / corr, abs=1e-6)
        assert value(dev, "RPump_Status") == "OK"
        state["RPump"] = float("nan")                  # the Pi has no reading: a status, not a link fault
        assert read(dev) is True
        assert math.isnan(value(dev, "RPump_SLM")) and value(dev, "RPump_Status") == "no reading"
        assert dev.pvs["RPump_SLM"].severity != 0      # CryoReadOnlyHealth alarms the NaN
        state["RPump"] = 5.0
        srv.set_fault("nak")                           # the Pi's serial side is down
        assert read(dev) is True
        assert value(dev, "RPump_Status") == "serial port not open"
        srv.set_fault("garbage")
        assert read(dev) is True and value(dev, "RPump_Status") == "PARSE_ERR"
        srv.set_fault("silent")                        # the Pi itself goes quiet
        assert_read_failed(dev, "RPump_SLM")
        assert emu.writes() == []


@pytest.mark.parametrize("ioc,ch", [("flowmeter_left", "LPump"), ("flowmeter_sep", "SepFlow")])
def test_flowmeter_gateway_round_trip_and_empty_reply(ioc, ch):
    settings = live_profile(ioc)
    corr = settings["correction"]
    state = {ch: 51.5606}
    emu = HastingsFlowmeter(ch, lambda c: state[c], correction=corr, via_pi=False)
    with real_driver("flowmeter_diewu", settings, emu) as (dev, srv, _mod):
        assert read(dev) is True
        assert value(dev, f"{ch}_SLM") == pytest.approx(51.5606, abs=1e-6)
        assert value(dev, f"{ch}_Raw") == pytest.approx(51.5606 / corr, abs=1e-6)
        state[ch] = -0.31                              # small negative zero offsets are replayed
        assert read(dev) is True and value(dev, f"{ch}_SLM") == pytest.approx(-0.31, abs=1e-6)
        state[ch] = float("nan")
        assert read(dev) is True
        assert math.isnan(value(dev, f"{ch}_SLM")) and value(dev, f"{ch}_Status") == "EMPTY_REPLY"
        srv.set_fault("silent")
        assert_read_failed(dev, f"{ch}_SLM")
    assert emu.respond(b"X\r") is None                 # the meter ignores what it does not know


# ------------------------------------------------------------ level meters
def test_ami1700_identity_level_and_no_reading():
    settings = live_profile("lhe_fridge")
    state = {"Fridge": 12.345}
    emu = AMI1700("Fridge", lambda ch: state[ch], scale=settings["scale"], offset=settings["offset"])
    with real_driver("ami1700", settings, emu) as (dev, srv, _mod):
        assert "MODEL 1700" in value(dev, "Fridge_IDN")
        assert read(dev) is True
        assert value(dev, "Fridge_LI") == pytest.approx(12.345, abs=6e-4)
        assert value(dev, "Fridge_Raw") == pytest.approx(12.345, abs=6e-4)
        state["Fridge"] = -9.288                       # the record's (unexplained) negative levels
        assert read(dev) is True and value(dev, "Fridge_LI") == pytest.approx(-9.288, abs=6e-4)
        state["Fridge"] = float("nan")
        assert read(dev) is True
        assert math.isnan(value(dev, "Fridge_LI")) and value(dev, "Fridge_Status") == "EMPTY_REPLY"
        srv.set_fault("garbage")
        assert read(dev) is True and value(dev, "Fridge_Status") == "PARSE_ERR"
    assert emu.respond(b"MEAS:HE:LEV?\r") is not None  # SCPI short form
    assert emu.respond(b"SYST:ERR?\r") is None


def test_ami1700_scale_and_offset_are_undone_on_the_wire():
    emu = AMI1700("Fridge", lambda ch: 10.0, scale=2.0, offset=1.0)
    assert emu.respond(b"MEASure:HE:LEVel?\r") == b"4.500\r\n"


def test_ami135_echo_and_centimetres():
    settings = live_profile("lhe_magnet")
    scale = settings["scale"]
    state = {"Magnet": 28.0930}
    emu = AMI135("Magnet", lambda ch: state[ch], scale=scale)
    with real_driver("ami135", settings, emu) as (dev, srv, _mod):
        assert read(dev) is True
        cm = round(28.0930 / scale, 1)                 # the monitor's 0.1 cm resolution
        assert value(dev, "Magnet_Raw") == pytest.approx(cm, abs=1e-9)
        assert value(dev, "Magnet_LI") == pytest.approx(28.0930, abs=0.0201)
        assert value(dev, "Magnet_Status") == "OK"
        state["Magnet"] = float("nan")
        assert read(dev) is True
        assert math.isnan(value(dev, "Magnet_LI")) and value(dev, "Magnet_Status") == "EMPTY_REPLY"
        srv.set_fault("silent")
        assert_read_failed(dev, "Magnet_LI")
    assert emu.respond(b"LEVEL\r").startswith(b"LEVEL\r\n")       # the DIP-switch echo


# ------------------------------------------------------------ Manometer-Lo
def test_manometer_low_stream_and_vapor_temperature(monkeypatch):
    monkeypatch.chdir(ROOT)                            # vapor_bands resolves against cwd
    from genesis_common import its90_helium as he
    settings = live_profile("manometer_low")
    state = {"Mano": 0.2001}
    emu = KJLSparcStream("Mano", lambda ch: state[ch])
    with real_driver("manometer_low", settings, emu, timeout=1.0) as (dev, srv, _mod):
        dev.read_outs()                                # loads the band file, as at IOC start
        time.sleep(0.3)
        assert read(dev) is True
        assert value(dev, "Mano_PI") == pytest.approx(0.2001, rel=6e-5)
        assert value(dev, "Mano_Status") == "OK"
        assert value(dev, "Mano_Raw").startswith("Pr: ")
        # 0.2 Torr is ~1.05 K: below the ITS-90 scale the driver publishes no temperature
        assert math.isnan(value(dev, "Mano_VapT"))
        assert value(dev, "Mano_VapStatus").startswith("P below scale")
        state["Mano"] = 4.2
        time.sleep(0.3)
        assert read(dev) is True
        assert value(dev, "Mano_VapT") == pytest.approx(he.torr_to_kelvin(4.2)[0], abs=1e-6)
        assert value(dev, "Mano_VapPhase") == "He-II superfluid"
        state["Mano"] = float("nan")                   # record has nothing: a line with no number
        time.sleep(0.3)
        assert read(dev) is True
        assert math.isnan(value(dev, "Mano_PI")) and value(dev, "Mano_Status") == "PARSE_ERR"
        srv.set_fault("silent")                        # the stream stops
        time.sleep(0.3)
        assert_read_failed(dev, "Mano_PI")
    assert emu.respond(b"anything\r\n") is None        # it answers no command


# -------------------------------------------------------- stack and profiles
def test_new_iocs_are_in_the_cryo_stack_on_free_ports():
    stacks = ports.load_stacks()
    cryo = stacks["legs"]["cryo"]["iocs"]
    for name in NEW_IOCS:
        assert name in cryo and "emulator" in cryo[name], name
    assert {cryo[n]["ca"] for n in NEW_IOCS} == set(range(6124, 6130))
    assert {cryo[n]["pva"] for n in NEW_IOCS} == set(range(6224, 6230))
    replay = ports.replay_ports(stacks)
    assert 6123 not in replay and 6223 not in replay       # dev-visualizer's trend-history IOC
    assert all(16000 <= cryo[n]["emulator"]["port"] < 17000 for n in NEW_IOCS)
    assert not ports.duplicate_replay_ports(stacks)


def test_generated_profiles_build_the_matching_emulators(tmp_path):
    stack = profile.generate(tmp_path, legs=("cryo",))
    kinds = {}
    for spec in stack.iocs:
        if spec.name not in NEW_IOCS:
            continue
        settings = host._settings(spec)
        assert settings["ip"] == "127.0.0.1" and settings["write_enabled"] is False
        emu = host.build(spec, settings, lambda ch: 1.0)
        kinds[spec.name] = type(emu).__name__
        if isinstance(emu, HastingsFlowmeter):
            assert emu.correction == pytest.approx(28 / 19)
            assert emu.via_pi == (spec.name == "flowmeter_pi")
    assert kinds == {"flowmeter_pi": "HastingsFlowmeter", "flowmeter_left": "HastingsFlowmeter",
                     "flowmeter_sep": "HastingsFlowmeter", "lhe_fridge": "AMI1700",
                     "lhe_magnet": "AMI135", "manometer_low": "KJLSparcStream"}


def test_channel_map_covers_every_new_ioc_channel():
    cmap = channel_map.load()
    assert not channel_map.validate(cmap)
    for name in NEW_IOCS:
        chans = [c for c in live_profile(name)["channels"] if "None" not in str(c)]
        assert set(cmap.instruments[name]) == set(chans), name
    record_only = " ".join(str(item.get("columns")) for item in cmap.record_only)
    for col in ("manometer_lo", "roots_flow_l", "roots_flow_r", "separator_flow",
                "fridge_he_level", "magnet_lhe_level"):
        assert col not in record_only, col


# ------------------------------------------------------------------- model
def flows_record(hours=3.0, left=6.0, right=2.0, lo=0.12):
    t = np.arange(0.0, hours * 3600.0, 20.0)
    n = len(t)
    return Timeline(SlowTable(t=t, numeric={
        "T1": np.full(n, 1.0), "central_field": np.full(n, 4.939), "mw_rf_status": np.zeros(n),
        "online_p": np.full(n, 0.05), "roots_flow_l": np.full(n, left),
        "roots_flow_r": np.full(n, right), "manometer_lo": np.full(n, lo),
        "manometer_hi": np.full(n, 0.1)}), max_hold_s=60)


def test_boiloff_is_split_between_the_roots_flowmeters():
    params = load_params()
    m = TargetModel(flows_record(), 60.0, params)
    assert m.roots_flow("L", 6.0, 600.0) == pytest.approx(6.0)        # no extra heat: the record
    m.inputs.coldboard_heat_W = 0.1
    t = 600.0 + 20 * params["heat"]["flow_tau_s"]
    extra = 0.1 / params["heat"]["W_per_slm"]
    assert m.roots_flow("L", 6.0, t) == pytest.approx(6.0 + 0.75 * extra, rel=1e-3)
    assert m.roots_flow("R", 2.0, t) == pytest.approx(2.0 + 0.25 * extra, rel=1e-3)
    one = TargetModel(Timeline(SlowTable(t=flows_record().table.t, numeric={
        k: v for k, v in flows_record().table.numeric.items() if k != "roots_flow_l"}), max_hold_s=60),
        60.0, params)
    one.inputs.coldboard_heat_W = 0.1
    assert one.roots_flow("R", 2.0, t) == pytest.approx(2.0 + extra, rel=1e-3)   # all of it
    assert math.isnan(m.roots_flow("R", float("nan"), t))


def test_manometer_lo_follows_the_cell_and_saturates():
    params = load_params()
    m = TargetModel(flows_record(), 60.0, params)
    assert m.lo_cell_pressure(0.12, 600.0) == pytest.approx(m.cell_pressure(0.12, 600.0))
    m.inputs.coldboard_heat_W = 0.1
    t = 600.0 + 10 * params["assumed"]["bath"]["tau_s"]
    assert m.lo_cell_pressure(0.12, t) > 0.12                        # vapor pressure follows the bath
    m.inputs.quench = True
    t2 = t + 5 * params["assumed"]["quench"]["rise_tau_s"]
    assert m.cell_pressure(0.12, t2) > MANO_LO_FULL_SCALE_TORR
    assert math.isnan(m.lo_cell_pressure(0.12, t2))                  # beyond the 10 Torr head


def test_model_provider_routes_the_new_instruments():
    from cooldown_sim.clock import SimClock
    tl = flows_record()
    cmap = channel_map.load()
    clock = SimClock(600.0, running=False)
    prov = ModelProvider(tl, cmap, clock, t0=60.0, noise_rel=0.0)
    assert prov.recorded("manometer_low", "Mano", 600.0) == pytest.approx(
        prov.model.lo_cell_pressure(0.12, 600.0))
    prov.model.inputs.coldboard_heat_W = 0.1
    t = 600.0 + 20 * prov.model.heat["flow_tau_s"]
    assert prov.recorded("flowmeter_left", "LPump", t) > 6.0
    assert prov.recorded("flowmeter_pi", "RPump", t) > 2.0
