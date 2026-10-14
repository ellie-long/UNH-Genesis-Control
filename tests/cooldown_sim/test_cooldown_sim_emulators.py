"""Driver conformance: the REAL Genesis cryo drivers against cooldown_sim emulators.

Each test builds the unchanged driver ``Device`` from target_ioc.instruments,
points it at an emulator on an ephemeral 127.0.0.1 port, runs the driver's own
connect()/do_reads(), and asserts the PV values and alarm/COMM states.  The
softioc stub stands in for EPICS records; nothing touches the EPICS network.
"""

import asyncio
import contextlib
import functools
import importlib
import math
import time
from pathlib import Path

import pytest
import yaml

from cooldown_sim import conversions
from cooldown_sim.emulators import LoopbackServer
from cooldown_sim.emulators.cryo import IONIVAC, LS224, LS455, MKS902B, TPG262
from cooldown_sim.emulators.scpi_supply import ScpiSupply
from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[2]


def profile(name):
    return yaml.safe_load((ROOT / "configs/target_physical" / name / "settings.yaml").read_text())[name]


@contextlib.contextmanager
def real_driver(module, settings, emulator, timeout=0.5):
    with softioc_stub.installed():
        mod = importlib.import_module(f"target_ioc.instruments.{module}")
        with LoopbackServer(emulator) as srv:
            s = dict(settings, ip="127.0.0.1", port=srv.port, timeout=timeout)
            dev = mod.Device("UNH:SIMTEST", s)
            dev.connect()
            yield dev, srv, mod


def read(dev):
    return asyncio.run(dev.do_reads())


def value(dev, pv):
    return dev.pvs[pv].get()


def assert_read_failed(dev, channel_pv):
    """What the live drivers do when an instrument behind a reachable gateway
    stops answering properly: the poll fails, the channel PVs take a READ
    alarm, and LAST_OK stops advancing.  COMM does NOT stay 'Disconnected':
    the driver reconnects at once and the gateway's TCP port still accepts,
    so COMM returns to 1 and ERROR is cleared (see known_issues.yaml,
    comm-flag-tracks-gateway)."""
    last_ok = value(dev, "LAST_OK")
    assert read(dev) is False
    assert dev.pvs[channel_pv].severity == 1, channel_pv
    assert value(dev, "LAST_OK") == last_ok


# ------------------------------------------------------------------- LS224
def ls224_emulator(settings, temps, mod):
    slots = dict(zip(settings["slots"], settings["channels"]))
    cal = mod.load_calibration(ROOT / settings["calibration"])
    inverse = {slot: conversions.InverseCalibration(functools.partial(mod.resistance_to_kelvin, cal[slot]))
               for slot in slots if slot in cal}
    return LS224(slots, lambda ch: temps.get(ch, float("nan")), inverse)


@pytest.mark.parametrize("ioc", ["ls224_fridge", "ls224_magnet"])
def test_ls224_calibrated_round_trip(ioc, monkeypatch):
    monkeypatch.chdir(ROOT)                          # driver resolves calibration vs cwd
    settings = profile(ioc)
    temps = {ch: 1.2 + 3.7 * i for i, ch in enumerate(settings["channels"])}
    with softioc_stub.installed():
        mod = importlib.import_module("target_ioc.instruments.ls224")
        emu = ls224_emulator(settings, temps, mod)
    with real_driver("ls224", settings, emu) as (dev, _srv, _mod):
        assert value(dev, "COMM") == 1
        assert read(dev) is True
        for ch, kelvin in temps.items():
            assert value(dev, f"{ch}_TI") == pytest.approx(kelvin, rel=1e-5), ch
            assert value(dev, f"{ch}_Status") == "OK"
            assert value(dev, f"{ch}_RI") > 0
        assert "MODEL224" in value(dev, f"{settings['channels'][0]}_IDN")
        assert emu.writes() == []


def test_ls224_missing_value_is_flagged_invalid_not_invented(monkeypatch):
    monkeypatch.chdir(ROOT)
    settings = profile("ls224_fridge")
    temps = {"F1": 4.2}                              # every other channel absent
    with softioc_stub.installed():
        mod = importlib.import_module("target_ioc.instruments.ls224")
        emu = ls224_emulator(settings, temps, mod)
    with real_driver("ls224", settings, emu) as (dev, _srv, _mod):
        assert read(dev) is True
        assert value(dev, "F1_TI") == pytest.approx(4.2, rel=1e-5)
        assert math.isnan(value(dev, "F2_TI"))
        assert value(dev, "F2_Status") == "invalid reading"


def test_ls224_silent_then_recovers(monkeypatch):
    monkeypatch.chdir(ROOT)
    settings = profile("ls224_fridge")
    temps = {ch: 4.0 for ch in settings["channels"]}
    with softioc_stub.installed():
        mod = importlib.import_module("target_ioc.instruments.ls224")
        emu = ls224_emulator(settings, temps, mod)
    with real_driver("ls224", settings, emu, timeout=0.3) as (dev, srv, _mod):
        assert read(dev) is True
        srv.set_fault("silent")
        assert_read_failed(dev, "F1_TI")
        srv.set_fault("none")
        dev._reconnect_due = 0.0                        # skip the 2 s backoff
        dev.reconnect()
        assert read(dev) is True and value(dev, "COMM") == 1
        assert dev.pvs["F1_TI"].severity == 0


# ---------------------------------------------------------------- MKS 902B
def test_mks902b_reads_torr_and_reports_nak(monkeypatch):
    settings = profile("mks902b")
    state = {"Cell": 9.25}
    emu = MKS902B("Cell", lambda ch: state.get(ch, float("nan")), address=settings["address"])
    with real_driver("mks902b", settings, emu) as (dev, srv, _mod):
        assert read(dev) is True
        assert value(dev, "Cell_PI") == pytest.approx(9.25, rel=1e-3)
        assert value(dev, "Cell_Status") == "OK"
        assert "902B" in value(dev, "Cell_IDN")
        state["Cell"] = float("nan")                    # record has no value now
        assert read(dev) is True                        # a NAK is not a comm failure
        assert math.isnan(value(dev, "Cell_PI"))
        assert value(dev, "Cell_Status") == f"NAK {MKS902B.NAK_NO_VALUE}"
        srv.set_fault("garbage")
        assert_read_failed(dev, "Cell_PI")


# ---------------------------------------------------------------- TPG 262
def test_tpg262_handshake_units_and_status():
    settings = profile("tpg262")
    state = {"Pump": 0.35, "OVC": float("nan")}
    emu = TPG262(tuple(settings["channels"]), lambda ch: state[ch])
    with real_driver("tpg262", settings, emu) as (dev, srv, _mod):
        assert value(dev, "Pump_Unit") == "mbar"
        assert read(dev) is True
        assert value(dev, "Pump_PI") == pytest.approx(0.35 * conversions.TORR_TO_MBAR, rel=1e-4)
        assert value(dev, "Pump_Status") == "OK"
        assert math.isnan(value(dev, "OVC_PI"))
        assert value(dev, "OVC_Status") == "Sensor off"
        srv.set_fault("nak")
        assert_read_failed(dev, "Pump_PI")


# ---------------------------------------------------------------- IONIVAC
def test_ionivac_stream_and_silence():
    settings = profile("ionivac")
    state = {"Ion": 3.2e-7}
    emu = IONIVAC("Ion", lambda ch: state[ch])
    with real_driver("ionivac", settings, emu) as (dev, _srv, _mod):
        time.sleep(0.35)
        assert read(dev) is True
        assert value(dev, "Ion_PI") == pytest.approx(3.2e-7, rel=conversions.IONIVAC_REL_QUANTUM * 1.01)
        state["Ion"] = float("nan")                     # gauge goes quiet
        time.sleep(0.25)
        dev.t.read_available()                          # drop frames sent before it went quiet
        time.sleep(0.25)
        assert_read_failed(dev, "Ion_PI")
        assert "sent nothing" in value(dev, "LAST_EVENT") or dev.pvs["Ion_PI"].severity == 1


# ---------------------------------------------------------------- LS455
def test_ls455_gauss_to_tesla():
    settings = profile("ls455")
    emu = LS455("Fringe", lambda ch: 1.04782)
    with real_driver("ls455", settings, emu) as (dev, _srv, _mod):
        assert read(dev) is True
        assert value(dev, "Fringe_Field") == pytest.approx(1.04782)
        assert value(dev, "Fringe_Unit") == "G"
        assert value(dev, "Fringe_FieldT") == pytest.approx(1.04782e-4)


def test_refused_instrument_reports_comm_loss():
    """Unlike a silent gauge, a refused connection DOES hold COMM at 0."""
    settings = profile("ls455")
    emu = LS455("Fringe", lambda ch: 1.0)
    emu.fault.set("absent")
    with real_driver("ls455", settings, emu) as (dev, _srv, _mod):
        assert value(dev, "COMM") == 0
        assert read(dev) is False
        assert value(dev, "COMM") == 0 and value(dev, "ERROR")


# ------------------------------------------------------- coldboard SPD1000X
def test_coldboard_read_only_profile_sends_no_writes():
    settings = profile("siglent_coldboard")
    assert settings["write_enabled"] is False
    emu = ScpiSupply("SPD1168X", voltage=0.0, current=0.0, output=False, load_ohms=10.0)
    with real_driver("spd1000x", settings, emu) as (dev, _srv, _mod):
        assert read(dev) is True
        assert value(dev, "Coldboard_Conn") == 1
        assert value(dev, "Coldboard_VI") == 0.0 and value(dev, "Coldboard_Mode") == 0
        # an operator caput on the read-only IOC must not reach the supply
        dev.do_sets(5.0, "UNH:SIMTEST:Coldboard_VC")
        dev.do_sets(1, "UNH:SIMTEST:Coldboard_Mode")
        assert read(dev) is True
        assert emu.writes() == []
        assert "read-only" in value(dev, "LAST_EVENT")


def test_supply_physics_and_status_word():
    emu = ScpiSupply("SPD1305X", voltage=5.0, current=0.1, output=True, load_ohms=1e4)
    v, i, cc = emu.measured()
    assert (v, cc) == (5.0, False) and i == pytest.approx(5e-4)
    emu.set_state(load_ohms=10.0)                       # 0.5 A wanted, 0.1 A allowed -> CC
    v, i, cc = emu.measured()
    assert cc and i == 0.1 and v == pytest.approx(1.0)
    assert emu.respond(b"SYSTem:STATus?\n") == b"0x11\n"
    assert int(emu.respond(b"SYSTem:STATus?\n").decode().strip(), 0) & 0x10
    emu.handle(b"OUTPut CH1,OFF\n")
    assert emu.measured() == (0.0, 0.0, False)
    assert [w.request for w in emu.writes()] == [b"OUTPut CH1,OFF\n"]


def test_emulators_refuse_non_loopback_bind():
    with pytest.raises(ValueError):
        LoopbackServer(LS455("Fringe", lambda ch: 1.0), host="0.0.0.0")
