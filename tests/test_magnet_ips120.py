"""magnet_ioc: the IPS120-10 protocol, the operations, and the cryogenics permit.

The operations run against the IPS120 emulator (cooldown_sim/emulators/ips120.py)
through a real loopback socket and Genesis's SerialTcpConnection.  The
emulator and the sequencer share one clock scaled by SCALE, so a 25 A ramp at
3 A/min and the 20 s heater waits finish in a few seconds of test time.
"""

import asyncio
import math
import time

import pytest

from cooldown_sim.emulators.base import LoopbackServer
from cooldown_sim.emulators.ips120 import IPS120
from genesis_common.serial_tcp_base import SerialTcpConnection
from magnet_ioc import interlocks
from magnet_ioc.ips120_protocol import (IPSClient, IPSError, decode_status, decode_xmn,
                                        format_current, parse_numeric_response)
from magnet_ioc.ips120_sequences import (MagnetConfig, MagnetSequencer, choose_sweep_rate,
                                         next_ramp_target)

SCALE = 200.0
BANDS = ((20.0, 3.0), (40.0, 2.0), (65.0, 1.0))


def scaled_clock():
    return time.monotonic() * SCALE


async def scaled_sleep(seconds):
    await asyncio.sleep(max(0.0, seconds) / SCALE)


class Rig:
    """An emulator on a loopback port, a client, a sequencer and its log."""

    def __init__(self, emu=None, permit=(True, "OK"), **cfg):
        self.emu = emu or IPS120(clock=time.monotonic, time_scale=SCALE)
        self.server = LoopbackServer(self.emu).start()
        t = SerialTcpConnection("127.0.0.1", self.server.port, timeout=2.0)
        self.ips = IPSClient(t)
        self.events, self.states, self.permit_value = [], [], permit
        # the 90 s inductance window: Windows' ~16 ms timer, scaled 200x, is ~3 s
        # of jitter per sample; the live default is 30 s
        self.cfg = MagnetConfig(**{"heater_wait_s": 20.0, "poll_s": 1.0,
                                   "measured_inductance_window_s": 90.0, **cfg})
        self.seq = MagnetSequencer(self.ips, self.cfg, permit=self._permit,
                                   event=lambda k, m, **d: self.events.append((k, m)),
                                   state=self.states.append, sleep=scaled_sleep, clock=scaled_clock)

    async def _permit(self):
        return self.permit_value

    def commands(self):
        """Control commands the supply received (no reads)."""
        out = []
        for e in self.emu.exchanges:
            text = e.request.decode().strip()
            if text[:1] in "AHISC":
                out.append(text)
        return out

    def close(self):
        self.ips.close()
        self.server.stop()


@pytest.fixture
def rig():
    rigs = []

    def make(**kw):
        r = Rig(**kw)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        r.close()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=60))


async def finish(seq):
    while seq.busy:
        await asyncio.sleep(0.005)


# ------------------------------------------------------------------ protocol
def test_status_word_decodes_every_field():
    st = decode_status("X10A1C3H1M01P00")
    assert st.quenched and st.fault and st.heater_on and st.activity == "1"
    assert st.text()["system"] == "QUENCHED" and st.text()["control"] == "REMOTE / UNLOCKED"
    assert decode_status("X00A4C0H2M00P00").clamped
    with pytest.raises(IPSError):
        decode_status("X00A4C0H2")
    # found by their letters, so trailing characters still decode
    assert decode_status("X00A0C3H0M00P000").heater_off


def test_xmn_meaning_follows_the_manual_not_v4s_numbering():
    # p32: m = 1 / 2 / 4 / 8 and n = 1 / 2 / 4 / 8 (V4 numbers them 1-4)
    assert decode_xmn("X00A0C3H0M00P00") == "NORMAL | NORMAL"
    assert decode_xmn("X40A0C3H0M00P00").startswith("WARMING UP")
    assert decode_xmn("X80A0C3H0M00P00").startswith("FAULT")
    assert decode_xmn("X08A0C3H0M00P00").endswith("OUTSIDE POSITIVE CURRENT LIMIT")


def test_numbers_parse_and_format_fixed_point():
    assert parse_numeric_response("R+00.600") == 0.6
    assert parse_numeric_response("R+48.666") == 48.666
    assert format_current(1e-5) == "0.000" and format_current(48.66600000000001) == "48.666"
    with pytest.raises(ValueError):
        format_current(float("nan"))


def test_client_checks_the_echo_and_refusals(rig):
    r = rig()
    assert r.ips.read_status().raw.startswith("X00A0C3")
    assert r.ips.read_inductance() == 38.0
    r.emu.control = 0                          # LOCAL: control commands refused
    with pytest.raises(IPSError):
        r.ips.to_setpoint()


# ------------------------------------------------------------------ the band rates (issue 1)
def test_segments_split_at_the_band_edges():
    assert next_ramp_target(10.0, 45.0, 0.01, BANDS) == 20.0
    assert next_ramp_target(20.0, 45.0, 0.01, BANDS) == 40.0
    assert next_ramp_target(45.0, 10.0, 0.01, BANDS) == 40.0


def test_rate_comes_from_the_segment_not_the_reading():
    # the student's bug: at 19.995 A the 20-40 A segment got 3 A/min
    assert choose_sweep_rate(19.995, 40.0, BANDS) == 2.0
    assert choose_sweep_rate(39.995, 45.0, BANDS) == 1.0
    assert choose_sweep_rate(0.0, 20.0, BANDS) == 3.0


def test_band_table_can_never_exceed_the_cap():
    with pytest.raises(ValueError):
        MagnetConfig(rate_bands=((20.0, 4.0), (65.0, 1.0))).validate()
    with pytest.raises(ValueError):
        MagnetConfig.from_settings({"rate_bands": [[20, 3.0], [65, 30.0]]})


# ------------------------------------------------------------------ ramps
def test_ramp_refuses_in_persistent_mode(rig):
    # V4: a ramp never opens the switch; match and heater ON are operator steps
    r = rig()
    r.emu.heater_on = r.emu.switch_open = False
    r.emu.persistent_current = r.emu.output = r.emu.magnet = 10.0

    async def go():
        r.seq.start("ramp", r.seq.ramp, 25.0)
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("REFUSED: persistent mode"), r.states[-3:]
    assert not [c for c in r.commands() if c[:1] in "HIS"] and r.emu.output == 10.0


def test_v4_sequence_from_persistent_zero(rig):
    """Match, heater ON (H1, never H2), then one 3 A/min ramp, no segments."""
    r = rig()
    r.emu.heater_on = r.emu.switch_open = False      # persistent at zero
    r.emu.persistent_current = r.emu.output = r.emu.magnet = 0.0

    async def go():
        for name, fn, *args in (("match", r.seq.match), ("heater on", r.seq.heater_on),
                                ("ramp", r.seq.ramp, 25.0)):
            assert r.seq.start(name, fn, *args)
            await finish(r.seq)
    run(go())
    assert r.states[-1] == "COMPLETE / HOLD", r.events
    assert abs(r.emu.output - 25.0) < 0.011 and r.emu.switch_open
    cmds = r.commands()
    assert "H1" in cmds and "H2" not in cmds and cmds.index("H1") < cmds.index("S3.000")
    assert [c for c in cmds if c[:1] in "SI"] == ["S3.000", "I25.000"]


def test_heater_on_matches_the_supply_tightly_before_h1(rig):
    # V4: the supply's own H1 check needs R0 = R16 within 0.0005 A
    r = rig()
    r.emu.heater_on = r.emu.switch_open = False
    r.emu.persistent_current = r.emu.magnet = 30.0
    r.emu.output = 30.004

    async def go():
        r.seq.start("heater on", r.seq.heater_on)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "HEATER ON / HOLD", r.events
    cmds = r.commands()
    assert "I30.000" in cmds and cmds.index("I30.000") < cmds.index("H1")
    assert r.emu.switch_open and abs(r.emu.magnet - 30.0) < 1e-6


def test_v3_band_rates_are_still_a_setting(rig):
    r = rig(rate_bands=BANDS)

    async def go():
        r.seq.start("ramp", r.seq.ramp, 45.0)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "COMPLETE / HOLD", r.events
    rates = [c for c in r.commands() if c.startswith("S")]
    assert rates == ["S3.000", "S2.000", "S1.000"]


def test_losing_driven_mode_mid_ramp_holds(rig):
    # V4: the heater dropping out during a ramp stops it at once
    r = rig()

    async def go():
        r.seq.start("ramp", r.seq.ramp, 15.0)
        await asyncio.sleep(0.3)
        r.emu.heater_on = False                       # the heater lost power
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("STOPPED: driven mode lost"), r.states[-3:]
    assert r.emu.activity == 0 and r.emu.output < 15.0


@pytest.mark.parametrize("mode", ["setting", "measured"])
def test_heater_off_is_verified_whatever_r20_reports(rig, mode):
    r = rig(emu=IPS120(clock=time.monotonic, time_scale=SCALE, r20_mode=mode))
    r.emu.output = r.emu.magnet = r.emu.target = 12.0

    async def go():
        r.seq.start("heater off", r.seq.heater_off)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "PERSISTENT / SUPPLY HELD", r.events
    assert r.seq.r20_meaning == mode                  # "auto" decided it
    assert any("SWITCH HEATER OFF" in m or "switch closed" in m for _, m in r.events)


def test_heater_off_not_verified_when_measured_r20_stays_up(rig):
    r = rig(r20_meaning="measured",
            emu=IPS120(clock=time.monotonic, time_scale=SCALE, r20_mode="setting"))
    r.emu.output = r.emu.magnet = r.emu.target = 12.0

    async def go():
        r.seq.start("heater off", r.seq.heater_off)
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("STOPPED: heater OFF not verified"), r.states[-3:]


def test_permit_blocks_a_ramp_before_anything_moves(rig):
    r = rig(permit=(False, "magnet LHe 18.0 in < 20 in"))

    async def go():
        r.seq.start("ramp", r.seq.ramp, 10.0)
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("REFUSED")
    assert not [c for c in r.commands() if c[:1] in "AHIS"]


def test_losing_the_permit_mid_ramp_holds(rig):
    r = rig()

    async def go():
        r.seq.start("ramp", r.seq.ramp, 15.0)
        await asyncio.sleep(0.3)
        r.permit_value = (False, "cryo IOC lhe_magnet stale (75 s)")
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("STOPPED: blocked"), r.states[-3:]
    assert r.commands()[-1] == "A0" and r.emu.activity == 0
    assert 0.0 < r.emu.output < 15.0


def test_a_quench_stops_the_ramp(rig):
    r = rig()

    async def go():
        r.seq.start("ramp", r.seq.ramp, 15.0)
        await asyncio.sleep(0.3)
        r.emu.quench()
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("STOPPED"), r.states[-3:]
    # stopped on the status word, or a poll earlier on the current collapsing
    assert any("QUENCHED" in m or "current changing" in m for k, m in r.events if k == "stopped")
    assert r.emu.activity == 0


def test_wrong_stored_inductance_refuses(rig):
    r = rig(emu=IPS120(clock=time.monotonic, time_scale=SCALE, inductance_h=20.0))

    async def go():
        r.seq.start("ramp", r.seq.ramp, 5.0)
        await finish(r.seq)
    run(go())
    assert "inductance mismatch: R24 20 H" in r.states[-1]
    assert not [c for c in r.commands() if c[:1] in "HIS"]


@pytest.mark.parametrize("stored, ok", [(38.0, True), (37.75, True), (38.29, True),
                                        (37.6, False), (38.5, False)])
def test_inductance_tolerance_is_v4s_0_3_h(rig, stored, ok):
    r = rig(emu=IPS120(clock=time.monotonic, time_scale=SCALE, inductance_h=stored))
    assert run(r.seq.check_inductance()) is ok


def test_ignore_inductance_lets_a_ramp_through_and_hold_never_needs_it(rig):
    # R24 37 H: outside V4's 0.3 H, inside the 25 % seen while ramping
    r = rig(emu=IPS120(clock=time.monotonic, time_scale=SCALE, inductance_h=37.0))

    async def go():
        await r.seq.hold()                            # V4 blocks HOLD here; we never do
        assert r.states[-1] == "HOLD"
        r.seq.inductance_override = True
        r.seq.start("ramp", r.seq.ramp, 2.0)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "COMPLETE / HOLD", r.events
    assert any("bypassed" in m for _, m in r.events)


def test_bypass_does_not_switch_off_the_inductance_seen_while_ramping(rig):
    # the bypass is for R24 only: a magnet that really is 20 H is still caught
    r = rig(emu=IPS120(clock=time.monotonic, time_scale=SCALE, inductance_h=20.0))
    r.seq.inductance_override = True

    async def go():
        r.seq.start("ramp", r.seq.ramp, 10.0)          # long enough for the 90 s window
        await finish(r.seq)
    run(go())
    assert r.states[-1].startswith("STOPPED: inductance") and "while ramping" in r.events[-1][1]


def test_one_operation_at_a_time(rig):
    r = rig()

    async def go():
        assert r.seq.start("ramp", r.seq.ramp, 10.0)
        assert not r.seq.start("heater off", r.seq.heater_off)   # refused while ramping
        await r.seq.hold()
        await finish(r.seq)
    run(go())
    assert any(k == "refused" and "still running" in m for k, m in r.events)


def test_hold_stops_a_ramp(rig):
    r = rig()

    async def go():
        r.seq.start("ramp", r.seq.ramp, 30.0)
        await asyncio.sleep(0.3)
        await r.seq.hold()
        await finish(r.seq)
    run(go())
    assert r.emu.activity == 0 and r.emu.output < 30.0


# ------------------------------------------------------------------ persistent mode
def test_heater_off_keeps_the_supply_energized_this_cooldown(rig):
    r = rig()
    r.emu.output = r.emu.magnet = r.emu.target = 30.0

    async def go():
        r.seq.start("heater off", r.seq.heater_off)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "PERSISTENT / SUPPLY HELD", r.events
    assert not r.emu.switch_open and abs(r.emu.persistent_current - 30.0) < 1e-6
    assert "A2" not in r.commands() and abs(r.emu.output - 30.0) < 1e-6


def test_run_down_setting_takes_the_leads_to_zero(rig):
    r = rig(persistent_supply="run_down")
    r.emu.output = r.emu.magnet = r.emu.target = 30.0

    async def go():
        r.seq.start("heater off", r.seq.heater_off)
        await finish(r.seq)
    run(go())
    assert "A2" in r.commands() and abs(r.emu.output) < 0.011
    assert abs(r.emu.persistent_current - 30.0) < 1e-6         # the magnet stays at field


def test_match_brings_the_supply_to_the_persistent_current(rig):
    r = rig()
    r.emu.heater_on = r.emu.switch_open = False
    r.emu.persistent_current = r.emu.magnet = 30.0
    r.emu.output = 0.0

    async def go():
        r.seq.start("match", r.seq.match)
        await finish(r.seq)
    run(go())
    assert r.states[-1] == "SUPPLY MATCHED / HOLD", r.events
    assert abs(r.emu.output - 30.0) < 0.011 and not r.emu.switch_open


# ------------------------------------------------------------------ outage (issue 19)
def test_outage_button_makes_a_driven_magnet_persistent(rig):
    r = rig()
    r.emu.output = r.emu.magnet = r.emu.target = 20.0

    async def go():
        r.seq.start("ramp", r.seq.ramp, 30.0)
        await asyncio.sleep(0.2)
        await r.seq.outage()
    run(go())
    assert r.states[-1] == "SAFE FOR POWER OUTAGE", r.events
    assert not r.emu.switch_open and r.emu.activity == 0
    assert "A2" not in r.commands()                              # leads held (this cool-down)


def test_power_cycle_leaves_it_clamped_and_hold_does_not_unclamp_it(rig):
    r = rig()
    r.emu.power_cycle()
    assert r.ips.read_status().clamped

    async def go():
        await r.seq.hold()
    run(go())
    assert r.emu.activity == 4 and r.states[-1] == "CLAMPED"


# ------------------------------------------------------------------ the cryogenics permit
def _readings(now, **over):
    cfg = interlocks.PermitConfig()
    vals = {cfg.lhe_pv: 25.0, cfg.shells_pv: 3e-7, cfg.magnet_temps["M4"]: 4.2,
            cfg.magnet_temps["M5"]: 4.2}
    vals.update({pv: now - 2.0 for pv in cfg.heartbeats.values()})
    vals.update(over)
    return cfg, {pv: interlocks.Reading(v, now - 1.0) for pv, v in vals.items() if v is not None}


def test_permit_passes_when_the_cryogenics_are_healthy():
    now = 1000.0
    cfg, rd = _readings(now)
    assert interlocks.evaluate(cfg, rd, now) == (True, "OK")


@pytest.mark.parametrize("override, expect", [
    ({"UNH:GEN:CRYO:MAG:LHE:Magnet_LI": 18.0}, "magnet LHe 18.0 in < 20 in"),
    ({"UNH:GEN:CRYO:MAG:LHE:Magnet_LI": None}, "magnet LHe level not available"),
    ({"UNH:GEN:CRYO:MAG:LHE:MAN:lhe_magnet_time": 800.0}, "cryo IOC lhe_magnet stale"),
    ({"UNH:GEN:CRYO:MAG:MAN:ls224_magnet_time": None}, "cryo IOC ls224_magnet not running"),
    ({"UNH:GEN:CRYO:MAG:M4_TI": 7.0}, "magnet M4 7.00 K > 6 K"),
    ({"UNH:GEN:CRYO:ION:Ion_PI": 2e-5}, "shells vacuum 2.0e-05 Torr > 1e-05"),
])
def test_permit_blocks(override, expect):
    now = 1000.0
    cfg, rd = _readings(now, **override)
    ok, reason = interlocks.evaluate(cfg, rd, now)
    assert not ok and reason.startswith(expect), reason


def test_expert_mode_allows_down_to_16_inches():
    now = 1000.0
    cfg, rd = _readings(now, **{"UNH:GEN:CRYO:MAG:LHE:Magnet_LI": 17.0})
    assert not interlocks.evaluate(cfg, rd, now, expert=False)[0]
    assert interlocks.evaluate(cfg, rd, now, expert=True) == (True, "OK")
    cfg, rd = _readings(now, **{"UNH:GEN:CRYO:MAG:LHE:Magnet_LI": 15.0})
    assert not interlocks.evaluate(cfg, rd, now, expert=True)[0]


def test_warm_magnet_uses_the_warm_vacuum_limit():
    now = 1000.0
    cfg, rd = _readings(now, **{"UNH:GEN:CRYO:ION:Ion_PI": 5e-5, "UNH:GEN:CRYO:MAG:M5_TI": 5.0})
    assert not interlocks.evaluate(cfg, rd, now)[0]              # cold bottom: 1e-5
    cfg.magnet_temp_max_k = 400.0
    cfg, rd2 = cfg, {**rd, cfg.magnet_temps["M5"]: interlocks.Reading(300.0, now - 1)}
    assert interlocks.evaluate(cfg, rd2, now)[0]                 # warm: 1e-4


def test_failed_fetch_blocks():
    async def broken(pvs):
        raise OSError("no CA")
    permit = interlocks.CryoPermit(interlocks.PermitConfig(), fetch=broken)
    ok, reason = asyncio.run(permit.check())
    assert not ok and "failed" in reason
