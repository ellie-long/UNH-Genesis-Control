"""ssRF leg: burn replay recipes and waveforms pass the REAL sequencer's own
validators, and observed RF applies heat and a tensor hole to the target."""

import math

import numpy as np
import pytest

from cooldown_sim.autopilot import ssrf as leg
from cooldown_sim.autopilot.ca import OperatorAllowlist
from cooldown_sim.data.blast_log import Burn
from cooldown_sim.model import physics as ph
from cooldown_sim.model.target import TargetModel
from genesis_common.ssrf_waveform_contract import validate_normalized_waveform
from logic_devices.ssrf_sequence_core import BurnParameters, validate_parameters

from test_cooldown_sim_model import B, record   # noqa: E402


def burn(**kw):
    base = dict(start=0.0, end=1.0, centre_mhz=32.283, fm_dev_khz=1000.0, step_db=98.0,
                sweep_min_dbm=-120.0, sweep_max_dbm=-22.0, dwell_ms=10.0, status="Success")
    base.update(kw)
    return Burn(**base)


def params(recipe):
    return BurnParameters(carrier_mhz=recipe["CARRIER_MHZ"], start_level_dbm=recipe["START_LEVEL_DBM"],
                          target_level_dbm=recipe["TARGET_LEVEL_DBM"], fm_deviation_khz=recipe["FM_DEV_KHZ"],
                          fm_period_s=recipe["FM_PERIOD_S"], vhigh_v=recipe["VHIGH_V"],
                          vlow_v=recipe["VLOW_V"], fm_repeats=recipe["FM_REPEATS"])


@pytest.mark.parametrize("b", [
    burn(), burn(dwell_ms=10000.0, sweep_max_dbm=3.0), burn(fm_dev_khz=9.0, sweep_max_dbm=-22.0),
    burn(fm_dev_khz=float("nan"), sweep_min_dbm=float("nan"), sweep_max_dbm=float("nan"),
         dwell_ms=float("nan")),                          # a 'Failed' row's empty fields
    burn(sweep_min_dbm=-10.0, sweep_max_dbm=-20.0),       # inverted limits
])
def test_recipe_passes_the_sequencer_validator(b):
    ok, why = validate_parameters(params(leg.recipe_from_burn(b)))
    assert ok, why


def test_recipe_preserves_dwell_and_levels():
    r = leg.recipe_from_burn(burn(dwell_ms=10000.0, sweep_min_dbm=-120.0, sweep_max_dbm=3.0))
    p = params(r)
    assert p.sweep_dwell_ms == pytest.approx(10000.0)
    assert (r["START_LEVEL_DBM"], r["TARGET_LEVEL_DBM"]) == (-120.0, 3.0)
    assert (r["VHIGH_V"], r["VLOW_V"]) == (0.5, -0.5)       # validated physical WaveGen limits


def test_staged_waveform_passes_the_contract():
    w = validate_normalized_waveform(leg.stage_waveform())
    assert len(w) == 8192 and w.min() >= -1 and w.max() <= 1


def test_autopilot_may_write_exactly_the_burn_workflow():
    allow = OperatorAllowlist()
    for pv in ("CARRIER_MHZ", "WAVEFORM_STAGE_REQ", "ARM_CMD", "START_CMD", "PREPARE_CMD"):
        assert allow.permits(f"UNH:GEN:SSRF:SEQ:{pv}"), pv
    for pv in ("RF_ON_CMD", "FM_ON_CMD", "MANUAL_ARM_CMD", "ABORT_CMD", "DISARM_CMD"):
        assert not allow.permits(f"UNH:GEN:SSRF:SEQ:{pv}"), pv
    assert not allow.permits("UNH:GEN:SSRF:GEN:RF_CMD")


class _Engine:
    def __init__(self, model):
        class P:
            pass
        self.provider = P()
        self.provider.model = model

        class Clock:
            t = 100.0
            def now(self):
                return self.t
        self.clock = Clock()

        class Ev:
            rows = []
            def write(self, t, kind, detail):
                self.rows.append((kind, detail))
        self.events = Ev()
        self.autopilot = {"ssrf": False}


def test_observed_rf_burns_a_hole_and_heats_the_cup():
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0)
    m.advance(100.0)
    eng = _Engine(m)
    s = leg.SSRFLeg(eng, ca=None)
    q0 = m.state.Q
    s._rf.update(on=1.0, level=-5.0, freq=ph.deuteron_larmor_MHz(B))
    s._observe()                                             # RF ON at t=100
    eng.clock.t = 110.0
    s._rf["on"] = 0.0
    s._observe()                                             # RF OFF at t=110: 10 s at -5 dBm
    m.advance(111.0)
    assert m.state.Q < q0 - 0.5 * q0 * (1 - math.exp(-0.5))  # ~40 % of Q burned
    assert m.burn_heat and m.burn_heat[0][2] == pytest.approx(leg.BURN_W_AT_M5_DBM)
    assert ph.physical(m.state.P, m.state.Q)


def test_off_resonance_rf_heats_but_does_not_burn():
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0)
    m.advance(100.0)
    eng = _Engine(m)
    s = leg.SSRFLeg(eng, ca=None)
    q0 = m.state.Q
    s._rf.update(on=1.0, level=-15.0, freq=ph.deuteron_larmor_MHz(B) + 2.0)
    s._observe()
    eng.clock.t = 110.0
    s._rf["on"] = 0.0
    s._observe()
    m.advance(111.0)
    assert m.state.Q == pytest.approx(q0, rel=0.02)
    assert m.burn_heat[0][2] == pytest.approx(leg.BURN_W_AT_M5_DBM * 0.1)   # 10 dB lower
