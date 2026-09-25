import asyncio
import unittest
from unittest.mock import patch

import numpy as np

from ssrf_analysis.fitter_stage_runtime import (
    DryRunWriter,
    EpicsStageWriteError,
    EpicsWaveformWriter,
    build_parser,
)

from ssrf_analysis.waveform_stager import (
    WAVEFORM_CMD_PV,
)


class TestFitterStageRuntime(
    unittest.TestCase
):

    def test_defaults_are_dry_run_and_loopback(
        self
    ):
        parser = build_parser()

        args = parser.parse_args(
            [
                "--case",
                "Enhancement",
            ]
        )

        self.assertEqual(
            args.host,
            "127.0.0.1",
        )

        self.assertEqual(
            args.port,
            8778,
        )

        self.assertFalse(
            args.stage
        )

    def test_dry_run_writer_never_calls_epics(
        self
    ):
        writer = DryRunWriter()

        values = np.zeros(
            8192,
            dtype=np.float32,
        )

        asyncio.run(
            writer(
                WAVEFORM_CMD_PV,
                values,
            )
        )

        self.assertEqual(
            writer.calls,
            1,
        )

    def test_dry_run_rejects_other_pv(
        self
    ):
        writer = DryRunWriter()

        with self.assertRaises(
            EpicsStageWriteError
        ):
            asyncio.run(
                writer(
                    "UNH:GEN:SSRF:WGEN:LOAD_CMD",
                    1,
                )
            )

    def test_epics_writer_rejects_other_pv_before_caput(
        self
    ):
        writer = EpicsWaveformWriter()

        with patch(
            "ssrf_analysis."
            "fitter_stage_runtime."
            "aioca.caput"
        ) as caput:

            with self.assertRaises(
                EpicsStageWriteError
            ):
                asyncio.run(
                    writer(
                        "UNH:GEN:SSRF:WGEN:LOAD_CMD",
                        1,
                    )
                )

            caput.assert_not_called()

    def test_epics_writer_calls_only_waveform_cmd(
        self
    ):
        writer = EpicsWaveformWriter()

        values = np.zeros(
            8192,
            dtype=np.float32,
        )

        async def fake_caput(
            pv,
            value,
            *,
            wait,
            timeout,
        ):
            self.assertEqual(
                pv,
                WAVEFORM_CMD_PV,
            )
            self.assertTrue(
                wait
            )
            self.assertEqual(
                np.asarray(value).size,
                8192,
            )

        with patch(
            "ssrf_analysis."
            "fitter_stage_runtime."
            "aioca.caput",
            new=fake_caput,
        ):
            asyncio.run(
                writer(
                    WAVEFORM_CMD_PV,
                    values,
                )
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
