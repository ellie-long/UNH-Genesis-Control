"""magnet_ioc.device: the Genesis IOC wrapper, against the IPS120 emulator.

Uses the softioc stub (no EPICS), the canonical settings.yaml with the
endpoint pointed at the emulator, and a fake cryogenics fetch.
"""

import asyncio
import importlib
import json
import time
from pathlib import Path

import pytest
import yaml

from cooldown_sim.emulators.base import LoopbackServer
from cooldown_sim.emulators.ips120 import IPS120
from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "configs" / "magnet_physical" / "ips120" / "settings.yaml"


def healthy(cfg):
    async def fetch(pvs):
        from magnet_ioc import interlocks
        now = time.time()
        vals = {cfg.lhe_pv: 25.0, cfg.shells_pv: 3e-7, cfg.fringe_pv: 1.25e-3}
        vals.update({pv: 4.2 for pv in cfg.magnet_temps.values()})
        vals.update({pv: now - 1 for pv in cfg.heartbeats.values()})
        return {pv: interlocks.Reading(v, now - 0.5) for pv, v in vals.items()}
    return fetch


@pytest.fixture
def device(tmp_path):
    made = []

    def make(write_enabled=False, emu=None, **extra):
        emu = emu or IPS120(time_scale=50.0)
        server = LoopbackServer(emu).start()
        settings = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["ips120"]
        settings.update(ip="127.0.0.1", port=server.port, timeout=2.0, write_enabled=write_enabled,
                        magnet_event_dir=str(tmp_path / "events"), heater_wait_s=0.2, poll_s=0.05,
                        **extra)
        stub_ctx = softioc_stub.installed()
        stub = stub_ctx.__enter__()
        mod = importlib.import_module("magnet_ioc.device")
        dev = mod.Device("UNH:GEN:MAG", settings)
        dev.permit._fetch = healthy(dev.permit_cfg)
        dev.connect()
        made.append((dev, server, stub_ctx))
        return dev, emu, stub.builder.records
    yield make
    for dev, server, ctx in made:
        dev._close_connection()
        server.stop()
        ctx.__exit__(None, None, None)


def test_every_settings_record_is_a_pv(device):
    dev, _, recs = device()
    listed = set(yaml.safe_load(SETTINGS.read_text())["ips120"]["records"])
    assert listed <= set(dev.pvs), sorted(listed - set(dev.pvs))
    for name in ("RAMP", "HOLD", "HEATER_ON_CMD", "HEATER_OFF_CMD", "MATCH", "OUTAGE"):
        assert name in dev.pvs


def test_start_up_sends_no_command(device):
    _, emu, _ = device()
    sent = [e.request.decode().strip() for e in emu.exchanges]
    assert sent and all(s[:1] in "VRX" for s in sent), sent       # reads only (issue 18)


def test_poll_publishes_field_from_the_nmr_equation(device):
    dev, emu, recs = device()
    emu.output = emu.magnet = 48.666
    assert asyncio.run(dev.do_reads())
    assert recs["COMM"].value == 1 and recs["HEATER_ON"].value == 1
    assert recs["MAGNET_CURRENT"].value == pytest.approx(48.666)
    assert recs["FIELD"].value == pytest.approx(0.1027405 * 48.666 + 0.00003497875)
    assert recs["NMR_D"].value == pytest.approx(recs["FIELD"].value * 6.53590365)
    assert recs["FIELD_SOURCE"].value.startswith("supply")
    assert recs["INDUCTANCE"].value == 38.0 and recs["PERMIT"].value == 1


def test_helium_and_gaussmeter_come_from_the_genesis_iocs(device):
    # V4 had both typed in; Ells: read them from the existing IOCs
    dev, _, recs = device()
    asyncio.run(dev.do_reads())
    assert recs["HELIUM_LEVEL"].value == 25.0 and recs["HELIUM_LOW_WARNING"].value == 0
    assert recs["FRINGE_FIELD_T"].value == pytest.approx(1.25e-3)
    assert recs["FRINGE_FIELD_G"].value == pytest.approx(12.5)
    assert recs["SYSTEM_CHECK_STATE"].value == "SYSTEM READY"
    assert recs["INDUCTANCE_CHECK"].value.startswith("OK") and recs["XMN"].value == "X00"
    for name in ("HELIUM_LEVEL", "FRINGE_FIELD_G", "FRINGE_FIELD_T"):
        assert not hasattr(recs[name], "on_update") or recs[name].on_update is None


def test_low_helium_warns(device):
    dev, _, recs = device()

    async def low(pvs):
        from magnet_ioc import interlocks
        now = time.time()
        return {dev.permit_cfg.lhe_pv: interlocks.Reading(18.5, now)}
    dev.permit._fetch = low
    asyncio.run(dev.do_reads())
    assert recs["HELIUM_LOW_WARNING"].value == 1 and recs["PERMIT"].value == 0
    assert recs["SYSTEM_CHECK_STATE"].value == "WARNING - LOW HELIUM LEVEL"


def test_every_poll_is_logged_to_csv(device, tmp_path):
    dev, emu, recs = device()
    emu.output = emu.magnet = 3.0
    asyncio.run(dev.do_reads())
    asyncio.run(dev.do_reads())
    rows = [line.split(",") for f in (tmp_path / "events").glob("magnet_log_*.csv")
            for line in f.read_text().splitlines()]
    assert rows[0][:5] == ["DateTime", "Current_A", "Voltage_V", "Xmn", "Xmn_Meaning"]
    assert len(rows) == 3 and rows[-1][1] == "3.0000" and rows[-1][3] == "X00"


def test_inductance_bypass_starts_off_and_is_recorded(device):
    dev, _, recs = device()
    assert recs["IGNORE_INDUCTANCE"].value == 0 and dev.sequencer.inductance_override is False
    dev._set_ignore_inductance(1)
    assert dev.sequencer.inductance_override and recs["INDUCTANCE_CHECK"].value == "BYPASS"
    assert recs["LAST_EVENT"].value.startswith("inductance: R24 check BYPASSED")


def test_persistent_field_comes_from_the_persistent_current(device):
    # issue 9: with the supply at zero and the magnet persistent at field, the
    # field must not read 0
    emu = IPS120(time_scale=50.0, initial_current_a=40.0, initial_persistent=True)
    emu.output = 0.0
    dev, _, recs = device(emu=emu)
    asyncio.run(dev.do_reads())
    assert recs["CURRENT"].value == 0.0 and recs["FIELD_SOURCE"].value == "persistent"
    assert recs["FIELD"].value == pytest.approx(0.1027405 * 40.0 + 0.00003497875)


def test_commands_are_refused_while_writes_are_disabled(device):
    dev, emu, recs = device(write_enabled=False)
    recs["TARGET_CURRENT"].set(5.0)
    asyncio.run(dev._cmd_ramp(1))
    asyncio.run(dev._cmd_outage(1))
    assert not [e for e in emu.exchanges if e.request.decode().strip()[:1] in "AHIS"]
    assert recs["LAST_EVENT"].value.startswith("refused")


def test_ramp_command_runs_and_is_logged(device, tmp_path):
    dev, emu, recs = device(write_enabled=True)

    async def go():
        recs["TARGET_CURRENT"].set(2.0)
        await dev._cmd_ramp(1)
        while dev.sequencer.busy:
            await dev.do_reads()
            await asyncio.sleep(0.02)
        await dev.do_reads()
    asyncio.run(asyncio.wait_for(go(), timeout=120))
    assert recs["STATE"].value == "COMPLETE / HOLD", recs["LAST_EVENT"].value
    assert recs["CURRENT"].value == pytest.approx(2.0, abs=0.011)
    rows = [json.loads(line) for f in (tmp_path / "events").glob("*.jsonl")
            for line in f.read_text().splitlines()]
    kinds = [r["kind"] for r in rows]
    assert "request" in kinds and "segment" in kinds and rows[-1]["message"].startswith("ramp complete")


def test_target_field_sets_the_target_current(device):
    dev, _, recs = device()
    dev._set_target_field(5.0)
    assert recs["TARGET_CURRENT"].value == pytest.approx((5.0 - 0.00003497875) / 0.1027405, abs=1e-4)


def test_a_quench_is_reported_and_recorded(device, tmp_path):
    dev, emu, recs = device(write_enabled=True)
    emu.output = emu.magnet = 30.0
    emu.quench()
    asyncio.run(dev.do_reads())
    assert recs["QUENCH"].value == 1 and recs["SYSTEM"].value == "QUENCHED"
    rows = [json.loads(line) for f in (tmp_path / "events").glob("*.jsonl")
            for line in f.read_text().splitlines()]
    assert any(r["kind"] == "fault" for r in rows)
