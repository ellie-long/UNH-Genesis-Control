"""Synthetic (data-free) cool-downs: deterministic, designed, and readable by
everything that reads a real record."""

import math

import numpy as np
import pytest

from cooldown_sim import channel_map
from cooldown_sim.model import physics as ph
from cooldown_sim.synth import cooldown as sc


def test_deterministic_for_a_seed():
    a, b = sc.make(seed=3), sc.make(seed=3)
    np.testing.assert_array_equal(a.timeline.table.numeric["F5"], b.timeline.table.numeric["F5"])
    assert not np.array_equal(sc.make(seed=4).timeline.table.numeric["F5"],
                              a.timeline.table.numeric["F5"])


def test_designed_schedule():
    s = sc.make(seed=0)
    tl = s.timeline
    assert tl.value_at("mw_rf_status", s.t0 + 0.5 * 3600) == 0
    assert tl.value_at("mw_rf_status", s.t0 + 2 * 3600) == 1
    assert tl.value_at("mw_freq_ghz", s.t0 + 2 * 3600) == pytest.approx(ph.electron_larmor_GHz(sc.B_T))
    assert tl.value_at("mw_rf_status", s.t0 + 6 * 3600) == 0
    p = [tl.value_at("online_p", s.t0 + h * 3600) for h in (0.5, 4.9, 6.9)]
    assert p[0] < 0.5 and 45 < p[1] < 50 and 20 < p[2] < p[1]        # TE, spin-up, relax
    assert len(s.burns) == 3 and all(b.ok for b in s.burns)
    assert s.burns[0].centre_mhz == pytest.approx(ph.deuteron_larmor_MHz(sc.B_T), abs=1e-3)


def test_history_replay_reproduces_the_designed_spin_up():
    # RF ON at 1:00 with the UCA at 5 V, full power 5 min later: the same rule the
    # history drive applies to an RF-on period no logbook covers
    from cooldown_sim.model.target import TargetModel
    s = sc.make(seed=0)
    m = TargetModel(s.timeline, s.t0 + 0.5 * 3600)
    assert m.hist_mw(s.t0 + 3600 + 120)[0] == 0.0
    assert m.hist_mw(s.t0 + 3600 + 600)[0] > 0.8
    for h in (1.5, 2.0, 3.0, 4.5, 6.0):
        t = s.t0 + h * 3600
        m.advance(t)
        assert 100 * m.state.P == pytest.approx(s.timeline.value_at("online_p", t), abs=0.5), h


def test_every_instrument_channel_has_a_value():
    s = sc.make(seed=0)
    cmap = channel_map.load()
    t = s.t0 + 2 * 3600
    for inst, chans in cmap.instruments.items():
        for name, spec in chans.items():
            if spec.source is None and not spec.select:
                continue                                         # coldboard / no record
            v = spec.recorded(s.timeline, t)
            if spec.source == "uca_v":
                continue
            assert math.isfinite(v), (inst, name)


def test_faults():
    s = sc.make(seed=0, faults=("data_gap", "glitches"))
    gaps = s.timeline.gaps(300)
    assert len(gaps) == 1 and gaps[0][1] - gaps[0][0] >= 600
    assert np.isnan(s.timeline.table.numeric["F5"]).sum() >= 3
    assert any("gap" in e[1] for e in s.events)
