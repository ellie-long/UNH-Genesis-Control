"""Hindcast and per-cool-down DNP values: lobe options, parameter merge,
field fallback, record despiking, and the hindcast loop on a synthetic record."""

import math

import numpy as np
import pytest
import yaml

from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.model import hindcast
from cooldown_sim.model import physics as ph
from cooldown_sim.model import target
from cooldown_sim.model.target import FIELD_T_PER_A, TargetModel, load_params

from test_cooldown_sim_model import B, F_OPT, record   # noqa: E402  (same synthetic record)


def test_lobe_default_unchanged_and_options():
    w = 0.2
    assert ph.dnp_lobe(0.0, w) == pytest.approx(1.0)
    assert ph.dnp_lobe(2 * w, w) == pytest.approx(-1.0)
    assert ph.dnp_lobe(2 * w, w, neg_scale=0.3) == pytest.approx(-0.3)
    assert ph.dnp_lobe(0.0, w, neg_scale=0.3) == pytest.approx(1.0)        # positive lobe untouched
    assert ph.dnp_lobe(-2 * w, w, neg_side=-1) == pytest.approx(-1.0)      # mirrored
    assert ph.dnp_lobe(0.0, w, neg_side=-1) == pytest.approx(1.0)


def test_cooldown_values_replace_the_defaults(tmp_path, monkeypatch):
    path = tmp_path / "cooldown_dnp.yaml"
    path.write_text(yaml.safe_dump({"cooldowns": {"2099-01-Cooldown": {
        "P_max": 0.123, "tau_up_s": 999.0, "neg_side": -1, "fit": {"cost_after_pp": 1.0}}}}))
    monkeypatch.setattr(target, "COOLDOWN_DNP_FILE", path)
    base = load_params()
    p = target.load_params(cooldown="2099-01")
    assert p["dnp"]["P_max"] == 0.123 and p["dnp"]["tau_up_s"] == 999.0 and p["dnp"]["neg_side"] == -1
    assert p["dnp"]["T1_P_s"] == base["dnp"]["T1_P_s"]                       # not overridden
    assert "fit" not in p["dnp"]
    assert target.load_params(cooldown="2000-01")["dnp"]["P_max"] == base["dnp"]["P_max"]


def test_field_falls_back_to_the_supply_current():
    t = np.arange(0.0, 3600.0, 20.0)
    n = len(t)
    tl = Timeline(SlowTable(t=t, numeric={"magnet_current": np.full(n, 48.1), "T1": np.full(n, 1.0),
                                          "online_p": np.full(n, 1.0)}), max_hold_s=60)
    m = TargetModel(tl, 60.0, load_params())
    assert m.field_T(600.0) == pytest.approx(48.1 * FIELD_T_PER_A)
    assert 4.9 < m.field_T(600.0) < 5.0


def test_polarization_is_lost_quickly_without_field(monkeypatch):
    p = load_params()
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0, p)
    monkeypatch.setattr(m, "field_T", lambda t: 0.0)
    m.advance(60.0 + 10 * target.LOW_FIELD_T1_S)
    assert abs(m.state.P) < 0.001


def test_despike_removes_isolated_glitches_only():
    p = np.linspace(0.0, 40.0, 200)
    p[[50, 120]] = [95.0, -60.0]
    out = hindcast.despike(p)
    assert np.isnan(out[50]) and np.isnan(out[120])
    assert np.isfinite(np.delete(out, [50, 120])).all()


def test_hindcast_tracks_a_record_that_follows_the_model():
    tl = record(hours=4.0, p_pct=0.0)
    s = hindcast.run(tl, load_params(), step_s=60.0)
    assert np.nanmax(s.p_model) > 10.0                                      # it spins up
    sc = hindcast.score("synthetic", "free", s)
    assert sc.n > 100 and math.isfinite(sc.mae)
