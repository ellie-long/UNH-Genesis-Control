"""Closed-loop target model: analytic limits and closed-loop behaviour.

Uses a synthetic record (constant 1.0 K lattice, 4.94 T, microwaves on at the
DNP optimum) so every expectation is analytic.
"""

import math

import numpy as np
import pytest

from cooldown_sim.data.records import SlowTable
from cooldown_sim.data.timeline import Timeline
from cooldown_sim.model import physics as ph
from cooldown_sim.model.target import TargetModel, load_params

B = 4.939
F_OPT = ph.electron_larmor_GHz(B)


# ------------------------------------------------------------------ physics
def test_te_polarization_limits():
    P, Q = ph.te_polarization(5.0, 1.0)
    y = ph.H_OVER_K_K_PER_MHZ * ph.deuteron_larmor_MHz(5.0) / 1.0
    assert P == pytest.approx(2 * y / 3, rel=1e-5)            # small-y limit
    assert Q == pytest.approx(ph.q_boltzmann(P), rel=1e-6)     # TE lies on the Boltzmann curve
    assert ph.te_polarization(5.0, 1e6)[0] == pytest.approx(0, abs=1e-8)
    P_cold, Q_cold = ph.te_polarization(5.0, 1e-5)             # T -> 0: fully polarised
    assert P_cold == pytest.approx(1.0) and Q_cold == pytest.approx(1.0)


def test_constants_match_the_lab():
    assert ph.deuteron_larmor_MHz(4.939) == pytest.approx(32.281, abs=0.002)   # ssRF carrier in blast logs
    assert ph.electron_larmor_GHz(4.939) == pytest.approx(138.42, abs=0.01)    # best DNP freq, 2026-06
    assert ph.electron_larmor_GHz(5.077) == pytest.approx(142.28, abs=0.01)    # best DNP freq, 2025-12


@pytest.mark.parametrize("P,Q", [(0.5, None), (0.5, 0.10), (-0.3, None), (0.0, 0.0), (0.9, 0.85)])
def test_populations_and_ratios_invert_the_lineshape_model(P, Q):
    import online_fitter.vendored  # noqa: F401
    import deuteron_model as dm
    Q = ph.q_boltzmann(P) if Q is None else Q
    pops = ph.populations(P, Q)
    assert sum(pops) == pytest.approx(1.0)
    rN, rP = ph.asymmetry_ratios(P, Q)
    assert dm.ratio_P_nonbolt(rN, rP) == pytest.approx(P, abs=1e-9)
    assert dm.ratio_Q_nonbolt(rN, rP) == pytest.approx(Q, abs=1e-9)
    if Q == pytest.approx(ph.q_boltzmann(P)):
        assert rN == pytest.approx(rP, rel=1e-9)               # one spin temperature


def test_dnp_lobe_shape():
    w = 0.2
    assert ph.dnp_lobe(0.0, w) == pytest.approx(1.0)
    assert ph.dnp_lobe(2 * w, w) == pytest.approx(-1.0)
    # record: small positive P 0.3-0.4 GHz below the optimum, never negative there
    assert 0.0 < ph.dnp_lobe(-0.42, w) < 0.25 and 0.0 < ph.dnp_lobe(-0.30, w) < 0.25
    xs = np.linspace(-1.5, 1.5, 3001)
    vals = [ph.dnp_lobe(x, w) for x in xs]
    assert max(vals) == pytest.approx(1.0, abs=1e-4) and min(vals) == pytest.approx(-1.0, abs=1e-4)
    assert abs(ph.dnp_lobe(1.6, w)) < 1e-6                     # 140 GHz at 4.94 T: no DNP (seen)


def test_vapor_pressure_ratio_against_its90_points():
    # ITS-90 He-4: 0.120 Torr at 1.0 K, 23.8 Torr at 2.0 K
    assert ph.he4_vapor_pressure_ratio(1.0, 2.0) == pytest.approx(23.8 / 0.120, rel=0.15)
    assert ph.he4_vapor_pressure_ratio(1.2, 1.2) == 1.0


# ------------------------------------------------------------------ target
def record(hours=6.0, rf=1.0, p_pct=10.0):
    t = np.arange(0.0, hours * 3600.0, 20.0)
    n = len(t)
    return Timeline(SlowTable(t=t, numeric={
        "T1": np.full(n, 1.0), "central_field": np.full(n, B), "mw_rf_status": np.full(n, rf),
        "mw_power_db": np.full(n, 10.0), "mw_freq_ghz": np.full(n, F_OPT),
        "online_p": np.full(n, p_pct), "F5": np.full(n, 3.0), "M2": np.full(n, 50.0)}), max_hold_s=60)


@pytest.fixture(scope="module")
def params():
    return load_params()


def test_repeating_history_reproduces_the_record(params):
    m = TargetModel(record(), 60.0, params)
    for t in (600.0, 3600.0, 7200.0):
        assert m.temperature("F5", 3.0, t) == pytest.approx(3.0)
        assert m.temperature("M2", 50.0, t) == pytest.approx(50.0)
        assert m.lattice_T(t) == pytest.approx(1.0)
        assert m.cell_pressure(0.12, t) == pytest.approx(0.12)


def test_spin_up_follows_tau_up(params):
    m = TargetModel(record(p_pct=0.0), 60.0, params)
    tau = params["dnp"]["tau_up_s"]
    s = m.advance(60.0 + tau)
    assert s.P == pytest.approx(params["dnp"]["P_max"] * (1 - math.exp(-1)), rel=0.02)
    s = m.advance(60.0 + 8 * tau)
    assert s.P == pytest.approx(params["dnp"]["P_max"], rel=0.01)
    assert s.Q == pytest.approx(ph.q_boltzmann(s.P), rel=0.02)   # TE-start spin-up stays on Q_B(P)


def test_q_heals_back_to_the_boltzmann_curve(params):
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0, params)   # MW off: P ~ constant, no tracking lag
    m.advance(600.0)
    m.queue_burn(600.0, dP=-0.02, dQ=-0.06)                  # a burn suppresses Q
    s = m.advance(660.0)
    assert s.Q < ph.q_boltzmann(s.P) - 0.03                  # knocked off the curve
    s = m.advance(660.0 + 6 * params["dnp"]["Q_heal_tau_s"])
    # healed: only the tracking lag behind the slowly relaxing Q_B(P) remains
    # (tau_heal * |dQ_B/dt| ~ 240 s * 1.7e-5 /s ~ 4e-3), vs the 0.038 burn offset
    assert s.Q == pytest.approx(ph.q_boltzmann(s.P), abs=6e-3)


def test_operator_turns_microwaves_off_early(params):
    m = TargetModel(record(p_pct=40.0), 60.0, params)
    m.inputs.mw_mode, m.inputs.mw_on = "operator", False
    T1 = params["dnp"]["T1_P_s"]
    s = m.advance(60.0 + T1)
    assert s.P == pytest.approx(0.40 * math.exp(-1), rel=0.03)      # relaxes toward TE ~0.1 %
    lat = params["assumed"]["lattice"]
    assert m.lattice_T() == pytest.approx(1.0 - lat["gain_K_per_drive"], rel=0.01)   # cooler than history
    assert m.temperature("M2", 50.0, s.t) < 50.0                     # fitted magnet gain responds


def test_wrong_frequency_gives_no_dnp(params):
    m = TargetModel(record(hours=10.0, p_pct=0.0), 60.0, params)
    m.inputs.mw_mode, m.inputs.mw_on, m.inputs.mw_freq_GHz = "operator", True, F_OPT + 1.6
    assert abs(m.advance(60.0 + 4 * params["dnp"]["tau_up_s"]).P) < 0.01
    m.inputs.mw_freq_GHz = F_OPT + 2 * params["dnp"]["lobe_width_GHz"]   # negative lobe
    assert m.advance(60.0 + 12 * params["dnp"]["tau_up_s"]).P < -0.3


def test_quench_and_recovery(params):
    m = TargetModel(record(), 60.0, params)
    m.inputs.quench = True
    qa = params["assumed"]["quench"]
    t = 60.0 + 5 * qa["rise_tau_s"]
    assert m.temperature("F5", 3.0, t) == pytest.approx(qa["fridge_target_K"], rel=0.02)
    assert m.cell_pressure(0.12, t) > 700.0
    m.inputs.quench = False
    t2 = t + 6 * qa["recovery_tau_s"]
    assert m.temperature("F5", 3.0, t2) == pytest.approx(3.0, rel=0.1)


def test_coldboard_heat_warms_the_lattice(params):
    m = TargetModel(record(), 60.0, params)
    m.inputs.coldboard_heat_W = 1.0
    cb = params["assumed"]["coldboard"]
    m.advance(60.0 + 10 * cb["tau_s"])
    assert m.lattice_T() == pytest.approx(1.0 + cb["gain_K_per_W"], rel=0.01)
    assert m.cell_pressure(0.12, m.state.t) > 0.12                 # vapor pressure follows


def test_seek_reinitialises_from_the_record(params):
    m = TargetModel(record(p_pct=25.0), 60.0, params)
    m.inputs.mw_mode, m.inputs.mw_on = "operator", False
    m.advance(5000.0)
    assert m.state.P < 0.25
    m.reset(9000.0)
    assert m.state.P == pytest.approx(0.25) and m.state.t == 9000.0


def test_spin_temperature_inverts_te(params):
    m = TargetModel(record(), 60.0, params)
    for T in (0.5, 1.0, 4.2):
        P = ph.te_polarization(B, T)[0]
        assert m.spin_temperature(P, B) == pytest.approx(T, rel=1e-4)
    assert m.spin_temperature(-ph.te_polarization(B, 1.0)[0], B) == pytest.approx(-1.0, rel=1e-4)


def test_params_mark_assumptions():
    p = load_params()
    for section in p["assumed"].values():
        assert "ASSUMED" in section["basis"]
    assert "ASSUMED" in p["dnp"]["basis"]["lobe_width_GHz"]
    assert all(g["status"].startswith(("fitted", "unfitted")) for g in p["thermal"]["channels"].values())


def test_burn_cannot_leave_the_physical_region(params):
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0, params)
    m.queue_burn(100.0, dP=-0.1, dQ=-5.0)                     # absurd burn
    s = m.advance(200.0)
    assert ph.physical(s.P, s.Q) and m.clamped_burns == 1


def test_negative_coldboard_heat_cannot_poison_the_state(params):
    m = TargetModel(record(), 60.0, params)
    m.inputs.coldboard_heat_W = -100.0                        # nonsense input
    s = m.advance(5000.0)
    assert math.isfinite(s.P) and math.isfinite(s.Q) and m.lattice_T() >= 0.05


def test_quench_collapses_polarization_quickly(params):
    qa = params["assumed"]["quench"]
    m = TargetModel(record(rf=0.0, p_pct=40.0), 60.0, params)
    m.inputs.quench = True
    s = m.advance(60.0 + 10 * qa["rise_tau_s"] + 5 * qa["T1_quenched_s"])
    assert abs(s.P) < 0.02


def test_te_polarization_never_overflows():
    assert ph.te_polarization(5.0, 1e-9) == pytest.approx((1.0, 1.0))
