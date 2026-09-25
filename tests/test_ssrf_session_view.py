import tempfile
import unittest
from pathlib import Path

import numpy as np

from ssrf_analysis.event_logger import (
    JsonlEventLogger,
)

from archive.ssrf_session_view import (
    create_arb_figure,
    create_control_trajectory_figure,
    create_event_timeline_figure,
    create_nmr_fit_figure,
    create_weighting_figure,
    event_dataframe,
    load_event_records,
    load_session_bundle,
    resolve_product_path,
    session_summaries,
)


class TestSsrfSessionView(
    unittest.TestCase
):

    def _make_session(
        self,
        root,
    ):
        root = Path(root)

        event_dir = (
            root
            / "events"
        )

        session_root = (
            root
            / "sessions"
        )

        session_id = (
            "ssrf-view-test"
        )

        session_dir = (
            session_root
            / session_id
        )

        session_dir.mkdir(
            parents=True
        )

        fit_path = (
            session_dir
            / "fit_data.npz"
        )

        waveform_path = (
            session_dir
            / "waveform_data.npz"
        )

        freqs = np.linspace(
            32.60,
            32.75,
            201,
        )

        np.savez_compressed(
            fit_path,
            freqs_mhz=freqs,
            data=np.ones(201),
            total_fit=np.ones(201) * 0.9,
            raw=np.arange(250),
        )

        np.savez_compressed(
            waveform_path,
            f_peak_mhz=np.linspace(
                32.63,
                32.65,
                41,
            ),
            f_shoulder_mhz=np.linspace(
                32.68,
                32.72,
                41,
            ),
            peak_weights=np.ones(41),
            shoulder_weights=np.ones(41) * 2.0,
            control_time_s=np.linspace(
                0.0,
                0.01,
                84,
                endpoint=False,
            ),
            control_offset_mhz=np.linspace(
                -0.7,
                0.7,
                84,
            ),
            normalized_samples=np.linspace(
                -1.0,
                1.0,
                8192,
            ),
            physical_voltage_v=np.linspace(
                -0.7,
                0.7,
                8192,
            ),
        )

        logger = JsonlEventLogger(
            directory=event_dir
        )

        events = [
            (
                "session_started",
                {
                    "mode": "dry-run",
                    "case": "Enhancement",
                },
            ),
            (
                "fit_received",
                {
                    "material": "ND3",
                    "fit_ts": (
                        "2026-09-25T17:00:00"
                    ),
                    "fit_data_path": (
                        str(fit_path)
                    ),
                },
            ),
            (
                "waveform_planned",
                {
                    "material": "ND3",
                    "case": "Enhancement",
                    "waveform_points": 8192,
                    "period_s": 0.01,
                    "waveform_data_path": (
                        str(
                            waveform_path
                        )
                    ),
                    "scaling_verified_for_hardware": False,
                },
            ),
            (
                "dry_run_completed",
                {
                    "points": 8192,
                },
            ),
            (
                "session_completed",
                {
                    "status": "success",
                },
            ),
        ]

        for index, (
            event_type,
            data,
        ) in enumerate(events):
            logger.append(
                event_type=event_type,
                source="test",
                session_id=session_id,
                timestamp=(
                    "2026-09-25T17:00:"
                    f"0{index}Z"
                ),
                event_id=f"e{index}",
                data=data,
            )

        return (
            event_dir,
            session_id,
        )

    def test_load_and_summarize_session(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            event_dir, session_id = (
                self._make_session(
                    tmp
                )
            )

            records = (
                load_event_records(
                    event_dir
                )
            )

            self.assertEqual(
                len(records),
                5,
            )

            summaries = (
                session_summaries(
                    records
                )
            )

            self.assertEqual(
                len(summaries),
                1,
            )

            self.assertEqual(
                summaries[0][
                    "session_id"
                ],
                session_id,
            )

            self.assertEqual(
                summaries[0][
                    "status"
                ],
                "completed",
            )

            self.assertEqual(
                summaries[0][
                    "material"
                ],
                "ND3",
            )

            self.assertFalse(
                summaries[0][
                    "scaling_verified_for_hardware"
                ]
            )

    def test_bundle_loads_npz_products(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            event_dir, session_id = (
                self._make_session(
                    tmp
                )
            )

            bundle = (
                load_session_bundle(
                    event_dir=event_dir,
                    session_id=session_id,
                )
            )

            self.assertEqual(
                bundle[
                    "record"
                ].status,
                "completed",
            )

            self.assertEqual(
                bundle[
                    "fit_data"
                ][
                    "freqs_mhz"
                ].size,
                201,
            )

            self.assertEqual(
                bundle[
                    "waveform_data"
                ][
                    "control_time_s"
                ].size,
                84,
            )

            self.assertEqual(
                bundle[
                    "waveform_data"
                ][
                    "normalized_samples"
                ].size,
                8192,
            )

    def test_plot_builders_return_figures(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            event_dir, session_id = (
                self._make_session(
                    tmp
                )
            )

            bundle = (
                load_session_bundle(
                    event_dir=event_dir,
                    session_id=session_id,
                )
            )

            fit = bundle[
                "fit_data"
            ]

            waveform = bundle[
                "waveform_data"
            ]

            self.assertGreater(
                len(
                    create_nmr_fit_figure(
                        fit
                    ).data
                ),
                0,
            )

            self.assertGreater(
                len(
                    create_control_trajectory_figure(
                        waveform
                    ).data
                ),
                0,
            )

            self.assertGreater(
                len(
                    create_weighting_figure(
                        waveform
                    ).data
                ),
                0,
            )

            self.assertGreater(
                len(
                    create_arb_figure(
                        waveform,
                        period_s=0.01,
                    ).data
                ),
                0,
            )

            self.assertGreater(
                len(
                    create_event_timeline_figure(
                        bundle[
                            "events"
                        ]
                    ).data
                ),
                0,
            )

    def test_event_dataframe_has_five_rows(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            event_dir, session_id = (
                self._make_session(
                    tmp
                )
            )

            bundle = (
                load_session_bundle(
                    event_dir=event_dir,
                    session_id=session_id,
                )
            )

            frame = event_dataframe(
                bundle["events"]
            )

            self.assertEqual(
                len(frame),
                5,
            )

    def test_resolve_relative_product_path(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = resolve_product_path(
                "archive/sessions/test.npz",
                repo_root=tmp,
            )

            self.assertEqual(
                path,
                (
                    Path(tmp)
                    / "archive"
                    / "sessions"
                    / "test.npz"
                ).resolve(),
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
