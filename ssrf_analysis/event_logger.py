"""
Genesis Phase-I structured ssRF event logging.

This module provides an append-only JSONL event layer that complements the
existing per-PV CSV archiver.

It performs no EPICS access and no hardware access.

The event log is metadata-oriented. Large waveform/NMR arrays should remain
in their native data products; events should record identifiers, dimensions,
fit parameters, paths, and state transitions needed to correlate them.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import numpy as np


EVENT_SCHEMA_VERSION = 1
DEFAULT_SUBSYSTEM = "ssrf"
DEFAULT_EVENT_DIR = Path("archive/events/ssrf")


class EventLogError(RuntimeError):
    """Structured event could not be validated or written."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | str | None = None) -> str:
    if value is None:
        dt = _utc_now()
    elif isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("timestamp must not be empty")

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    dt = dt.astimezone(timezone.utc)

    return dt.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def new_session_id(
    *,
    subsystem: str = DEFAULT_SUBSYSTEM,
    now: datetime | None = None,
) -> str:
    subsystem = str(subsystem).strip().lower()

    if not subsystem:
        raise ValueError(
            "subsystem must not be empty"
        )

    dt = (
        _utc_now()
        if now is None
        else now
    )

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    stamp = dt.astimezone(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    return (
        f"{subsystem}-{stamp}-"
        f"{uuid4().hex[:8]}"
    )


def _json_safe(value: Any) -> Any:
    """
    Convert common scientific-Python scalar/container values to JSON-safe
    representations without silently serializing arbitrary objects.
    """

    if value is None:
        return None

    if isinstance(
        value,
        (str, bool, int, float),
    ):
        if isinstance(
            value,
            float,
        ) and not np.isfinite(value):
            raise EventLogError(
                "event data contains non-finite float"
            )
        return value

    if isinstance(
        value,
        np.generic,
    ):
        return _json_safe(
            value.item()
        )

    if isinstance(
        value,
        Path,
    ):
        return str(value)

    if isinstance(
        value,
        datetime,
    ):
        return _iso_utc(value)

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(
        value,
        (list, tuple),
    ):
        return [
            _json_safe(item)
            for item in value
        ]

    raise EventLogError(
        "unsupported event-data type: "
        f"{type(value).__name__}"
    )


@dataclass(frozen=True)
class EventRecord:
    schema_version: int
    event_id: str
    timestamp_utc: str
    subsystem: str
    session_id: str
    event_type: str
    source: str
    level: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlEventLogger:
    """
    Append-only daily JSONL logger.

    Default output:
        archive/events/ssrf/YYYY-MM-DD.jsonl
    """

    def __init__(
        self,
        *,
        directory: str | Path = DEFAULT_EVENT_DIR,
        subsystem: str = DEFAULT_SUBSYSTEM,
    ):
        self.directory = Path(
            directory
        )

        self.subsystem = str(
            subsystem
        ).strip().lower()

        if not self.subsystem:
            raise ValueError(
                "subsystem must not be empty"
            )

    def _path_for_timestamp(
        self,
        timestamp_utc: str,
    ) -> Path:
        date_part = timestamp_utc[:10]

        return (
            self.directory
            / f"{date_part}.jsonl"
        )

    def make_record(
        self,
        *,
        event_type: str,
        source: str,
        data: Mapping[str, Any] | None = None,
        session_id: str,
        level: str = "INFO",
        timestamp: datetime | str | None = None,
        event_id: str | None = None,
    ) -> EventRecord:

        event_type = str(
            event_type
        ).strip()

        source = str(
            source
        ).strip()

        session_id = str(
            session_id
        ).strip()

        level = str(
            level
        ).strip().upper()

        if not event_type:
            raise ValueError(
                "event_type must not be empty"
            )

        if not source:
            raise ValueError(
                "source must not be empty"
            )

        if not session_id:
            raise ValueError(
                "session_id must not be empty"
            )

        if not level:
            raise ValueError(
                "level must not be empty"
            )

        timestamp_utc = _iso_utc(
            timestamp
        )

        clean_data = _json_safe(
            dict(data or {})
        )

        if not isinstance(
            clean_data,
            dict,
        ):
            raise EventLogError(
                "event data must resolve to a JSON object"
            )

        resolved_event_id = (
            str(event_id).strip()
            if event_id is not None
            else uuid4().hex
        )

        if not resolved_event_id:
            raise ValueError(
                "event_id must not be empty"
            )

        return EventRecord(
            schema_version=(
                EVENT_SCHEMA_VERSION
            ),
            event_id=(
                resolved_event_id
            ),
            timestamp_utc=(
                timestamp_utc
            ),
            subsystem=(
                self.subsystem
            ),
            session_id=(
                session_id
            ),
            event_type=(
                event_type
            ),
            source=source,
            level=level,
            data=clean_data,
        )

    def append(
        self,
        *,
        event_type: str,
        source: str,
        data: Mapping[str, Any] | None = None,
        session_id: str,
        level: str = "INFO",
        timestamp: datetime | str | None = None,
        event_id: str | None = None,
    ) -> EventRecord:

        record = self.make_record(
            event_type=event_type,
            source=source,
            data=data,
            session_id=session_id,
            level=level,
            timestamp=timestamp,
            event_id=event_id,
        )

        path = self._path_for_timestamp(
            record.timestamp_utc
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        line = json.dumps(
            record.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

        try:
            with path.open(
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    line + "\n"
                )
                handle.flush()
        except OSError as exc:
            raise EventLogError(
                f"failed to append event log: {path}"
            ) from exc

        return record


def read_jsonl(
    path: str | Path,
) -> list[dict[str, Any]]:
    """Read a JSONL file for tests/offline inspection."""

    path = Path(path)

    if not path.exists():
        return []

    records = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line_number, raw in enumerate(
            handle,
            start=1,
        ):
            raw = raw.strip()

            if not raw:
                continue

            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EventLogError(
                    "invalid JSONL at "
                    f"{path}:{line_number}"
                ) from exc

            if not isinstance(
                item,
                dict,
            ):
                raise EventLogError(
                    "event JSONL record must be "
                    f"an object at {path}:{line_number}"
                )

            records.append(item)

    return records
