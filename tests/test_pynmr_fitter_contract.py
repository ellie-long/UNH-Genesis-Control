"""PyNMR -> Genesis online-fitter wire-contract regression tests.

Reference producer:
    unhpoltarg/jlab_pynmr
    branch: unh
    inspected baseline: 7d9fa52a7d46e491150498c9735e7b851ea3e8d1

The test deliberately does not import the PyNMR repository.  It captures the
stable message boundary emitted by unh/data_streamer.py so Genesis can verify
compatibility independently.

This file tests transport/data ownership only.  It does not decide whether
PyNMR ``basesub`` or ``fitsub`` is the physically preferred signal for the
Genesis fitter; that is validated separately.
"""

import json
import unittest

import numpy as np

from online_fitter import protocol
from online_fitter import server


NPTS = 512
CENTFREQ_MHZ = 32.7
MOD_FREQ_KHZ = 400.0


def pynmr_frequency_axis():
    """Reproduce PyNMR Config.freq_list for its standard 512-step sweep."""
    freq_ints = np.linspace(
        -32768,
        32767,
        num=NPTS,
    ).astype(np.int32)

    return (
        CENTFREQ_MHZ
        + (MOD_FREQ_KHZ / 1000.0)
        * freq_ints
        / 32768.0
    )


def streamer_axis_params(freqs):
    """Reproduce unh.data_streamer.axis_params()."""
    freqs = np.asarray(freqs, dtype=float)
    step = (freqs[-1] - freqs[0]) / (len(freqs) - 1)
    span = step * len(freqs)
    centfreq = freqs[0] + span / 2.0
    return float(centfreq), float(span)


def pynmr_message():
    """Representative message emitted by PyNMR unh/data_streamer.py."""
    freqs = pynmr_frequency_axis()
    centfreq_mhz, span_mhz = streamer_axis_params(freqs)

    x = np.linspace(-1.0, 1.0, NPTS)
    raw = 0.5 + 0.1 * x + np.exp(-(x**2) / 0.01)
    basesub = 0.1 * x + np.exp(-(x**2) / 0.01)
    fitsub = np.exp(-(x**2) / 0.01)

    return {
        "type": "sweep",
        "ts": "6/17/2026 9:31:27 PM",
        "centfreq_mhz": centfreq_mhz,
        "span_mhz": span_mhz,
        "npts": NPTS,
        "raw": raw.tolist(),
        "baseline_sub": fitsub.tolist(),
        "source": "pynmr",
        "uniform_axis": True,
        "freqs_mhz": freqs.tolist(),
        "basesub": basesub.tolist(),
        "fitsub": fitsub.tolist(),
        "amplitude_scale": 1.0,
        "channel": "Deuteron5T",
        "cent_freq_mhz": CENTFREQ_MHZ,
        "mod_freq_khz": MOD_FREQ_KHZ,
        "sweeps": 640,
        "pynmr_area": 12.5,
        "pynmr_pol": -1.0,
        "pynmr_cc": -0.08,
        "stop_stamp": 1.7e9,
    }


class FakeEngine:
    def __init__(self):
        self.calls = []

    def fit_line(
        self,
        freqs,
        baseline_sub,
        mode,
        span=None,
        centfreq=None,
    ):
        self.calls.append(
            {
                "freqs": np.asarray(freqs, dtype=float).copy(),
                "baseline_sub": np.asarray(
                    baseline_sub,
                    dtype=float,
                ).copy(),
                "mode": mode,
                "span": span,
                "centfreq": centfreq,
            }
        )

        return {
            "fit_type": mode,
            "mode": mode,
        }


class FakeState:
    def __init__(self):
        self.engine = FakeEngine()
        self.publisher = None
        self.n_received = 0
        self.results = []

    def get_mode(self):
        return "baseline"

    def push_result(self, item):
        self.results.append(item)


class TestPyNMRFitterContract(unittest.TestCase):

    def test_512_point_pynmr_message_decodes(self):
        message = pynmr_message()

        wire = (
            json.dumps(message)
            + "\r\n"
        ).encode("utf-8")

        decoded = protocol.decode_message(
            wire.strip()
        )

        freqs, raw, baseline_sub = (
            protocol.sweep_arrays(decoded)
        )

        self.assertEqual(decoded["source"], "pynmr")
        self.assertEqual(decoded["npts"], NPTS)

        self.assertEqual(freqs.size, NPTS)
        self.assertEqual(raw.size, NPTS)
        self.assertEqual(baseline_sub.size, NPTS)

        np.testing.assert_allclose(
            raw,
            message["raw"],
        )

        np.testing.assert_allclose(
            baseline_sub,
            message["baseline_sub"],
        )

        one_sweep_integer_mhz = (
            MOD_FREQ_KHZ
            / 1000.0
            / 32768.0
        )

        np.testing.assert_allclose(
            freqs,
            message["freqs_mhz"],
            rtol=0.0,
            atol=one_sweep_integer_mhz,
        )

        self.assertLess(
            one_sweep_integer_mhz,
            0.01
            * message["span_mhz"]
            / message["npts"],
        )

    def test_pynmr_extra_metadata_is_accepted(self):
        message = pynmr_message()

        decoded = protocol.decode_message(
            json.dumps(message).encode("utf-8")
        )

        self.assertEqual(
            decoded["channel"],
            "Deuteron5T",
        )
        self.assertEqual(
            decoded["sweeps"],
            640,
        )
        self.assertIn("basesub", decoded)
        self.assertIn("fitsub", decoded)
        self.assertIn("freqs_mhz", decoded)

        freqs, raw, baseline_sub = (
            protocol.sweep_arrays(decoded)
        )

        self.assertEqual(freqs.size, NPTS)
        self.assertEqual(raw.size, NPTS)
        self.assertEqual(baseline_sub.size, NPTS)

    def test_server_passes_pynmr_signal_unchanged_to_engine(self):
        message = pynmr_message()
        state = FakeState()

        reply = server._handle_sweep(
            state,
            message,
        )

        self.assertTrue(reply["ok"])
        self.assertEqual(
            reply["fit_type"],
            "baseline",
        )
        self.assertEqual(
            state.n_received,
            1,
        )
        self.assertEqual(
            len(state.engine.calls),
            1,
        )

        call = state.engine.calls[0]

        self.assertEqual(
            call["mode"],
            "baseline",
        )
        self.assertAlmostEqual(
            call["span"],
            message["span_mhz"],
        )
        self.assertAlmostEqual(
            call["centfreq"],
            message["centfreq_mhz"],
        )

        np.testing.assert_allclose(
            call["baseline_sub"],
            message["baseline_sub"],
        )

        one_sweep_integer_mhz = (
            MOD_FREQ_KHZ
            / 1000.0
            / 32768.0
        )

        np.testing.assert_allclose(
            call["freqs"],
            message["freqs_mhz"],
            rtol=0.0,
            atol=one_sweep_integer_mhz,
        )

        self.assertEqual(
            len(state.results),
            1,
        )
        self.assertEqual(
            state.results[0]["raw"].size,
            NPTS,
        )
        self.assertEqual(
            state.results[0]["baseline_sub"].size,
            NPTS,
        )

    def test_legacy_sweep_contract_remains_supported(self):
        npts = 16
        raw = np.linspace(0.0, 1.0, npts)
        baseline_sub = raw - 0.5

        wire = protocol.encode_sweep(
            32.7,
            0.4,
            raw,
            baseline_sub,
            ts="legacy-test",
        )

        decoded = protocol.decode_message(
            wire.strip()
        )

        freqs, received_raw, received_base = (
            protocol.sweep_arrays(decoded)
        )

        self.assertEqual(freqs.size, npts)

        np.testing.assert_allclose(
            received_raw,
            raw,
            atol=1e-8,
        )

        np.testing.assert_allclose(
            received_base,
            baseline_sub,
            atol=1e-8,
        )


if __name__ == "__main__":
    unittest.main()
