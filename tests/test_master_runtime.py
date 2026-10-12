"""Hardware-free master scheduler and queue-barrier tests."""

import asyncio
import csv
from datetime import datetime, timedelta, timezone

import pytest

from archive.master_ca import MasterCACollector
from archive.master_recorder import MasterRecorder
from archive.master_runtime import MasterRuntime


PV = "UNH:GEN:CRYO:FRIDGE:F1_TI"
T0 = datetime(
    2026, 10, 12, 14, 0, 0,
    tzinfo=timezone.utc,
)


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    async def sleep(self, delay):
        self.elapsed += delay
        await asyncio.sleep(0)

    def now(self):
        return T0 + timedelta(seconds=self.elapsed)


class FakeCollector:
    def __init__(self, recorder):
        self.recorder = recorder
        self.started = False
        self.stopped = False
        self.flushes = 0

    async def start(self):
        self.started = True
        self.recorder.record(PV, 1.2345, at=T0)

    async def flush(self):
        self.flushes += 1

    async def stop(self):
        self.stopped = True


def test_three_synchronized_snapshots(tmp_path):
    async def scenario():
        recorder = MasterRecorder(tmp_path, {PV: {}})
        collector = FakeCollector(recorder)
        clock = FakeClock()

        runtime = MasterRuntime(
            recorder,
            collector,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            now=clock.now,
        )

        await runtime.run(max_ticks=3)

        assert collector.started
        assert collector.stopped
        assert collector.flushes == 3
        assert recorder.closed

        health = runtime.health()

        assert health["snapshots"] == 3
        assert health["overruns"] == 0
        assert health["finished"] is True

        path = (
            tmp_path / "2026-10-12"
            / "genesis_master.csv"
        )

        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 3

        assert [r["timestamp_utc"] for r in rows] == [
            "2026-10-12T14:00:01.000Z",
            "2026-10-12T14:00:02.000Z",
            "2026-10-12T14:00:03.000Z",
        ]

        assert all(
            r[PV + "|value"] == "1.2345"
            for r in rows
        )

    asyncio.run(scenario())


def test_runtime_closes_on_collector_failure(tmp_path):
    class FailingCollector(FakeCollector):
        async def flush(self):
            raise RuntimeError("Synthetic collector failure")

    async def scenario():
        recorder = MasterRecorder(tmp_path, {PV: {}})
        collector = FailingCollector(recorder)
        clock = FakeClock()

        runtime = MasterRuntime(
            recorder,
            collector,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            now=clock.now,
        )

        with pytest.raises(RuntimeError, match="Synthetic"):
            await runtime.run(max_ticks=2)

        assert collector.stopped
        assert recorder.closed
        assert runtime.health()["finished"] is True

    asyncio.run(scenario())


def test_barrier_processes_prior_ca_updates(tmp_path):
    class Subscription:
        def __init__(self, callback):
            self.callback = callback
            self.closed = False

        def close(self):
            self.closed = True

    async def scenario():
        recorder = MasterRecorder(tmp_path, {PV: {}})
        subscriptions = {}

        def fake_monitor(name, callback, **kwargs):
            item = Subscription(callback)
            subscriptions[name] = item
            return item

        collector = MasterCACollector(
            recorder,
            monitor_factory=fake_monitor,
        )

        await collector.start()

        try:
            for value in range(100):
                subscriptions[PV].callback(float(value))

            await asyncio.wait_for(
                collector.flush(), timeout=2
            )

            assert collector.written_events == 100
            assert collector.dropped_events == 0
            assert recorder.latest[PV]["value"] == 99.0

        finally:
            await collector.stop()
            recorder.close()

        assert subscriptions[PV].closed

    asyncio.run(scenario())


def test_invalid_tick_count_rejected(tmp_path):
    async def scenario():
        recorder = MasterRecorder(tmp_path, {PV: {}})
        clock = FakeClock()

        runtime = MasterRuntime(
            recorder,
            FakeCollector(recorder),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            now=clock.now,
        )

        for value in (0, -1, True, 1.5):
            with pytest.raises(ValueError):
                await runtime.run(max_ticks=value)

        assert not runtime.started
        recorder.close()

    asyncio.run(scenario())
