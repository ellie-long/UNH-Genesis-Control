"""
Genesis Phase-I ssRF correlated session/burn record.

The append-only JSONL event stream remains the authoritative event history.
This module derives a compact session-level summary from those events.

It performs no EPICS access and no hardware access.

The record is designed to evolve from offline 5C.4A validation into later
5C.3B/5C.4B/5C.5 physical commissioning without changing the correlation
model.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping


SESSION_RECORD_SCHEMA_VERSION = 1


class SessionRecordError(RuntimeError):
    """Session events are inconsistent or insufficient."""


def _event_dict(
    event: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "event_id",
        "timestamp_utc",
        "subsystem",
        "session_id",
        "event_type",
        "source",
        "level",
        "data",
    }

    missing = (
        required
        - set(event)
    )

    if missing:
        raise SessionRecordError(
            "event missing required fields: "
            + ", ".join(
                sorted(missing)
            )
        )

    data = event["data"]

    if not isinstance(
        data,
        Mapping,
    ):
        raise SessionRecordError(
            "event data must be a mapping"
        )

    return {
        **dict(event),
        "data": dict(data),
    }


@dataclass(frozen=True)
class SessionRecord:
    schema_version: int
    subsystem: str
    session_id: str
    status: str
    started_utc: str | None
    completed_utc: str | None
    event_count: int
    event_ids: list[str]

    before_nmr: dict[str, Any] | None
    fit: dict[str, Any] | None
    waveform: dict[str, Any] | None
    staging: dict[str, Any] | None
    execution: dict[str, Any] | None
    fault: dict[str, Any] | None
    after_nmr: dict[str, Any] | None

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


def build_session_record(
    events: Iterable[
        Mapping[str, Any]
    ],
) -> SessionRecord:
    """
    Build one compact session summary from one session's event stream.

    The input event order is preserved. The JSONL stream itself remains the
    authoritative detailed history.
    """

    rows = [
        _event_dict(event)
        for event in events
    ]

    if not rows:
        raise SessionRecordError(
            "cannot build session record "
            "from zero events"
        )

    session_ids = {
        row["session_id"]
        for row in rows
    }

    if len(session_ids) != 1:
        raise SessionRecordError(
            "events contain multiple session IDs"
        )

    subsystems = {
        row["subsystem"]
        for row in rows
    }

    if len(subsystems) != 1:
        raise SessionRecordError(
            "events contain multiple subsystems"
        )

    event_ids = [
        str(row["event_id"])
        for row in rows
    ]

    if len(set(event_ids)) != len(
        event_ids
    ):
        raise SessionRecordError(
            "duplicate event_id found"
        )

    def first_event(
        event_type: str,
    ):
        for row in rows:
            if (
                row["event_type"]
                == event_type
            ):
                return row
        return None

    def last_event(
        event_types: set[str],
    ):
        for row in reversed(rows):
            if (
                row["event_type"]
                in event_types
            ):
                return row
        return None

    started = first_event(
        "session_started"
    )

    terminal = last_event(
        {
            "session_completed",
            "session_failed",
            "session_aborted",
        }
    )

    failed = first_event(
        "session_failed"
    )

    aborted = first_event(
        "session_aborted"
    )

    if failed is not None:
        status = "failed"
    elif aborted is not None:
        status = "aborted"
    elif terminal is not None:
        status = "completed"
    else:
        status = "incomplete"

    before_nmr_event = last_event(
        {
            "before_nmr",
            "nmr_before",
        }
    )

    fit_event = last_event(
        {
            "fit_received",
            "fit_completed",
        }
    )

    waveform_event = last_event(
        {
            "waveform_planned",
            "waveform_validated",
        }
    )

    staging_event = last_event(
        {
            "dry_run_completed",
            "waveform_staged",
            "waveform_loaded",
        }
    )

    execution_event = last_event(
        {
            "burn_started",
            "burn_completed",
            "burn_aborted",
            "execution_state",
        }
    )

    fault_event = last_event(
        {
            "fault",
            "interlock_fault",
            "session_failed",
        }
    )

    after_nmr_event = last_event(
        {
            "after_nmr",
            "nmr_after",
        }
    )

    def payload(
        row,
    ):
        if row is None:
            return None

        return {
            "event_id": row["event_id"],
            "timestamp_utc": (
                row["timestamp_utc"]
            ),
            "event_type": (
                row["event_type"]
            ),
            "source": row["source"],
            "level": row["level"],
            "data": row["data"],
        }

    return SessionRecord(
        schema_version=(
            SESSION_RECORD_SCHEMA_VERSION
        ),
        subsystem=next(
            iter(subsystems)
        ),
        session_id=next(
            iter(session_ids)
        ),
        status=status,
        started_utc=(
            started["timestamp_utc"]
            if started
            else rows[0]["timestamp_utc"]
        ),
        completed_utc=(
            terminal["timestamp_utc"]
            if terminal
            else None
        ),
        event_count=len(rows),
        event_ids=event_ids,
        before_nmr=payload(
            before_nmr_event
        ),
        fit=payload(
            fit_event
        ),
        waveform=payload(
            waveform_event
        ),
        staging=payload(
            staging_event
        ),
        execution=payload(
            execution_event
        ),
        fault=payload(
            fault_event
        ),
        after_nmr=payload(
            after_nmr_event
        ),
    )


def group_session_records(
    events: Iterable[
        Mapping[str, Any]
    ],
) -> dict[str, SessionRecord]:
    """Build one derived record per session ID."""

    grouped: dict[
        str,
        list[Mapping[str, Any]],
    ] = {}

    for event in events:
        row = _event_dict(
            event
        )

        grouped.setdefault(
            str(row["session_id"]),
            [],
        ).append(row)

    return {
        session_id: build_session_record(
            rows
        )
        for session_id, rows
        in grouped.items()
    }
