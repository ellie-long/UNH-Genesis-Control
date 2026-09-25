import unittest

from ssrf_analysis.session_record import (
    SESSION_RECORD_SCHEMA_VERSION,
    SessionRecordError,
    build_session_record,
    group_session_records,
)


def event(
    *,
    event_id,
    event_type,
    timestamp,
    session_id="ssrf-session-1",
    subsystem="ssrf",
    source="test",
    level="INFO",
    data=None,
):
    return {
        "schema_version": 1,
        "event_id": event_id,
        "timestamp_utc": timestamp,
        "subsystem": subsystem,
        "session_id": session_id,
        "event_type": event_type,
        "source": source,
        "level": level,
        "data": dict(data or {}),
    }


class TestSessionRecord(
    unittest.TestCase
):

    def test_current_dry_run_session_builds_record(
        self
    ):
        rows = [
            event(
                event_id="e1",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
                data={"mode": "dry-run"},
            ),
            event(
                event_id="e2",
                event_type="fit_received",
                timestamp="2026-09-25T15:00:00.100Z",
                data={
                    "material": "ND3",
                    "omegD_mhz": 32.677988031,
                },
            ),
            event(
                event_id="e3",
                event_type="waveform_planned",
                timestamp="2026-09-25T15:00:00.200Z",
                data={
                    "case": "Enhancement",
                    "waveform_points": 8192,
                    "scaling_verified_for_hardware": False,
                },
            ),
            event(
                event_id="e4",
                event_type="dry_run_completed",
                timestamp="2026-09-25T15:00:00.300Z",
                data={
                    "points": 8192,
                },
            ),
            event(
                event_id="e5",
                event_type="session_completed",
                timestamp="2026-09-25T15:00:00.400Z",
                data={
                    "status": "success",
                },
            ),
        ]

        record = build_session_record(
            rows
        )

        self.assertEqual(
            record.schema_version,
            SESSION_RECORD_SCHEMA_VERSION,
        )

        self.assertEqual(
            record.status,
            "completed",
        )

        self.assertEqual(
            record.event_count,
            5,
        )

        self.assertEqual(
            record.fit["data"]["material"],
            "ND3",
        )

        self.assertEqual(
            record.waveform["data"][
                "waveform_points"
            ],
            8192,
        )

        self.assertEqual(
            record.staging["event_type"],
            "dry_run_completed",
        )

        self.assertIsNone(
            record.execution
        )

        self.assertIsNone(
            record.after_nmr
        )

    def test_future_full_burn_record_correlates_all_phases(
        self
    ):
        rows = [
            event(
                event_id="e1",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
            ),
            event(
                event_id="e2",
                event_type="before_nmr",
                timestamp="2026-09-25T15:00:01.000Z",
                data={
                    "data_path": "before.npz",
                },
            ),
            event(
                event_id="e3",
                event_type="fit_received",
                timestamp="2026-09-25T15:00:02.000Z",
            ),
            event(
                event_id="e4",
                event_type="waveform_planned",
                timestamp="2026-09-25T15:00:03.000Z",
            ),
            event(
                event_id="e5",
                event_type="waveform_staged",
                timestamp="2026-09-25T15:00:04.000Z",
            ),
            event(
                event_id="e6",
                event_type="burn_started",
                timestamp="2026-09-25T15:00:05.000Z",
            ),
            event(
                event_id="e7",
                event_type="burn_completed",
                timestamp="2026-09-25T15:00:06.000Z",
                data={
                    "result": "success",
                },
            ),
            event(
                event_id="e8",
                event_type="after_nmr",
                timestamp="2026-09-25T15:00:07.000Z",
                data={
                    "data_path": "after.npz",
                },
            ),
            event(
                event_id="e9",
                event_type="session_completed",
                timestamp="2026-09-25T15:00:08.000Z",
            ),
        ]

        record = build_session_record(
            rows
        )

        self.assertEqual(
            record.status,
            "completed",
        )

        self.assertIsNotNone(
            record.before_nmr
        )

        self.assertIsNotNone(
            record.fit
        )

        self.assertIsNotNone(
            record.waveform
        )

        self.assertIsNotNone(
            record.staging
        )

        self.assertEqual(
            record.execution["event_type"],
            "burn_completed",
        )

        self.assertIsNotNone(
            record.after_nmr
        )

    def test_failed_session_records_fault(
        self
    ):
        rows = [
            event(
                event_id="e1",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
            ),
            event(
                event_id="e2",
                event_type="fit_received",
                timestamp="2026-09-25T15:00:01.000Z",
            ),
            event(
                event_id="e3",
                event_type="session_failed",
                timestamp="2026-09-25T15:00:02.000Z",
                level="ERROR",
                data={
                    "error_type": "ValueError",
                },
            ),
        ]

        record = build_session_record(
            rows
        )

        self.assertEqual(
            record.status,
            "failed",
        )

        self.assertEqual(
            record.fault["event_type"],
            "session_failed",
        )

        self.assertEqual(
            record.completed_utc,
            "2026-09-25T15:00:02.000Z",
        )

    def test_mixed_session_ids_rejected(
        self
    ):
        rows = [
            event(
                event_id="e1",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
                session_id="s1",
            ),
            event(
                event_id="e2",
                event_type="session_completed",
                timestamp="2026-09-25T15:00:01.000Z",
                session_id="s2",
            ),
        ]

        with self.assertRaises(
            SessionRecordError
        ):
            build_session_record(
                rows
            )

    def test_duplicate_event_id_rejected(
        self
    ):
        rows = [
            event(
                event_id="same",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
            ),
            event(
                event_id="same",
                event_type="session_completed",
                timestamp="2026-09-25T15:00:01.000Z",
            ),
        ]

        with self.assertRaises(
            SessionRecordError
        ):
            build_session_record(
                rows
            )

    def test_group_session_records(
        self
    ):
        rows = [
            event(
                event_id="a1",
                event_type="session_started",
                timestamp="2026-09-25T15:00:00.000Z",
                session_id="a",
            ),
            event(
                event_id="a2",
                event_type="session_completed",
                timestamp="2026-09-25T15:00:01.000Z",
                session_id="a",
            ),
            event(
                event_id="b1",
                event_type="session_started",
                timestamp="2026-09-25T16:00:00.000Z",
                session_id="b",
            ),
        ]

        grouped = group_session_records(
            rows
        )

        self.assertEqual(
            set(grouped),
            {"a", "b"},
        )

        self.assertEqual(
            grouped["a"].status,
            "completed",
        )

        self.assertEqual(
            grouped["b"].status,
            "incomplete",
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
