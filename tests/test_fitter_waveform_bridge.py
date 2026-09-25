import asyncio
import json
import unittest

import numpy as np

from online_fitter.vendored import (
    deuteron_model as fitter_model,
)

from ssrf_analysis.fitter_waveform_bridge import (
    DEFAULT_FIT_PUBLISH_HOST,
    DEFAULT_FIT_PUBLISH_PORT,
    FitBridgeError,
    FitWaveformBridge,
    decode_fit_line,
    validate_loopback_host,
)

from ssrf_analysis.waveform_stager import (
    WAVEFORM_CMD_PV,
    WaveformStager,
)


def nd3_constants():
    preset = fitter_model.MATERIALS[
        "deuterated ammonia (Dec 2024)"
    ]

    return {
        "source": "fit",
        "material": "ND3",
        "model": "single",
        "CC": 1.0,
        "CC_DAQ": 1.0,
        "red_chisq": 1.0,
        "omegD": 32.677988031,
        "omegQ1": preset["omegQ1"],
        "A1": preset["A1"],
        "eta1": preset["eta1"],
        "scale": preset["scale"],
        "phase": float(
            np.deg2rad(
                preset["phase_deg"]
            )
        ),
        "r": 1.5,
    }


def fit_message(
    *,
    constants_good=True,
):
    return {
        "type": "fit",
        "ts": "2026-09-25T10:00:00",
        "fit_type": "ssrf",
        "mode": "ssrf",
        "material": "ND3",
        "model": "single",
        "npts": 0,
        "freqs_mhz": None,
        "constants_good": constants_good,
        "constants": nd3_constants(),
        "data": None,
        "total_fit": None,
        "I_plus_primary": None,
        "I_minus_primary": None,
        "I_plus_secondary": None,
        "I_minus_secondary": None,
        "raw": None,
    }


class TestFitterWaveformBridge(
    unittest.TestCase
):

    def test_defaults_match_existing_publisher(
        self
    ):
        self.assertEqual(
            DEFAULT_FIT_PUBLISH_HOST,
            "127.0.0.1",
        )

        self.assertEqual(
            DEFAULT_FIT_PUBLISH_PORT,
            8778,
        )

    def test_non_loopback_host_is_rejected(
        self
    ):
        with self.assertRaises(ValueError):
            validate_loopback_host(
                "0.0.0.0"
            )

    def test_decode_fit_line(
        self
    ):
        message = fit_message()

        decoded = decode_fit_line(
            (
                json.dumps(message)
                + "\n"
            ).encode("utf-8")
        )

        self.assertEqual(
            decoded["type"],
            "fit",
        )

        self.assertTrue(
            decoded["constants_good"]
        )

    def test_non_fit_message_is_rejected(
        self
    ):
        with self.assertRaises(
            FitBridgeError
        ):
            decode_fit_line(
                b'{"type":"status"}\n'
            )

    def test_uncalibrated_fit_never_stages(
        self
    ):
        writes = []

        async def writer(
            pv,
            value,
        ):
            writes.append(
                (pv, value)
            )

        bridge = FitWaveformBridge(
            stager=WaveformStager(
                write=writer
            ),
            case="Enhancement",
        )

        with self.assertRaises(
            ValueError
        ):
            asyncio.run(
                bridge.process_message(
                    fit_message(
                        constants_good=False
                    )
                )
            )

        self.assertEqual(
            writes,
            [],
        )

    def test_calibrated_fit_generates_and_stages_once(
        self
    ):
        writes = []

        async def writer(
            pv,
            value,
        ):
            writes.append(
                (
                    pv,
                    np.asarray(
                        value,
                        dtype=np.float32,
                    ).copy(),
                )
            )

        bridge = FitWaveformBridge(
            stager=WaveformStager(
                write=writer
            ),
            case="Enhancement",
        )

        result = asyncio.run(
            bridge.process_message(
                fit_message()
            )
        )

        self.assertEqual(
            len(writes),
            1,
        )

        self.assertEqual(
            writes[0][0],
            WAVEFORM_CMD_PV,
        )

        self.assertEqual(
            writes[0][1].size,
            8192,
        )

        self.assertEqual(
            result.plan.waveform_points,
            8192,
        )

        self.assertEqual(
            result.receipt.points,
            8192,
        )

        self.assertFalse(
            result.plan
            .scaling_verified_for_hardware
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
