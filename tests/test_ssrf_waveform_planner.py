"""
Self-contained scientific regression tests for the Genesis ssRF waveform planner.

The numerical golden vectors under tests/fixtures were generated from the
verified legacy ssRF controller snapshot documented in the accompanying JSON
manifest. No external repository or hardware module is required to run these
tests.
"""

import json
import unittest
from pathlib import Path

import numpy as np

from ssrf_analysis.waveform_planner import (
    FitSnapshot,
    MAX_ARB_POINTS,
    WaveformPlanError,
    build_waveform_plan,
)


def chhetra_stepped_samples(plan):
    """Independent Oct-2026 Chhetra ARB reconstruction from plan bins/weights."""
    frequencies = np.concatenate((plan.f_peak_mhz, plan.f_shoulder_mhz))
    weights = np.concatenate((plan.peak_weights, plan.shoulder_weights))
    edges = np.cumsum(plan.period_s * weights / weights.sum())
    edges[-1] = plan.period_s
    t = np.linspace(0.0, plan.period_s, MAX_ARB_POINTS)
    indices = np.clip(np.searchsorted(edges, t, side="right"), 0, len(frequencies) - 1)
    volts = (frequencies - plan.omegD_mhz)[indices].copy()
    volts[0], volts[-1] = plan.vlow_v, plan.vhigh_v
    return np.clip(volts / plan.vhigh_v, -1.0, 1.0).astype(np.float32)


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
)

MANIFEST_PATH = (
    FIXTURE_DIR
    / "ssrf_waveform_legacy_reference.json"
)

GOLDEN_PATH = (
    FIXTURE_DIR
    / "ssrf_waveform_legacy_reference.npz"
)


MANIFEST = json.loads(
    MANIFEST_PATH.read_text(
        encoding="utf-8"
    )
)

with np.load(
    GOLDEN_PATH,
    allow_pickle=False,
) as fixture:
    GOLDEN = {
        key: fixture[key].copy()
        for key in fixture.files
    }


def constants_for(material):
    return {
        key: float(value)
        for key, value in (
            MANIFEST[
                "materials"
            ][material][
                "constants"
            ].items()
        )
    }


def model_for(material):
    return MANIFEST[
        "materials"
    ][material][
        "model"
    ]


def wire_material(material):
    return MANIFEST[
        "materials"
    ][material][
        "wire_material"
    ]


def fixture_prefix(
    material,
    case,
):
    return (
        material.lower()
        .replace("-", "_")
        .replace(" ", "_")
        + "__"
        + case.lower()
    )


def _build_plan_explicit(
    *args,
    **kwargs,
):
    kwargs.setdefault(
        "polarization",
        float(
            MANIFEST[
                "polarization"
            ]
        ),
    )

    kwargs.setdefault(
        "high_polarization",
        float(
            MANIFEST[
                "high_polarization"
            ]
        ),
    )

    return build_waveform_plan(
        *args,
        **kwargs,
    )


class TestWaveformPlanner(
    unittest.TestCase
):

    def test_all_material_case_combinations_follow_chhetra_steps(
        self
    ):
        self.assertEqual(
            MANIFEST[
                "schema_version"
            ],
            1,
        )

        self.assertFalse(
            MANIFEST[
                "source"
            ][
                "git_identity_available"
            ]
        )

        self.assertEqual(
            int(
                MANIFEST[
                    "max_arb_points"
                ]
            ),
            MAX_ARB_POINTS,
        )

        waveform_atol = float(
            MANIFEST[
                "comparison_tolerances"
            ][
                "waveform_atol"
            ]
        )

        frequency_atol = float(
            MANIFEST[
                "comparison_tolerances"
            ][
                "frequency_atol_mhz"
            ]
        )

        fraction_places = int(
            MANIFEST[
                "comparison_tolerances"
            ][
                "peak_time_fraction_places"
            ]
        )

        for material in (
            "ND3",
            "Butanol",
        ):
            for case in (
                "Enhancement",
                "Suppression",
                "Zero",
            ):
                with self.subTest(
                    material=material,
                    case=case,
                ):
                    constants = (
                        constants_for(
                            material
                        )
                    )

                    snapshot = (
                        FitSnapshot.from_message(
                            {
                                "material":
                                    wire_material(
                                        material
                                    ),
                                "model":
                                    model_for(
                                        material
                                    ),
                                "constants_good":
                                    True,
                                "constants":
                                    constants,
                            }
                        )
                    )

                    plan = (
                        _build_plan_explicit(
                            snapshot,
                            case=case,
                        )
                    )

                    prefix = (
                        fixture_prefix(
                            material,
                            case,
                        )
                    )

                    self.assertEqual(
                        plan.waveform_points,
                        MAX_ARB_POINTS,
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.normalized_samples,
                            chhetra_stepped_samples(plan),
                            rtol=0.0, atol=2e-7,
                        )
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.f_peak_mhz,
                            GOLDEN[
                                prefix
                                + "__f_peak_mhz"
                            ],
                            rtol=0.0,
                            atol=frequency_atol,
                        )
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.f_shoulder_mhz,
                            GOLDEN[
                                prefix
                                + "__f_shoulder_mhz"
                            ],
                            rtol=0.0,
                            atol=frequency_atol,
                        )
                    )

                    self.assertAlmostEqual(
                        plan.peak_time_fraction,
                        float(
                            GOLDEN[
                                prefix
                                + "__peak_time_fraction"
                            ][0]
                        ),
                        places=fraction_places,
                    )

                    self.assertFalse(
                        plan.used_measured_heights
                    )

                    self.assertFalse(
                        plan.scaling_verified_for_hardware
                    )

    def test_live_measured_height_path_follows_chhetra_steps(
        self
    ):
        constants = (
            constants_for(
                "ND3"
            )
        )

        freqs = GOLDEN[
            "measured_nd3_enhancement__freqs_mhz"
        ].copy()

        data = GOLDEN[
            "measured_nd3_enhancement__data"
        ].copy()

        snapshot = (
            FitSnapshot.from_message(
                {
                    "material":
                        wire_material(
                            "ND3"
                        ),
                    "model":
                        model_for(
                            "ND3"
                        ),
                    "constants_good":
                        True,
                    "constants":
                        constants,
                    "freqs_mhz":
                        freqs,
                    "data":
                        data,
                }
            )
        )

        plan = (
            _build_plan_explicit(
                snapshot,
                case="Enhancement",
            )
        )

        self.assertTrue(
            plan.used_measured_heights
        )

        self.assertTrue(
            np.allclose(
                plan.normalized_samples,
                chhetra_stepped_samples(plan),
                rtol=0.0, atol=2e-7,
            )
        )

    def test_uncalibrated_fit_is_rejected(
        self
    ):
        with self.assertRaises(
            WaveformPlanError
        ):
            FitSnapshot.from_message(
                {
                    "material":
                        "ND3",
                    "model":
                        "single",
                    "constants_good":
                        False,
                    "constants":
                        {},
                }
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
