"""
Regression tests for explicit polarization provenance.

No physical hardware or EPICS connection is used.
"""

import inspect
import io
import unittest
from contextlib import redirect_stderr

from ssrf_analysis.fitter_stage_runtime import (
    build_parser,
)
from ssrf_analysis.fitter_waveform_bridge import (
    FitWaveformBridge,
)
from ssrf_analysis.waveform_planner import (
    build_waveform_plan,
)


class TestExplicitPolarizationGate(unittest.TestCase):

    def test_runtime_rejects_both_omitted(self):
        parser = build_parser()

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--case",
                        "Enhancement",
                    ]
                )

    def test_runtime_rejects_missing_high_polarization(self):
        parser = build_parser()

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--case",
                        "Enhancement",
                        "--polarization",
                        "0.30",
                    ]
                )

    def test_runtime_rejects_missing_polarization(self):
        parser = build_parser()

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--case",
                        "Enhancement",
                        "--high-polarization",
                        "0.30",
                    ]
                )

    def test_runtime_accepts_explicit_values(self):
        args = build_parser().parse_args(
            [
                "--case",
                "Enhancement",
                "--polarization",
                "0.31",
                "--high-polarization",
                "0.44",
            ]
        )

        self.assertEqual(
            args.polarization,
            0.31,
        )

        self.assertEqual(
            args.high_polarization,
            0.44,
        )

    def test_bridge_has_no_polarization_defaults(self):
        sig = inspect.signature(
            FitWaveformBridge
        )

        self.assertIs(
            sig.parameters[
                "polarization"
            ].default,
            inspect.Parameter.empty,
        )

        self.assertIs(
            sig.parameters[
                "high_polarization"
            ].default,
            inspect.Parameter.empty,
        )

    def test_planner_has_no_polarization_defaults(self):
        sig = inspect.signature(
            build_waveform_plan
        )

        self.assertIs(
            sig.parameters[
                "polarization"
            ].default,
            inspect.Parameter.empty,
        )

        self.assertIs(
            sig.parameters[
                "high_polarization"
            ].default,
            inspect.Parameter.empty,
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
