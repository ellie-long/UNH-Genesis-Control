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
    assert spin1.material_for("2025-07") == ""                    # unknown: phenomenological
    assert spin1.material_for(None) == ""                          # synthetic
    t_1207 = 1607346000.0                                          # 2020-12-07 08:00 EST
    t_1211 = 1607691600.0                                          # 2020-12-11 08:00 EST
    assert spin1.material_for("2020-12", t_1207) == "trityl_d_propanediol"
    assert spin1.material_for("2020-12", t_1211) == "tempo_d_butanol"
    assert not spin1.uses_cooldown_fit("2026-06") and not spin1.uses_cooldown_fit("2020-12", t_1211)


def test_material_uses_its_own_measured_lineshape():
    # irradiated d-butanol: the 2024-09-18 slow sweep, beta peak at 28.023 GHz/T
    s = spin1.Spin1(MAT)
    assert s.peak_x == pytest.approx(28.023) and s.x_opt == pytest.approx(28.023)
    B = 4.85
    at_peak = s.rates(28.023 * B, B, 1.0)
    assert at_peak.k_beta1 == pytest.approx(s.base["k_beta1"], rel=1e-6)   # the fitted set at its optimum
    assert s.rates(28.031 * B, B, 1.0).k_beta1 < 0.05 * s.base["k_beta1"]  # past the crossover
    assert s.rates(28.0405 * B, B, 1.0).k_alpha1 > 50 * s.base["k_alpha1"]  # Magic -: P-down drive


def test_lineshape_tapers_each_side_separately():
    ls = {"peak_x": 0.0, "taper": 1.0, "taper_low": 0.1, "taper_high": 2.0,
          "x": [0.0, 1.0], "B": [1.0, 1.0], "A": [1.0, 1.0]}
    assert spin1.lineshape(-0.05, ls=ls)[0] == pytest.approx(0.5)
    assert spin1.lineshape(-0.2, ls=ls)[0] == 0.0
    assert spin1.lineshape(2.0, ls=ls)[1] == pytest.approx(0.5)


def test_label_only_material_keeps_the_cooldown_fit():
    # 2025-11 is labelled irradiated d-butanol (Magic +/-) but keeps its own fitted DNP
    assert spin1.material_for("2025-11") == MAT
    assert spin1.uses_cooldown_fit("2025-11")
    assert not spin1.uses_cooldown_fit("2024-09")          # the rate model (McClellan-scored)
    params = load_params(cooldown="2025-11")
    m = TargetModel(record(), 60.0, params, cooldown="2025-11")
    assert m.spin1_engine(600.0) is None
    assert m.dnp_at(600.0) == m.dnp
    assert m.magic_GHz("+", 600.0) == pytest.approx(spin1.magic_ratio("+", MAT) * B)


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
    assert d["P_max"] == pytest.approx(0.39) and d["lobe_width_GHz"] == pytest.approx(0.026)
    assert m.dnp["P_max"] != d["P_max"]                             # defaults untouched
