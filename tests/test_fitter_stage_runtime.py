import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from online_fitter.vendored import (
    deuteron_model as fitter_model,
)

from ssrf_analysis.event_logger import (
    read_jsonl,
)

from ssrf_analysis.fitter_stage_runtime import (
    DryRunWriter,
    EpicsStageWriteError,
    EpicsWaveformWriter,
    build_parser,
    run_once,
)

from ssrf_analysis.waveform_stager import (
    WAVEFORM_CMD_PV,
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
        "ts": "2026-09-25T13:00:00",
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
        "red_chisq": 1.0,
    }


class TestFitterStageRuntime(
    unittest.TestCase
):

    def test_defaults_are_dry_run_loopback_and_logged(
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

        self.assertFalse(
            args.no_event_log
        )

        self.assertEqual(
            args.event_dir,
            "archive/events/ssrf",
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

    def test_dry_run_logs_correlated_session(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            parser = build_parser()

            args = parser.parse_args(
                [
                    "--case",
                    "Enhancement",
                    "--event-dir",
                    tmp,
                    "--session-id",
                    "ssrf-test-session",
                ]
            )

            with patch(
                "ssrf_analysis."
                "fitter_stage_runtime."
                "iter_fit_messages",
                return_value=iter(
                    [fit_message()]
                ),
            ):
                result = run_once(
                    args
                )

            files = list(
                Path(tmp).glob(
                    "*.jsonl"
                )
            )

            self.assertEqual(
                len(files),
                1,
            )

            rows = read_jsonl(
                files[0]
            )

            self.assertEqual(
                [
                    row["event_type"]
                    for row in rows
                ],
                [
                    "session_started",
                    "fit_received",
                    "waveform_planned",
                    "dry_run_completed",
                    "session_completed",
                ],
            )

            self.assertEqual(
                {
                    row["session_id"]
                    for row in rows
                },
                {
                    "ssrf-test-session"
                },
            )

            planned = rows[2]["data"]

            self.assertEqual(
                planned["waveform_points"],
                8192,
            )

            self.assertFalse(
                planned[
                    "scaling_verified_for_hardware"
                ]
            )

            completed = rows[3]["data"]

            self.assertEqual(
                completed["pv"],
                WAVEFORM_CMD_PV,
            )

            self.assertEqual(
                completed["points"],
                8192,
            )

            self.assertEqual(
                result.receipt.points,
                8192,
            )

    def test_failure_is_logged(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            parser = build_parser()

            args = parser.parse_args(
                [
                    "--case",
                    "Enhancement",
                    "--event-dir",
                    tmp,
                    "--session-id",
                    "ssrf-failure-session",
                ]
            )

            with patch(
                "ssrf_analysis."
                "fitter_stage_runtime."
                "iter_fit_messages",
                return_value=iter(
                    [
                        fit_message(
                            constants_good=False
                        )
                    ]
                ),
            ):
                with self.assertRaises(
                    ValueError
                ):
                    run_once(
                        args
                    )

            files = list(
                Path(tmp).glob(
                    "*.jsonl"
                )
            )

            self.assertEqual(
                len(files),
                1,
            )

            rows = read_jsonl(
                files[0]
            )

            self.assertEqual(
                [
                    row["event_type"]
                    for row in rows
                ],
                [
                    "session_started",
                    "fit_received",
                    "session_failed",
                ],
            )

            self.assertEqual(
                rows[-1]["level"],
                "ERROR",
            )

    def test_no_event_log_creates_no_event_files(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = (
                Path(tmp)
                / "events"
            )

            parser = build_parser()

            args = parser.parse_args(
                [
                    "--case",
                    "Enhancement",
                    "--event-dir",
                    str(target),
                    "--no-event-log",
                ]
            )

            with patch(
                "ssrf_analysis."
                "fitter_stage_runtime."
                "iter_fit_messages",
                return_value=iter(
                    [fit_message()]
                ),
            ):
                run_once(
                    args
                )

            self.assertFalse(
                target.exists()
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
