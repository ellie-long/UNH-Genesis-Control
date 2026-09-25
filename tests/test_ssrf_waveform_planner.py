import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np


REFERENCE_ROOT = Path(
    "/Users/shrestha/project/unh/genesis/"
    "Development/genesis_reference/"
    "Genesis-Mission-ssRF-Controller-main_Utsav/"
    "Genesis-Mission-ssRF-Controller-main"
)

KEYSIGHT_REFERENCE = (
    REFERENCE_ROOT
    / "Keysight"
)


# The legacy module imports pyvisa at module load,
# even though these regression tests perform no hardware I/O.
try:
    import pyvisa  # noqa: F401
except ImportError:
    fake_pyvisa = types.ModuleType(
        "pyvisa"
    )
    fake_pyvisa.ResourceManager = (
        lambda *args, **kwargs: None
    )
    sys.modules[
        "pyvisa"
    ] = fake_pyvisa


if str(
    KEYSIGHT_REFERENCE
) not in sys.path:
    sys.path.insert(
        0,
        str(
            KEYSIGHT_REFERENCE
        ),
    )


import ssRF_sweep_height as legacy


from online_fitter.vendored import (
    deuteron_model as fitter_model,
)

from ssrf_analysis.waveform_planner import (
    FitSnapshot,
    MAX_ARB_POINTS,
    WaveformPlanError,
    build_waveform_plan,
)


def constants_for(
    material,
):

    if material == "ND3":

        preset = (
            fitter_model.MATERIALS[
                "deuterated ammonia "
                "(Dec 2024)"
            ]
        )

        return {
            "omegD":
                legacy.REFERENCE_F0[
                    "ND3"
                ],
            "omegQ1":
                preset["omegQ1"],
            "A1":
                preset["A1"],
            "eta1":
                preset["eta1"],
            "scale":
                preset["scale"],
            "phase":
                np.deg2rad(
                    preset[
                        "phase_deg"
                    ]
                ),
            "r": 1.5,
        }

    preset = (
        fitter_model.MATERIALS[
            "irradiated d-butanol "
            "(Dec 2025, 13th/15th)"
        ]
    )

    return {
        "omegD":
            legacy.REFERENCE_F0[
                "Butanol"
            ],
        "omegQ1":
            preset["omegQ1"],
        "omegQ2":
            preset["omegQ2"],
        "A1":
            preset["A1"],
        "eta1":
            preset["eta1"],
        "eta2":
            preset["eta2"],
        "K":
            preset["K"],
        "scale":
            preset["scale"],
        "phase":
            np.deg2rad(
                preset[
                    "phase_deg"
                ]
            ),
        "r": 1.5,
    }


def model_for(
    material,
):
    return (
        "single"
        if material == "ND3"
        else "double"
    )


def wire_material(
    material,
):
    return (
        "ND3"
        if material == "ND3"
        else "d-butanol"
    )


def legacy_waveform(
    material,
    case,
    constants,
    measured=None,
):

    controller = (
        legacy.SSRFController(
            material,
            case,
            P=0.30,
            P_high=0.30,
        )
    )

    controller.compute_reference_pattern()

    if measured is None:

        controller.live_freqs_meas = (
            None
        )

        controller.live_data_meas = (
            None
        )

    else:

        (
            controller.live_freqs_meas,
            controller.live_data_meas,
        ) = measured

    controller.omegD_fit = (
        constants["omegD"]
    )

    controller.frequency_shift = (
        controller.omegD_fit
        -
        legacy.REFERENCE_F0[
            controller.material
        ]
    )

    controller._load_material_constants(
        constants
    )

    controller.f_peak_reference = (
        controller.f_peak.copy()
    )

    controller.f_shoulder_reference = (
        controller.f_shoulder.copy()
    )

    controller.f_peak = (
        controller.f_peak
        + controller.frequency_shift
    )

    controller.f_shoulder = (
        controller.f_shoulder
        + controller.frequency_shift
    )

    with tempfile.TemporaryDirectory() as td:

        cwd = os.getcwd()

        os.chdir(td)

        try:

            waveform = (
                controller
                ._recompute_waveform_from_current_pattern()
            )

        finally:

            os.chdir(cwd)

    return (
        controller,
        waveform,
    )


def _build_plan_explicit(
    *args,
    **kwargs,
):
    """Legacy planner test fixture with explicit polarization inputs."""
    kwargs.setdefault(
        "polarization",
        0.30,
    )
    kwargs.setdefault(
        "high_polarization",
        0.30,
    )

    return build_waveform_plan(
        *args,
        **kwargs,
    )


class TestWaveformPlanner(
    unittest.TestCase
):

    def test_all_material_case_combinations_match_source(
        self
    ):

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

                    (
                        old,
                        old_waveform,
                    ) = legacy_waveform(
                        material,
                        case,
                        constants,
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

                    self.assertEqual(
                        plan.waveform_points,
                        MAX_ARB_POINTS,
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.normalized_samples,
                            old_waveform,
                            rtol=0.0,
                            atol=5e-8,
                        )
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.f_peak_mhz,
                            old.f_peak,
                            rtol=0.0,
                            atol=1e-12,
                        )
                    )

                    self.assertTrue(
                        np.allclose(
                            plan.f_shoulder_mhz,
                            old.f_shoulder,
                            rtol=0.0,
                            atol=1e-12,
                        )
                    )

                    self.assertAlmostEqual(
                        plan.peak_time_fraction,
                        old.PEAK_TIME_FRACTION,
                        places=14,
                    )

                    self.assertFalse(
                        plan.scaling_verified_for_hardware
                    )

    def test_live_measured_height_path_matches_source(
        self
    ):

        constants = (
            constants_for(
                "ND3"
            )
        )

        center = (
            constants["omegD"]
        )

        freqs = np.linspace(
            center - 0.4,
            center + 0.4,
            512,
        )

        data = (
            0.03
            +
            np.exp(
                -(
                    (
                        freqs
                        - (
                            center
                            - 0.08
                        )
                    )
                    / 0.05
                ) ** 2
            )
            +
            0.4
            * np.exp(
                -(
                    (
                        freqs
                        - (
                            center
                            + 0.12
                        )
                    )
                    / 0.08
                ) ** 2
            )
        )

        (
            _old,
            old_waveform,
        ) = legacy_waveform(
            "ND3",
            "Enhancement",
            constants,
            measured=(
                freqs,
                data,
            ),
        )

        snapshot = (
            FitSnapshot.from_message(
                {
                    "material":
                        "ND3",
                    "model":
                        "single",
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
                old_waveform,
                rtol=0.0,
                atol=5e-8,
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
