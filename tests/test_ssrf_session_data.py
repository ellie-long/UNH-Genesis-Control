import tempfile
import unittest
from pathlib import Path

import numpy as np

from ssrf_analysis.session_data import (
    SessionDataError,
    SessionDataWriter,
    load_npz_numeric,
    validate_session_id,
)


class FakePlan:
    waveform_points = 8192

    f_peak_mhz = np.linspace(
        32.63,
        32.65,
        41,
    )

    f_shoulder_mhz = np.linspace(
        32.68,
        32.72,
        41,
    )

    peak_weights = np.linspace(
        1.0,
        2.0,
        41,
    )

    shoulder_weights = np.linspace(
        2.0,
        1.0,
        41,
    )

    control_time_s = np.linspace(
        0.0,
        0.01,
        waveform_points,
        endpoint=False,
    )

    control_offset_mhz = np.linspace(
        -0.7,
        0.7,
        waveform_points,
    )

    normalized_samples = np.linspace(
        -1.0,
        1.0,
        waveform_points,
    )

    physical_voltage_v = np.linspace(
        -0.7,
        0.7,
        waveform_points,
    )


class TestSessionDataWriter(
    unittest.TestCase
):

    def test_safe_session_id(
        self
    ):
        self.assertEqual(
            validate_session_id(
                "ssrf-20260925T150000Z-abcd1234"
            ),
            "ssrf-20260925T150000Z-abcd1234",
        )

        for bad in (
            "",
            "..",
            "../escape",
            "a/b",
            "a b",
        ):
            with self.assertRaises(
                ValueError
            ):
                validate_session_id(
                    bad
                )

    def test_write_waveform_product(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            path = writer.write_waveform_data(
                session_id="session-1",
                plan=FakePlan(),
            )

            self.assertTrue(
                path.is_file()
            )

            data = load_npz_numeric(
                path
            )

            self.assertEqual(
                data[
                    "normalized_samples"
                ].size,
                8192,
            )

            self.assertEqual(
                data[
                    "physical_voltage_v"
                ].size,
                8192,
            )

            self.assertEqual(
                data[
                    "f_peak_mhz"
                ].size,
                41,
            )

    def test_write_fit_product(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            frequencies = np.linspace(
                32.60,
                32.75,
                101,
            )

            path = writer.write_fit_data(
                session_id="session-1",
                message={
                    "freqs_mhz": frequencies,
                    "data": np.ones(101),
                    "total_fit": np.ones(101) * 0.9,
                    "raw": np.arange(150),
                },
            )

            self.assertTrue(
                path.is_file()
            )

            data = load_npz_numeric(
                path
            )

            self.assertEqual(
                data["freqs_mhz"].size,
                101,
            )

            self.assertEqual(
                data["data"].size,
                101,
            )

            self.assertEqual(
                data["total_fit"].size,
                101,
            )

            self.assertEqual(
                data["raw"].size,
                150,
            )

    def test_absent_fit_arrays_create_no_file(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            path = writer.write_fit_data(
                session_id="session-1",
                message={
                    "freqs_mhz": None,
                    "data": None,
                    "raw": None,
                },
            )

            self.assertIsNone(
                path
            )

            self.assertFalse(
                (
                    Path(tmp)
                    / "session-1"
                    / "fit_data.npz"
                ).exists()
            )

    def test_mismatched_fit_arrays_rejected(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            with self.assertRaises(
                SessionDataError
            ):
                writer.write_fit_data(
                    session_id="session-1",
                    message={
                        "freqs_mhz": np.zeros(10),
                        "data": np.zeros(9),
                    },
                )

    def test_nonfinite_data_rejected(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            with self.assertRaises(
                SessionDataError
            ):
                writer.write_fit_data(
                    session_id="session-1",
                    message={
                        "freqs_mhz": np.array(
                            [1.0, np.nan]
                        )
                    },
                )

    def test_write_all_products(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionDataWriter(
                root=tmp
            )

            products = writer.write_products(
                session_id="session-1",
                message={
                    "freqs_mhz": np.arange(
                        10,
                        dtype=float,
                    ),
                    "data": np.arange(
                        10,
                        dtype=float,
                    ),
                },
                plan=FakePlan(),
            )

            self.assertEqual(
                products.session_id,
                "session-1",
            )

            self.assertTrue(
                Path(
                    products.fit_data_path
                ).is_file()
            )

            self.assertTrue(
                Path(
                    products.waveform_data_path
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
