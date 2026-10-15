"""Live NMR source: sweep synthesis and DAQ-style baselines (no fitter process)."""

import numpy as np
import pytest

from cooldown_sim.model import physics as ph
from cooldown_sim.model.target import TargetModel
from cooldown_sim.nmr import source as nmr_source
from cooldown_sim.nmr.source import NMRSource

from test_cooldown_sim_model import B, record   # noqa: E402  (same synthetic record)


def make(p_pct=50.0, seed=1):
    tl = record(rf=0.0, p_pct=p_pct)
    m = TargetModel(tl, 60.0)
    return m, NMRSource(m, tl, seed=seed)


def test_sweep_geometry_and_amplitude_calibration():
    m, src = make(50.0)
    sw = src.sweep(61.0)
    assert len(sw["raw"]) == nmr_source.W == len(sw["baseline_sub"])
    assert sw["centfreq_mhz"] == pytest.approx(ph.deuteron_larmor_MHz(B), abs=1e-6)
    peak = np.max(np.abs(sw["baseline_sub"]))
    assert peak == pytest.approx(nmr_source.PEAK_AT_50_V, rel=0.05)     # real 2026-06 amplitude
    # two sweeps at the same instant differ only by white noise: std = sqrt(2) * sigma
    diff = src.sweep(61.0)["raw"] - src.sweep(61.0)["raw"]
    assert np.std(diff) / np.sqrt(2) == pytest.approx(nmr_source.NOISE_V, rel=0.15)


def test_signal_scales_with_polarization_and_vanishes_without_field():
    peaks = [np.max(np.abs(make(p)[1].sweep(61.0)["baseline_sub"])) for p in (5.0, 25.0, 50.0)]
    assert peaks[0] < peaks[1] < peaks[2]
    m, src = make(50.0)
    m.field_T = lambda t: 0.0                                           # magnet off
    assert np.max(np.abs(src.sweep(61.0)["baseline_sub"])) < 10 * nmr_source.NOISE_V


def test_tensor_polarization_changes_the_line():
    m1, s1 = make(40.0, seed=2)
    m2, s2 = make(40.0, seed=2)
    m2.state.Q = ph.q_boltzmann(0.4) - 0.05                            # off the Boltzmann curve
    a, b = s1.sweep(61.0)["baseline_sub"], s2.sweep(61.0)["baseline_sub"]
    assert np.max(np.abs(a - b)) > 20 * nmr_source.NOISE_V


def test_baseline_with_the_line_on_window_subtracts_the_line():
    m, src = make(50.0)
    src.sweep(61.0)
    m.inputs.baseline_requests += 1                                     # operator: Take baseline
    sw = src.sweep(62.0)
    assert "WITH the line" in sw["baseline_note"]
    assert np.max(np.abs(src.sweep(63.0)["baseline_sub"])) < 10 * nmr_source.NOISE_V


def test_baseline_with_the_line_shifted_off_keeps_the_signal():
    m, src = make(50.0)
    m.inputs.line_offset_MHz = 1.0                                      # field shifted
    m.inputs.baseline_requests += 1
    sw = src.sweep(61.0)
    assert "off-window" in sw["baseline_note"]
    m.inputs.line_offset_MHz = 0.0
    assert np.max(np.abs(src.sweep(62.0)["baseline_sub"])) == pytest.approx(
        nmr_source.PEAK_AT_50_V, rel=0.06)


def test_noise_multiplier():
    m, src = make(50.0, seed=4)
    m.inputs.nmr_noise_x = 10.0
    diff = src.sweep(61.0)["raw"] - src.sweep(61.0)["raw"]
    assert np.std(diff) / np.sqrt(2) == pytest.approx(10 * nmr_source.NOISE_V, rel=0.15)
