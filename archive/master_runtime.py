"""Genesis synchronized master snapshot runtime.

Uses a supplied read-only collector and a single master recorder.
Does not issue EPICS writes, poll devices, or start hardware IOCs.
"""

import asyncio
from datetime import datetime, timezone
import math
import time


class MasterRuntime:
    """Run a common snapshot clock across all registered PVs."""

    def __init__(
        self,
        recorder,
        collector,
        *,
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
        now=None,
        flush_timeout_s=10.0,
    ):
        if not math.isfinite(flush_timeout_s) or flush_timeout_s <= 0:
            raise ValueError("flush_timeout_s must be positive")

        self.recorder = recorder
        self.collector = collector
        self.monotonic = monotonic
        self.sleep = sleep
        self.now = now or (
            lambda: datetime.now(timezone.utc)
        )
        self.flush_timeout_s = flush_timeout_s

        self.snapshots = 0
        self.overruns = 0
        self.max_lateness_s = 0.0
        self.started = False
        self.finished = False

    def health(self):
        """Return diagnostic counters without touching instruments."""
        return {
            "snapshot_interval_s": self.recorder.interval_s,
            "snapshots": self.snapshots,
            "overruns": self.overruns,
            "max_lateness_s": round(
                self.max_lateness_s, 6
            ),
            "accepted_events": getattr(
                self.collector, "accepted_events", 0
            ),
            "written_events": getattr(
                self.collector, "written_events", 0
            ),
            "dropped_events": getattr(
                self.collector, "dropped_events", 0
            ),
            "finished": self.finished,
        }

    async def run(self, *, max_ticks=None):
        """Run until cancelled or max_ticks snapshots are written.

        No skipped deadline is represented as a fabricated measurement.
        Snapshot timestamps reflect actual recording time.
        """
        if self.started:
            raise RuntimeError(
                "Master runtime cannot be started twice"
            )

        if max_ticks is not None:
            if (
                isinstance(max_ticks, bool)
                or not isinstance(max_ticks, int)
                or max_ticks < 1
            ):
                raise ValueError("max_ticks must be a positive integer")

        self.started = True
        interval = self.recorder.interval_s

        try:
            await self.collector.start()

            deadline = self.monotonic() + interval

            while max_ticks is None or self.snapshots < max_ticks:
                remaining = deadline - self.monotonic()

                if remaining > 0:
                    await self.sleep(remaining)

                # Process events queued before this snapshot boundary.
                await asyncio.wait_for(
                    self.collector.flush(),
                    timeout=self.flush_timeout_s,
                )

                self.recorder.snapshot(at=self.now())
                self.snapshots += 1

                completed = self.monotonic()
                lateness = max(0.0, completed - deadline)

                self.max_lateness_s = max(
                    self.max_lateness_s, lateness
                )

                if lateness >= interval:
                    self.overruns += 1

                # Never fabricate catch-up samples or produce snapshots
                # closer together than the configured minimum interval.
                deadline = max(
                    deadline + interval,
                    completed + interval,
                )

        finally:
            try:
                await self.collector.stop()
            finally:
                self.recorder.close()
                self.finished = True
