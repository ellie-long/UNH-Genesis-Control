"""Read-only Channel Access event collector for Genesis.

Uses aioca.camonitor. Never issues caput or physical device commands.
All callbacks enter a bounded queue; one task writes to MasterRecorder.
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import math


UTC = timezone.utc


def source_timestamp(raw):
    """Extract a timezone-aware source timestamp, if available."""
    timestamp = getattr(raw, "timestamp", None)

    if callable(timestamp):
        timestamp = timestamp()

    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(UTC)

    if isinstance(timestamp, (int, float)):
        if math.isfinite(timestamp):
            try:
                return datetime.fromtimestamp(timestamp, UTC)
            except (ValueError, OverflowError, OSError):
                return None

    return None


def ca_scalar(raw):
    """Normalize a CA scalar without silently flattening arrays."""
    value = getattr(raw, "value", raw)

    if hasattr(value, "ndim") and value.ndim != 0:
        raise TypeError("CA array requires native-array handling")

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    if not isinstance(value, (str, bool, int, float)):
        raise TypeError(
            f"Unsupported Channel Access value: {type(value)!r}"
        )

    return value


class MasterCACollector:
    """Single-consumer, bounded read-only EPICS CA monitor adapter."""

    def __init__(
        self, recorder, *,
        monitor_factory=None,
        max_queue=10000,
    ):
        if max_queue < 1:
            raise ValueError("max_queue must be positive")

        self.recorder = recorder
        self.monitor_factory = monitor_factory
        self.queue = asyncio.Queue(maxsize=max_queue)

        self.subscriptions = []
        self.worker = None
        self.running = False

        self.fatal_error = None
        self.accepted_events = 0
        self.written_events = 0
        self.dropped_events = 0

    def _fail(self, error):
        if self.fatal_error is None:
            self.fatal_error = error

    def check_health(self):
        if self.fatal_error is not None:
            raise RuntimeError(
                "Master CA collector fault; recording incomplete"
            ) from self.fatal_error

    def _callback(self, pv, raw):
        if not self.running or self.fatal_error is not None:
            return

        try:
            received = datetime.now(UTC)
            connected = raw is not None

            value = ca_scalar(raw) if connected else None
            timestamp = (
                source_timestamp(raw) if connected else None
            )

            event = (
                pv,
                value,
                received,
                timestamp,
                connected,
            )

            self.queue.put_nowait(event)
            self.accepted_events += 1

        except Exception as error:
            self.dropped_events += 1
            self._fail(error)

    async def _consume(self):
        while True:
            event = await self.queue.get()

            try:
                if self.fatal_error is None:
                    pv, value, received, source, connected = event

                    self.recorder.record(
                        pv,
                        value,
                        at=received,
                        source_time=source,
                        connected=connected,
                    )

                    self.written_events += 1
                else:
                    self.dropped_events += 1

            except Exception as error:
                self.dropped_events += 1
                self._fail(error)

            finally:
                self.queue.task_done()

    async def start(self):
        if self.running:
            raise RuntimeError("Collector already running")
        if self.worker is not None:
            raise RuntimeError("Collector cannot be restarted")
        self.check_health()

        if self.monitor_factory is None:
            from aioca import camonitor, FORMAT_TIME

            factory = camonitor
            format_time = FORMAT_TIME
        else:
            factory = self.monitor_factory
            format_time = 1  # aioca.FORMAT_TIME

        self.running = True
        self.worker = asyncio.create_task(self._consume())

        try:
            for pv in self.recorder.names:
                callback = lambda raw, name=pv: self._callback(
                    name, raw
                )

                subscription = factory(
                    pv,
                    callback,
                    format=format_time,
                    all_updates=True,
                    notify_disconnect=True,
                )

                self.subscriptions.append(subscription)

        except Exception:
            await self.stop()
            raise

    async def drain(self):
        await self.queue.join()
        self.check_health()

    async def stop(self):
        if not self.running and self.worker is None:
            self.check_health()
            return

        self.running = False

        for subscription in self.subscriptions:
            try:
                subscription.close()
            except Exception as error:
                self._fail(error)

        self.subscriptions.clear()

        if self.worker is not None:
            await self.queue.join()

            self.worker.cancel()

            with suppress(asyncio.CancelledError):
                await self.worker

            self.worker = None

        self.check_health()
