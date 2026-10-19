"""Pure fallback waveform generation without requiring a calibrated NMR fit.

Operator mode uses an explicit/reference resonance center and the existing
41+41 frequency pattern; uniformly weights each bin when no usable NMR
heights exist. This is not a fitter result or hardware authorization.
"""
from __future__ import annotations
import math
import numpy as np
from .waveform_planner import (
    WaveformPlan, WaveformPlanError, REFERENCE_F0_MHZ,
    TOTAL_BURN_TIME_S, MAX_ARB_POINTS,
    reference_pattern, _material_name, _case_name,
)


OPERATOR_EDGE = 0.5  # Chhetra's October 2026 default, in physical volts.


def build_operator_waveform_plan(*, material, case, polarization=0.30,
                                 center_mhz=None):
    material = _material_name(material)
    case = _case_name(case)
    p = float(polarization)
    if not math.isfinite(p) or not -1.0 <= p <= 1.0:
        raise WaveformPlanError('operator reference polarization must be finite and within [-1, 1]')
    center = REFERENCE_F0_MHZ[material] if center_mhz is None else float(center_mhz)
    if not math.isfinite(center) or not 1.0 <= center <= 100.0:
        raise WaveformPlanError('operator resonance center MHz must be finite and in [1, 100]')
    f_peak, f_shoulder = reference_pattern(material, case, p)
    offset = center - REFERENCE_F0_MHZ[material]
    f_peak = np.asarray(f_peak, dtype=float) + offset
    f_shoulder = np.asarray(f_shoulder, dtype=float) + offset
    frequencies = np.concatenate((f_peak, f_shoulder))
    volts = frequencies - center
    # Do not silently clip a physically invalid frequency-to-voltage mapping.
    if np.any(~np.isfinite(volts)) or np.max(np.abs(volts)) > OPERATOR_EDGE:
        raise WaveformPlanError('reference frequencies exceed the +/-0.5 V FM window')
    weights = np.ones(frequencies.size, dtype=float)
    dwell = TOTAL_BURN_TIME_S * weights / weights.sum()
    edges = np.cumsum(dwell)
    edges[-1] = TOTAL_BURN_TIME_S
    time_axis = np.linspace(0.0, TOTAL_BURN_TIME_S, MAX_ARB_POINTS)
    positions = np.clip(np.searchsorted(edges, time_axis, side='right'), 0, len(volts)-1)
    v = volts[positions].copy()
    v[0], v[-1] = -OPERATOR_EDGE, OPERATOR_EDGE
    normalized = (v / OPERATOR_EDGE).astype(np.float32)
    control_t = np.concatenate(([0.0], edges))
    control_v = np.concatenate(([-OPERATOR_EDGE], volts))
    plan = WaveformPlan(
        material=material, case=case, fit_ts=None, fit_type='OPERATOR_REFERENCE',
        omegD_mhz=center, frequency_shift_mhz=offset,
        f_peak_mhz=f_peak, f_shoulder_mhz=f_shoulder,
        peak_weights=weights[:len(f_peak)], shoulder_weights=weights[len(f_peak):],
        peak_time_fraction=float(len(f_peak))/float(len(frequencies)),
        control_time_s=control_t, control_offset_mhz=control_v,
        normalized_samples=normalized, physical_voltage_v=(normalized*OPERATOR_EDGE).astype(np.float32),
        period_s=TOTAL_BURN_TIME_S, vhigh_v=OPERATOR_EDGE, vlow_v=-OPERATOR_EDGE,
        r_high=1.0, used_measured_heights=False,
    )
    plan.validate()
    return plan
