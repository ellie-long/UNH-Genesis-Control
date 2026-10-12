"""Genesis master scalar archive engine.

This module does not communicate with instruments or EPICS.
A separate read-only subscriber supplies published PV updates.
"""

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from archive.master_storage import (
    WriterLease, verify_metadata, verify_appendable,
)


UTC = timezone.utc
LOCAL = ZoneInfo("America/New_York")
MIN_INTERVAL_S = 1.0


def aware_utc(value=None):
    if value is None:
        value = datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include timezone information")
    return value.astimezone(UTC)


def iso_utc(value):
    return aware_utc(value).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def iso_local(value):
    return aware_utc(value).astimezone(LOCAL).isoformat(
        timespec="milliseconds"
    )


def scalar(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    raise TypeError(
        "Master scalar archive requires a scalar value; "
        "store arrays as timestamped external session products"
    )


class MasterRecorder:
    """Append-only native events and synchronized wide CSV snapshots."""

    def __init__(
        self, output_dir, registry,
        interval_s=1.0, stale_after_s=60.0
    ):
        interval_s = float(interval_s)
        if not math.isfinite(interval_s) or interval_s < MIN_INTERVAL_S:
            raise ValueError(
                "Snapshot interval must be at least 1 second"
            )
        if not registry:
            raise ValueError("PV registry cannot be empty")
        if stale_after_s <= 0:
            raise ValueError("Stale threshold must be positive")

        self.root = Path(output_dir)
        self.registry = dict(registry)
        self.names = sorted(self.registry)
        self.interval_s = interval_s
        self.stale_after_s = float(stale_after_s)
        self.latest = {}
        self.last_snapshot = None
        self.handles = {}
        self.closed = False
        self._writer_lease = WriterLease(self.root)

    def _directory(self, when):
        directory = self.root / aware_utc(when).date().isoformat()
        directory.mkdir(parents=True, exist_ok=True)
        metadata = directory / "metadata.json"
        expected = {
            "schema_version": 1,
            "timezone": "America/New_York",
            "timestamp_standard": "UTC",
            "snapshot_interval_s": self.interval_s,
            "pv_registry": self.registry,
        }
        verify_metadata(metadata, expected)
        return directory

    def _csv_columns(self):
        columns = ["timestamp_utc", "timestamp_local"]
        for name in self.names:
            columns.extend([
                name + "|value",
                name + "|quality",
                name + "|age_s",
                name + "|received_utc",
                name + "|source_utc",
            ])
        return columns

    def _handle(self, when, kind):
        directory = self._directory(when)
        key = (str(directory), kind)

        if key not in self.handles:
            if kind == "events":
                path = directory / "genesis_master.jsonl"
                verify_appendable(path)
                handle = path.open("a", encoding="utf-8")
                self.handles[key] = (handle, None)
            else:
                path = directory / "genesis_master.csv"
                existed = path.exists() and path.stat().st_size > 0

                if existed:
                    verify_appendable(path)
                    with path.open(
                        newline="", encoding="utf-8"
                    ) as previous:
                        observed = next(csv.reader(previous), None)

                    if observed != self._csv_columns():
                        raise RuntimeError(
                            "Master CSV column schema mismatch; "
                            "a new dataset segment is required"
                        )

                handle = path.open(
                    "a", newline="", encoding="utf-8"
                )
                writer = csv.writer(handle)
                if not existed:
                    writer.writerow(self._csv_columns())
                    handle.flush()
                self.handles[key] = (handle, writer)

        return self.handles[key]

    def record(
        self, pv, value, *,
        at=None, source_time=None, connected=True
    ):
        if self.closed:
            raise RuntimeError("Recorder is closed")
        if pv not in self.registry:
            raise KeyError(pv)

        at = aware_utc(at)
        source = iso_utc(source_time) if source_time else None
        value = scalar(value)
        connected = bool(connected)

        record = {
            "kind": "pv_update",
            "pv": pv,
            "value": value,
            "connected": connected,
            "received_utc": iso_utc(at),
            "received_local": iso_local(at),
            "source_utc": source,
        }

        handle, _ = self._handle(at, "events")
        handle.write(json.dumps(record, allow_nan=False) + "\n")
        handle.flush()

        self.latest[pv] = {
            "value": value,
            "at": at,
            "source": source,
            "connected": connected,
        }

    def snapshot(self, at=None):
        if self.closed:
            raise RuntimeError("Recorder is closed")

        at = aware_utc(at)

        if self.last_snapshot is not None:
            elapsed = (at - self.last_snapshot).total_seconds()
            if elapsed < self.interval_s - 1e-6:
                raise ValueError("Snapshot interval violated")

        row = [iso_utc(at), iso_local(at)]

        for name in self.names:
            state = self.latest.get(name)
            if state is None or state["at"] > at:
                row.extend(["", "missing", "", "", ""])
                continue

            age = max(0.0, (at - state["at"]).total_seconds())

            if not state["connected"]:
                quality = "disconnected"
            elif age > self.stale_after_s:
                quality = "stale"
            else:
                quality = "valid"

            row.extend([
                state["value"],
                quality,
                round(age, 6),
                iso_utc(state["at"]),
                state["source"] or "",
            ])

        handle, writer = self._handle(at, "snapshots")
        writer.writerow(row)
        handle.flush()

        self.last_snapshot = at

    def export_json(self, date):
        """Stream a full-day JSON export without loading all rows in RAM."""
        directory = self.root / str(date)
        metadata = directory / "metadata.json"
        csv_path = directory / "genesis_master.csv"
        jsonl_path = directory / "genesis_master.jsonl"
        output = directory / "genesis_master.json"

        if not metadata.exists():
            raise FileNotFoundError(metadata)

        # Export only finalized days, after closing active file handles.
        if not self.closed:
            raise RuntimeError("Close the recorder before JSON export")

        with output.open("w", encoding="utf-8") as dest:
            dest.write('{"metadata":')
            dest.write(metadata.read_text().strip())
            dest.write(',"snapshots":[')

            first = True
            if csv_path.exists():
                with csv_path.open(newline="", encoding="utf-8") as src:
                    for record in csv.DictReader(src):
                        if not first:
                            dest.write(",")
                        dest.write(json.dumps(record, allow_nan=False))
                        first = False

            dest.write('],"events":[')
            first = True

            if jsonl_path.exists():
                with jsonl_path.open(encoding="utf-8") as src:
                    for line in src:
                        if line.strip():
                            if not first:
                                dest.write(",")
                            dest.write(line.strip())
                            first = False

            dest.write("]}\n")

        return output

    def close(self):
        if self.closed:
            return

        try:
            for handle, _ in self.handles.values():
                handle.flush()
                handle.close()
        finally:
            self.handles.clear()
            self.closed = True
            self._writer_lease.close()
