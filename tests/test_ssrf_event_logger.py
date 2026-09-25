from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ssrf_analysis.event_logger import (
    EVENT_SCHEMA_VERSION,
    EventLogError,
    JsonlEventLogger,
    new_session_id,
    read_jsonl,
)


class TestSsrfEventLogger(
    unittest.TestCase
):

    def test_session_id_is_namespaced(
        self
    ):
        sid = new_session_id(
            subsystem="ssrf",
            now=datetime(
                2026,
                9,
                25,
                17,
                0,
                tzinfo=timezone.utc,
            ),
        )

        self.assertTrue(
            sid.startswith(
                "ssrf-20260925T170000Z-"
            )
        )

    def test_append_creates_daily_jsonl(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            logger = JsonlEventLogger(
                directory=tmp
            )

            record = logger.append(
                event_type="waveform_planned",
                source="test",
                session_id="ssrf-test-session",
                timestamp=(
                    "2026-09-25T17:00:00Z"
                ),
                event_id="event-001",
                data={
                    "material": "ND3",
                    "points": 8192,
                    "ready": False,
                },
            )

            path = (
                Path(tmp)
                / "2026-09-25.jsonl"
            )

            self.assertTrue(
                path.is_file()
            )

            rows = read_jsonl(
                path
            )

            self.assertEqual(
                len(rows),
                1,
            )

            self.assertEqual(
                rows[0]["schema_version"],
                EVENT_SCHEMA_VERSION,
            )

            self.assertEqual(
                rows[0]["event_id"],
                "event-001",
            )

            self.assertEqual(
                rows[0]["session_id"],
                "ssrf-test-session",
            )

            self.assertEqual(
                rows[0]["data"]["points"],
                8192,
            )

            self.assertEqual(
                record.timestamp_utc,
                "2026-09-25T17:00:00.000Z",
            )

    def test_append_is_append_only(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            logger = JsonlEventLogger(
                directory=tmp
            )

            for index in range(3):
                logger.append(
                    event_type="test_event",
                    source="test",
                    session_id="session-1",
                    timestamp=(
                        "2026-09-25T17:00:00Z"
                    ),
                    event_id=f"event-{index}",
                    data={
                        "index": index,
                    },
                )

            rows = read_jsonl(
                Path(tmp)
                / "2026-09-25.jsonl"
            )

            self.assertEqual(
                [
                    row["data"]["index"]
                    for row in rows
                ],
                [0, 1, 2],
            )

    def test_numpy_scalars_are_serialized(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            logger = JsonlEventLogger(
                directory=tmp
            )

            logger.append(
                event_type="fit_received",
                source="test",
                session_id="session-1",
                timestamp=(
                    "2026-09-25T17:00:00Z"
                ),
                data={
                    "r": np.float64(1.5),
                    "points": np.int64(8192),
                    "valid": np.bool_(True),
                },
            )

            rows = read_jsonl(
                Path(tmp)
                / "2026-09-25.jsonl"
            )

            self.assertEqual(
                rows[0]["data"]["r"],
                1.5,
            )

            self.assertEqual(
                rows[0]["data"]["points"],
                8192,
            )

            self.assertIs(
                rows[0]["data"]["valid"],
                True,
            )

    def test_nonfinite_float_is_rejected(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            logger = JsonlEventLogger(
                directory=tmp
            )

            with self.assertRaises(
                EventLogError
            ):
                logger.append(
                    event_type="bad_event",
                    source="test",
                    session_id="session-1",
                    data={
                        "value": np.nan,
                    },
                )

            self.assertEqual(
                list(
                    Path(tmp).glob(
                        "*.jsonl"
                    )
                ),
                [],
            )

    def test_arbitrary_object_is_rejected(
        self
    ):
        with tempfile.TemporaryDirectory() as tmp:
            logger = JsonlEventLogger(
                directory=tmp
            )

            with self.assertRaises(
                EventLogError
            ):
                logger.append(
                    event_type="bad_event",
                    source="test",
                    session_id="session-1",
                    data={
                        "object": object(),
                    },
                )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
