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
from cooldown_sim.model.fit_params import drive
from cooldown_sim.model.target import TargetModel, load_params

B = 4.939
F_OPT = ph.electron_larmor_GHz(B)
#: history drive of the synthetic record: RF on at +10 dB (saturated), UCA 0 V, at F_OPT
D_HIST = drive(1.0, 10.0, F_OPT)


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
    cup, bath = params["assumed"]["cup"], params["assumed"]["bath"]
    q_mw = params["heat"]["mw_W_at_full_drive"] * D_HIST
    # cooler than history, but never below the (cooled) bath it sits in
    expected = max(1.0 - cup["gain_K_per_W"] * q_mw, 1.0 - bath["gain_K_per_W"] * q_mw)
    assert m.lattice_T() == pytest.approx(expected, rel=0.01)
    assert m.lattice_T() < 1.0
    assert m.truth(m.state.t)["boiloff_delta_slm"] == pytest.approx(-q_mw / params["heat"]["W_per_slm"], rel=0.01)
    assert m.temperature("M2", 50.0, s.t) < 50.0                     # fitted magnet gain responds


def test_wrong_frequency_gives_no_dnp(params):
    m = TargetModel(record(hours=10.0, p_pct=0.0), 60.0, params)
    m.inputs.mw_mode, m.inputs.mw_on, m.inputs.mw_freq_GHz = "operator", True, F_OPT + 1.6
    m.inputs.mw_uca_V = 0.0                                   # UCA lowered: full power
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
    m.inputs.coldboard_heat_W = 0.1
    bath = params["assumed"]["bath"]
    m.advance(60.0 + 10 * bath["tau_s"])
    assert m.lattice_T() == pytest.approx(1.0 + 0.1 * bath["gain_K_per_W"], rel=0.01)
    assert m.cell_pressure(0.12, m.state.t) > 0.12                 # vapor pressure follows
    tr = m.truth(m.state.t)
    assert tr["heat_total_W"] == pytest.approx(0.1 + params["heat"]["mw_W_at_full_drive"] * D_HIST)
    assert tr["boiloff_delta_slm"] == pytest.approx(0.1 / params["heat"]["W_per_slm"], rel=0.01)


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
        assert section["basis"].startswith(("ASSUMED", "PARTLY FITTED")), section["basis"][:40]
    assert "MEASURED" in p["heat"]["basis"]["mw_W_at_full_drive"]
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


# ------------------------------------------------------------ microwave drive
def test_vaunix_setpoints_from_minus_5_db_up_give_full_power():
    from cooldown_sim.model.fit_params import saturation
    for p in (10.0, 0.0, -3.0, -4.5, -5.0):           # every era of the record: saturated chain
        assert saturation(p) == 1.0
        assert drive(1.0, p, 140.0) == pytest.approx(1.0)
    assert saturation(-15.0) == pytest.approx(0.1)     # ASSUMED roll-off below saturation
    assert drive(1.0, -40.0, 140.0) < 1e-3              # the record's genuinely-low entries
    for junk in (float("nan"), 99.0, -99.0):           # missing / garbage -> the usual saturated setting
        assert saturation(junk) == 1.0
    assert drive(0.0, 10.0, 140.0) == 0.0 and drive(float("nan"), 10.0) == 0.0


def test_uca_table_endpoints_and_monotonic():
    from cooldown_sim.model.fit_params import uca_fraction
    for f in (138.0, 139.0, 140.0, 141.3, 142.0, float("nan"), 130.0, 150.0):
        assert uca_fraction(0.0, f) == pytest.approx(1.0)      # 0 V: full power
        assert uca_fraction(5.0, f) == 0.0                     # 5 V: none
        assert uca_fraction(4.0, f) == 0.0
        vals = [uca_fraction(v, f) for v in np.linspace(0.0, 5.0, 501)]
        assert all(b <= a + 1e-12 for a, b in zip(vals, vals[1:]))
    assert uca_fraction(3.0, 139.0) == pytest.approx((0.17 + 0.26) / 2)   # linear in frequency
    assert uca_fraction(2.75, 140.0) == pytest.approx((0.55 + 0.435) / 2)  # linear in voltage
    assert uca_fraction(3.0, 137.0) == uca_fraction(3.0, 138.0)            # clamped to the band
    assert uca_fraction(-1.0) == 1.0 and uca_fraction(7.0) == 0.0
    assert uca_fraction(float("nan")) == 0.0          # no attenuation information: no power


def test_frequency_factor_is_relative_to_140_ghz():
    from cooldown_sim.model.fit_params import freq_factor
    assert freq_factor(140.0) == pytest.approx(1.0)
    assert freq_factor(float("nan")) == 1.0
    assert freq_factor(138.0) == pytest.approx(282 / 325)
    assert freq_factor(139.8) == pytest.approx(333 / 325)            # the chain's peak
    assert freq_factor(139.9) == pytest.approx((333 + 325) / 2 / 325)
    assert freq_factor(145.0) == freq_factor(142.0) == pytest.approx(252 / 325)


def test_history_drive_ignores_the_record_uca_column(params):
    # the record's UCA column has not been the real UCA for years: history is
    # full power whenever RF is on, whatever that column says
    tl = record()
    n = len(tl.table.t)
    tl_uca = Timeline(SlowTable(t=tl.table.t, numeric={**tl.table.numeric, "uca_v": np.full(n, 5.0)}),
                      max_hold_s=60)
    m, m_uca = TargetModel(tl, 60.0, params), TargetModel(tl_uca, 60.0, params)
    for t in (600.0, 3600.0):
        assert m_uca.hist_mw(t) == m.hist_mw(t)
        assert m_uca.hist_mw(t)[0] == pytest.approx(D_HIST)
    assert 138.4 < F_OPT < 138.6                       # vendor points 289 / 290 mW; 325 mW at 140 GHz
    assert D_HIST == pytest.approx((289 + (290 - 289) * (F_OPT - 138.4) / 0.2) / 325)


def test_operator_drive_follows_the_observed_uca(params):
    m = TargetModel(record(), 60.0, params)
    assert m.inputs.mw_uca_V == 5.0                    # safe start, like the emulated supply
    m.inputs.mw_mode, m.inputs.mw_on, m.inputs.mw_freq_GHz = "operator", True, 140.0
    assert m.op_mw(600.0)[0] == 0.0                    # RF on at the 5 V enable voltage: no power
    m.inputs.mw_uca_V = 0.0
    assert m.op_mw(600.0)[0] == pytest.approx(1.0)     # UCA lowered: full power
    m.inputs.mw_uca_V = 2.8
    assert m.op_mw(600.0)[0] == pytest.approx(0.435)
    m.inputs.mw_on = False
    assert m.op_mw(600.0)[0] == 0.0


def test_dnp_strength_excludes_the_chain_frequency_response(params):
    # P_max comes from spin-ups at the operating frequencies, where the chain's
    # output already is what it is: full UCA power pumps to P_max anywhere in band
    m = TargetModel(record(p_pct=0.0), 60.0, params)
    m.inputs.mw_mode, m.inputs.mw_on, m.inputs.mw_uca_V = "operator", True, 0.0
    s = m.advance(60.0 + 8 * params["dnp"]["tau_up_s"])
    assert s.P == pytest.approx(params["dnp"]["P_max"], rel=0.01)


def test_minus_5_db_era_transitions_are_full_drive_steps():
    import datetime as dt

    from cooldown_sim.data import records
    from cooldown_sim.model.fit_params import transitions
    t0 = dt.datetime(2023, 3, 1, 12, 0, tzinfo=records.lab_tz()).timestamp()
    t = t0 + np.arange(0.0, 7200.0, 30.0)
    n = len(t)
    tl = Timeline(SlowTable(t=t, numeric={"mw_rf_status": (t >= t0 + 3600.0).astype(float),
                                          "mw_power_db": np.full(n, -4.0),
                                          "mw_freq_ghz": np.full(n, 140.0)}), max_hold_s=60)
    (ts, dd), = transitions(tl)
    assert ts == pytest.approx(t0 + 3600.0) and dd == pytest.approx(1.0)   # was 10**-1.4 ~ 0.04
