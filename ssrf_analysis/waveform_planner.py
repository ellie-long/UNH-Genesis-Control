"""
Genesis Phase-I ssRF adaptive waveform planner.

This module extracts the waveform mathematics from the authoritative
Genesis-Mission-ssRF-Controller ssRF_sweep_height.py implementation.

SAFETY / ARCHITECTURE BOUNDARY
------------------------------
This module performs analysis only.

It does NOT:
- communicate with the Keysight
- communicate with the SMC100A
- write EPICS PVs
- operate NMR / ssRF switches
- enable RF
- execute burns

Its only output is a validated WaveformPlan for the downstream Genesis
EPICS/sequencer layer.

The legacy source numerically maps frequency offset in MHz to WaveGen
voltage with a 1 MHz/V convention. That convention is preserved here for
source fidelity, but is explicitly marked NOT hardware-verified. Hardware
scaling must be confirmed during 5C.1 / 5C.2 before physical execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
from scipy.optimize import fsolve

from . import legacy_deuts as deuts


V_EDGE_V = 0.7

PEAK_POINTS = 41
SHOULDER_POINTS = 41

TOTAL_BURN_TIME_S = 0.01
MAX_ARB_POINTS = 8192


REFERENCE_F0_MHZ = {
    "Butanol": 31.694211,
    "ND3": 32.677988031,
}


ENGINE_MODEL_NAME = {
    "Butanol": "double",
    "ND3": "single",
}


P_FIT = np.array(
    [
        0.55,
        0.52,
        0.50,
        0.4750,
        0.4531,
        0.42,
        0.40,
        0.35,
        0.30,
        0.25,
    ]
)


class WaveformPlanError(ValueError):
    """Invalid fitter input or generated ssRF waveform plan."""


def _material_name(material: str) -> str:

    value = str(material).strip().lower()

    if value in {
        "nd3",
        "deuterated ammonia",
        "ammonia",
    }:
        return "ND3"

    if value in {
        "butanol",
        "d-butanol",
        "deuterated butanol",
    }:
        return "Butanol"

    raise WaveformPlanError(
        f"unsupported material {material!r}; "
        "expected Butanol or ND3"
    )


def _case_name(case: str) -> str:

    value = str(case).strip().capitalize()

    if value not in {
        "Enhancement",
        "Suppression",
        "Zero",
    }:
        raise WaveformPlanError(
            f"unsupported ssRF case {case!r}; "
            "expected Enhancement, Suppression, or Zero"
        )

    return value


def _finite_sorted_pair(
    freqs: Any,
    data: Any,
):

    if freqs is None or data is None:
        return None, None

    f = np.asarray(
        freqs,
        dtype=float,
    ).reshape(-1)

    y = np.asarray(
        data,
        dtype=float,
    ).reshape(-1)

    if (
        f.size != y.size
        or f.size < 2
    ):
        raise WaveformPlanError(
            "measured NMR frequency/data arrays "
            "must have the same length >= 2"
        )

    good = (
        np.isfinite(f)
        & np.isfinite(y)
    )

    if np.count_nonzero(good) < 2:
        raise WaveformPlanError(
            "measured NMR data must contain "
            "at least two finite points"
        )

    f = f[good]
    y = y[good]

    order = np.argsort(f)

    return (
        f[order],
        y[order],
    )


@dataclass(frozen=True)
class FitSnapshot:
    """Validated subset of one online_fitter publisher message."""

    material: str
    model: str
    constants_good: bool
    constants: Mapping[str, float]

    freqs_mhz: np.ndarray | None
    data: np.ndarray | None

    ts: str | None = None
    fit_type: str | None = None

    @classmethod
    def from_message(
        cls,
        message: Mapping[str, Any],
    ) -> "FitSnapshot":

        material = _material_name(
            message.get(
                "material",
                "",
            )
        )

        model = str(
            message.get("model")
            or ""
        ).strip()

        expected_model = (
            ENGINE_MODEL_NAME[
                material
            ]
        )

        if model != expected_model:
            raise WaveformPlanError(
                f"fit model {model!r} "
                f"does not match {material} "
                f"expected model "
                f"{expected_model!r}"
            )

        if not bool(
            message.get(
                "constants_good"
            )
        ):
            raise WaveformPlanError(
                "fit constants are not calibrated "
                "(constants_good is false)"
            )

        block = (
            message.get("constants")
            or {}
        )

        required = [
            "omegD",
            "omegQ1",
            "A1",
            "eta1",
            "scale",
            "phase",
            "r",
        ]

        if material == "Butanol":
            required += [
                "omegQ2",
                "eta2",
                "K",
            ]

        missing = [
            key
            for key in required
            if block.get(key) is None
        ]

        if missing:
            raise WaveformPlanError(
                "fit constants missing required "
                "field(s): "
                + ", ".join(missing)
            )

        constants = {
            key: float(block[key])
            for key in required
        }

        freqs, data = (
            _finite_sorted_pair(
                message.get(
                    "freqs_mhz"
                ),
                message.get(
                    "data"
                ),
            )
        )

        ts = (
            None
            if message.get("ts") is None
            else str(message["ts"])
        )

        fit_type = (
            None
            if message.get(
                "fit_type"
            ) is None
            else str(
                message[
                    "fit_type"
                ]
            )
        )

        return cls(
            material=material,
            model=model,
            constants_good=True,
            constants=constants,
            freqs_mhz=freqs,
            data=data,
            ts=ts,
            fit_type=fit_type,
        )


@dataclass(frozen=True)
class WaveformPlan:
    """Hardware-independent result of one fitter-to-ssRF calculation."""

    material: str
    case: str

    fit_ts: str | None
    fit_type: str | None

    omegD_mhz: float
    frequency_shift_mhz: float

    f_peak_mhz: np.ndarray
    f_shoulder_mhz: np.ndarray

    peak_weights: np.ndarray
    shoulder_weights: np.ndarray

    peak_time_fraction: float

    control_time_s: np.ndarray
    control_offset_mhz: np.ndarray

    normalized_samples: np.ndarray
    physical_voltage_v: np.ndarray

    period_s: float
    vhigh_v: float
    vlow_v: float

    r_high: float

    used_measured_heights: bool

    # Preserved from the authoritative implementation.
    # This numerical MHz-offset -> voltage convention has NOT yet
    # been independently verified on physical hardware.
    legacy_offset_mhz_per_v: float = 1.0
    scaling_verified_for_hardware: bool = False

    @property
    def waveform_points(self) -> int:
        return int(
            self.normalized_samples.size
        )

    def validate(self) -> None:

        if (
            self.waveform_points
            != MAX_ARB_POINTS
        ):
            raise WaveformPlanError(
                "waveform must contain "
                f"{MAX_ARB_POINTS} points, "
                f"got {self.waveform_points}"
            )

        if not np.all(
            np.isfinite(
                self.normalized_samples
            )
        ):
            raise WaveformPlanError(
                "waveform contains "
                "non-finite samples"
            )

        if (
            np.min(
                self.normalized_samples
            )
            < -1.0 - 1e-12
            or
            np.max(
                self.normalized_samples
            )
            > 1.0 + 1e-12
        ):
            raise WaveformPlanError(
                "normalized waveform "
                "exceeds [-1, 1]"
            )

        if not np.isclose(
            self.period_s,
            TOTAL_BURN_TIME_S,
            rtol=0.0,
            atol=1e-12,
        ):
            raise WaveformPlanError(
                "waveform period differs "
                "from authoritative "
                "10 ms source period"
            )

        if not (
            self.vhigh_v
            > self.vlow_v
        ):
            raise WaveformPlanError(
                "WaveGen VHIGH must "
                "be greater than VLOW"
            )


def weighted_bin_centers(
    t_start: float,
    t_end: float,
    weights: Any,
):

    weights = np.asarray(
        weights,
        dtype=float,
    )

    if (
        weights.ndim != 1
        or weights.size == 0
    ):
        raise WaveformPlanError(
            "weights must be a "
            "non-empty 1D array"
        )

    if (
        np.any(
            ~np.isfinite(weights)
        )
        or np.any(
            weights <= 0
        )
    ):
        raise WaveformPlanError(
            "weights must be "
            "finite and positive"
        )

    span = (
        float(t_end)
        - float(t_start)
    )

    if span <= 0:
        raise WaveformPlanError(
            "weighted-bin time span "
            "must be positive"
        )

    slot_widths = (
        span
        * weights
        / weights.sum()
    )

    slot_edges = np.concatenate(
        (
            [0.0],
            np.cumsum(
                slot_widths
            ),
        )
    )

    centers = (
        float(t_start)
        + 0.5
        * (
            slot_edges[:-1]
            + slot_edges[1:]
        )
    )

    return (
        centers,
        slot_widths,
    )


def reference_pattern(
    material: str,
    case: str,
    polarization: float,
):

    material = _material_name(
        material
    )

    case = _case_name(
        case
    )

    P = float(
        polarization
    )

    if case == "Enhancement":

        if material == "Butanol":

            peak_right_fit = np.array(
                [
                    31.646,
                    31.648,
                    31.649,
                    31.650,
                    31.652,
                    31.655,
                    31.656,
                    31.661,
                    31.665,
                    31.669,
                ]
            )

            peak_left = 31.6265

            shoulder_lo = 31.7680
            shoulder_hi = 31.83840

        else:

            peak_right_fit = np.array(
                [
                    32.621,
                    32.623,
                    32.625,
                    32.628,
                    32.629,
                    32.630,
                    32.632,
                    32.635,
                    32.637,
                    32.638,
                ]
            )

            peak_left = 32.592

            shoulder_lo = 32.776
            shoulder_hi = 32.95

        m, b = np.polyfit(
            P_FIT,
            peak_right_fit,
            1,
        )

        peak_right = (
            m * P + b
        )

        f_peak = np.linspace(
            peak_left,
            peak_right,
            PEAK_POINTS,
        )

        f_shoulder = np.linspace(
            shoulder_lo,
            shoulder_hi,
            SHOULDER_POINTS,
        )

    elif case == "Suppression":

        if material == "Butanol":

            f_peak = np.linspace(
                31.50,
                31.6292,
                PEAK_POINTS,
            )

            f_shoulder = np.linspace(
                31.694196745,
                31.7673,
                SHOULDER_POINTS,
            )

        else:

            f_peak = np.linspace(
                32.44,
                32.59,
                PEAK_POINTS,
            )

            f_shoulder = np.linspace(
                32.677988032,
                32.775,
                SHOULDER_POINTS,
            )

    else:

        if material == "Butanol":

            shoulder_right_fit = np.array(
                [
                    31.769,
                    31.760,
                    31.759,
                    31.7585,
                    31.7580,
                    31.7572,
                    31.7562,
                    31.7545,
                    31.7525,
                    31.7490,
                ]
            )

            peak_lo = 31.50
            peak_hi = 31.626

            shoulder_left = 31.7420

        else:

            shoulder_right_fit = np.array(
                [
                    32.743,
                    32.742,
                    32.740,
                    32.739,
                    32.737,
                    32.734,
                    32.732,
                    32.728,
                    32.721,
                    32.714,
                ]
            )

            peak_lo = 32.44
            peak_hi = 32.59

            shoulder_left = (
                32.677988032
            )

        m, b = np.polyfit(
            P_FIT,
            shoulder_right_fit,
            1,
        )

        shoulder_right = (
            m * P + b
        )

        f_peak = np.linspace(
            peak_lo,
            peak_hi,
            PEAK_POINTS,
        )

        f_shoulder = np.linspace(
            shoulder_left,
            shoulder_right,
            SHOULDER_POINTS,
        )

    return (
        f_peak,
        f_shoulder,
    )


def _component_height(
    freqs,
    r,
    *,
    material,
    constants,
):

    f = np.asarray(
        freqs,
        dtype=float,
    )

    omegD = constants["omegD"]
    omegQ1 = constants["omegQ1"]
    A1 = constants["A1"]
    eta1 = constants["eta1"]

    scale = constants["scale"]
    phase = constants["phase"]

    def one_bond(
        omegQ,
        A,
        eta,
        fraction,
    ):

        neg = (
            deuts.deut_phiavg(
                f,
                -1,
                omegD,
                omegQ,
                A,
                eta,
                r,
            )
            * np.cos(phase)
        )

        neg += (
            deuts.deut_disp_phiavg(
                f,
                -1,
                omegD,
                omegQ,
                A,
                eta,
                r,
            )
            * np.sin(phase)
        )

        pos = (
            deuts.deut_phiavg(
                f,
                1,
                omegD,
                omegQ,
                A,
                eta,
                r,
            )
            * np.cos(phase)
        )

        pos += (
            deuts.deut_disp_phiavg(
                f,
                1,
                omegD,
                omegQ,
                A,
                eta,
                r,
            )
            * np.sin(phase)
        )

        return (
            deuts.Xi(r)
            * scale
            * fraction
            * (
                neg + pos
            )
        )

    if material == "ND3":

        return one_bond(
            omegQ1,
            A1,
            eta1,
            1.0,
        )

    omegQ2 = constants["omegQ2"]
    eta2 = constants["eta2"]
    K = constants["K"]

    A2 = (
        A1
        * omegQ1
        / omegQ2
    )

    return (
        one_bond(
            omegQ1,
            A1,
            eta1,
            1.0 - K,
        )
        +
        one_bond(
            omegQ2,
            A2,
            eta2,
            K,
        )
    )


def build_waveform_plan(
    snapshot: FitSnapshot,
    *,
    case: str,
    polarization: float,
    high_polarization: float,
) -> WaveformPlan:

    material = _material_name(
        snapshot.material
    )

    case = _case_name(
        case
    )

    expected_model = (
        ENGINE_MODEL_NAME[
            material
        ]
    )

    if snapshot.model != expected_model:

        raise WaveformPlanError(
            f"snapshot model "
            f"{snapshot.model!r} "
            f"does not match "
            f"{material} expected "
            f"{expected_model!r}"
        )

    constants = dict(
        snapshot.constants
    )

    omegD = float(
        constants["omegD"]
    )

    (
        f_peak_reference,
        f_shoulder_reference,
    ) = reference_pattern(
        material,
        case,
        polarization,
    )

    shift = (
        omegD
        - REFERENCE_F0_MHZ[
            material
        ]
    )

    f_peak = (
        f_peak_reference
        + shift
    )

    f_shoulder = (
        f_shoulder_reference
        + shift
    )

    r_fit = float(
        constants["r"]
    )

    r_high = float(
        fsolve(
            lambda r:
            deuts.ratio_method(r)
            - float(
                high_polarization
            ),
            r_fit,
        )[0]
    )

    model_peak = (
        _component_height(
            f_peak,
            r_high,
            material=material,
            constants=constants,
        )
    )

    model_shoulder = (
        _component_height(
            f_shoulder,
            r_high,
            material=material,
            constants=constants,
        )
    )

    using_measured = (
        snapshot.freqs_mhz
        is not None
        and snapshot.data
        is not None
    )

    if using_measured:

        height_peak = np.interp(
            f_peak,
            snapshot.freqs_mhz,
            snapshot.data,
        )

        height_shoulder = np.interp(
            f_shoulder,
            snapshot.freqs_mhz,
            snapshot.data,
        )

    else:

        height_peak = model_peak
        height_shoulder = (
            model_shoulder
        )

    w_peak = np.clip(
        np.abs(
            height_peak
        ),
        1e-12,
        None,
    )

    w_shoulder = np.clip(
        np.abs(
            height_shoulder
        ),
        1e-12,
        None,
    )

    peak_fraction = float(
        w_peak.sum()
        /
        (
            w_peak.sum()
            + w_shoulder.sum()
        )
    )

    t_split = (
        peak_fraction
        * TOTAL_BURN_TIME_S
    )

    (
        t_peak,
        _peak_dwell,
    ) = weighted_bin_centers(
        0.0,
        t_split,
        w_peak,
    )

    (
        t_shoulder,
        _shoulder_dwell,
    ) = weighted_bin_centers(
        t_split,
        TOTAL_BURN_TIME_S,
        w_shoulder,
    )

    time_axis = np.concatenate(
        (
            [0.0],
            t_peak,
            t_shoulder,
            [
                TOTAL_BURN_TIME_S
            ],
        )
    )

    burn_frequency = (
        np.concatenate(
            (
                f_peak,
                f_shoulder,
            )
        )
    )

    frequency_offset = (
        burn_frequency
        - omegD
    )

    # Authoritative legacy convention:
    # numeric MHz offset is used as the physical WaveGen voltage.
    # Preserve it here, but mark the scaling unverified for hardware.
    voltage_control = (
        np.concatenate(
            (
                [-V_EDGE_V],
                frequency_offset,
                [V_EDGE_V],
            )
        )
    )

    t_uniform = np.linspace(
        time_axis.min(),
        time_axis.max(),
        MAX_ARB_POINTS,
    )

    voltage_uniform = np.interp(
        t_uniform,
        time_axis,
        voltage_control,
    )

    normalized = np.clip(
        voltage_uniform
        / V_EDGE_V,
        -1.0,
        1.0,
    )

    plan = WaveformPlan(
        material=material,
        case=case,
        fit_ts=snapshot.ts,
        fit_type=snapshot.fit_type,
        omegD_mhz=omegD,
        frequency_shift_mhz=shift,
        f_peak_mhz=f_peak,
        f_shoulder_mhz=f_shoulder,
        peak_weights=w_peak,
        shoulder_weights=w_shoulder,
        peak_time_fraction=peak_fraction,
        control_time_s=time_axis,
        control_offset_mhz=(
            np.concatenate(
                (
                    [-V_EDGE_V],
                    frequency_offset,
                    [V_EDGE_V],
                )
            )
        ),
        normalized_samples=(
            normalized.astype(
                np.float32
            )
        ),
        physical_voltage_v=(
            (
                normalized
                * V_EDGE_V
            ).astype(
                np.float32
            )
        ),
        period_s=(
            TOTAL_BURN_TIME_S
        ),
        vhigh_v=V_EDGE_V,
        vlow_v=-V_EDGE_V,
        r_high=r_high,
        used_measured_heights=(
            using_measured
        ),
    )

    plan.validate()

    return plan
