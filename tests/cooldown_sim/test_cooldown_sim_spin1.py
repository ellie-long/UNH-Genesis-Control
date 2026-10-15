"""Spin-1 rate-equation DNP (spin1fit): material selection, frequency and power
dependence, Q pinned to the Boltzmann curve, and stepping consistency.
Skipped when the Spin-1-Rates-Fit checkout is not next to this repository."""

import math

import numpy as np
import pytest

from cooldown_sim.model import physics as ph
from cooldown_sim.model import spin1
from cooldown_sim.model.target import TargetModel, load_params

from test_cooldown_sim_model import B, record   # noqa: E402  (same synthetic record)

pytestmark = pytest.mark.skipif(not spin1.available(), reason="Spin-1-Rates-Fit (spin1fit) not found")

MAT = "irradiated_d_butanol"
X_OPT = spin1.load_table()["materials"][MAT]["x_opt"]


def test_material_schedule():
    assert spin1.material_for("2026-06") == MAT
    assert spin1.material_for("2026-06-Cooldown") == MAT
    assert spin1.material_for("2024-09") == ""                    # unknown: phenomenological
    assert spin1.material_for(None) == ""                          # synthetic
    t_1207 = 1607346000.0                                          # 2020-12-07 08:00 EST
    t_1211 = 1607691600.0                                          # 2020-12-11 08:00 EST
    assert spin1.material_for("2020-12", t_1207) == "trityl_d_propanediol"
    assert spin1.material_for("2020-12", t_1211) == "tempo_d_butanol"


def test_lineshape_normalized_and_tapered():
    ls = spin1.load_table()["lineshape"]
    b, a = spin1.lineshape(ls["peak_x"])
    assert b == pytest.approx(1.0) and a < 0.2
    assert spin1.lineshape(27.0) == (0.0, 0.0) and spin1.lineshape(29.0) == (0.0, 0.0)


def test_spin_up_at_the_optimum_reaches_the_fitted_steady_state():
    eng = spin1.Spin1(MAT)
    f = X_OPT * 5.077
    s = eng.initial(ph.te_polarization(5.077, 0.997)[0], 5.077, 0.997)
    for _ in range(int(6 * 3600 / 30)):
        s = eng.step(s, 30.0, f_GHz=f, B_T=5.077, T_K=0.997, power=1.0)
    assert s.Pn == pytest.approx(0.498, abs=0.02)                  # fit_asym_pq steady state
    assert s.Qn == pytest.approx(ph.q_boltzmann(s.Pn), abs=0.005)  # Q pinned (k_delta)


def test_stepping_equals_one_long_solve():
    eng = spin1.Spin1(MAT)
    f = X_OPT * B
    s = eng.initial(0.0, B, 1.0)
    for _ in range(120):
        s = eng.step(s, 30.0, f_GHz=f, B_T=B, T_K=1.0, power=1.0)
    one = eng.step(eng.initial(0.0, B, 1.0), 3600.0, f_GHz=f, B_T=B, T_K=1.0, power=1.0)
    assert s.Pn == pytest.approx(one.Pn, rel=1e-4)


def test_power_and_frequency_steer_the_polarization():
    eng = spin1.Spin1(MAT)

    def after(power, x, hours=2.0):
        s = eng.initial(0.0, B, 1.0)
        return eng.step(s, hours * 3600.0, f_GHz=x * B, B_T=B, T_K=1.0, power=power).Pn

    full = after(1.0, X_OPT)
    assert full > 0.3
    assert 0.0 < after(0.2, X_OPT) < full                          # less power, slower build
    assert abs(after(0.0, X_OPT)) < 0.01                           # no power, no DNP
    assert after(1.0, X_OPT + 0.058) < 0.0                         # the P-down (alpha) side


def test_target_model_uses_the_rate_equations_for_a_known_material():
    m = TargetModel(record(p_pct=0.0), 60.0, load_params(), cooldown="2026-06")
    assert m.spin1_engine(600.0) is not None
    m.advance(3600.0)
    assert math.isfinite(m.state.Pe) and m.state.P > 0.05
    synth = TargetModel(record(p_pct=0.0), 60.0, load_params())
    assert synth.spin1_engine(600.0) is None                       # synthetic: phenomenological


def test_material_without_rates_uses_its_phenomenological_values():
    m = TargetModel(record(p_pct=0.0), 60.0, load_params(), cooldown="2020-09")
    assert m.material(600.0) == "trityl_d_propanediol"
    assert m.spin1_engine(600.0) is None                           # no fitted rates for trityl
    d = m.dnp_at(600.0)
    assert d["P_max"] == pytest.approx(0.25) and d["lobe_width_GHz"] == pytest.approx(0.04)
    assert m.dnp["P_max"] != d["P_max"]                             # defaults untouched
