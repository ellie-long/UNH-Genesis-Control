"""343-PV (337 + target-cup T1/T3) end-to-end master pipeline test using fake CA callbacks.

No physical IOC, network connection, EPICS write, or device polling.
"""

import asyncio
import csv
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from archive.master_ca import MasterCACollector
from archive.master_recorder import MasterRecorder, iso_utc
from archive.master_registry import build_registry
from archive.master_runtime import MasterRuntime


class FakeSubscription:
    def __init__(self, callback):
        self.callback = callback
        self.closed = False

    def close(self):
        self.closed = True


class FakeCA:
    def __init__(self):
        self.subscriptions = {}
        self.options = {}

    def monitor(self, pv, callback, **kwargs):
        self.options[pv] = kwargs
        item = FakeSubscription(callback)
        self.subscriptions[pv] = item
        return item


class FakeClock:
    def __init__(self, start, ca, pvs):
        self.start = start
        self.elapsed = 0.0
        self.ca = ca
        self.pvs = pvs

    def monotonic(self):
        return self.elapsed

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    async def sleep(self, delay):
        self.elapsed += delay
        stamp = self.now()

        # Simulate a published event from every registered PV.
        for index, pv in enumerate(self.pvs):
            self.ca.subscriptions[pv].callback(
                SimpleNamespace(
                    value=float(index) + self.elapsed / 100.0,
                    timestamp=stamp.timestamp(),
                )
            )

        await asyncio.sleep(0)


def test_complete_337_pv_master_pipeline(tmp_path):
    async def scenario():
        registry, report = build_registry()
        assert report["total"] == 343

        pvs = sorted(registry)
        started = datetime.now(timezone.utc)

        recorder = MasterRecorder(
            tmp_path,
            registry,
            interval_s=1.0,
        )

        fake_ca = FakeCA()

        collector = MasterCACollector(
            recorder,
            monitor_factory=fake_ca.monitor,
            max_queue=5000,
        )

        clock = FakeClock(started, fake_ca, pvs)

        runtime = MasterRuntime(
            recorder,
            collector,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            now=clock.now,
        )

        await runtime.run(max_ticks=3)

        health = runtime.health()

        assert health["snapshots"] == 3
        assert health["accepted_events"] == 1029        # 3 events x 343 PVs
        assert health["written_events"] == 1029        # 3 events x 343 PVs
        assert health["dropped_events"] == 0
        assert health["finished"] is True
        assert health["overruns"] == 0

        assert len(fake_ca.subscriptions) == 343
        assert all(
            subscription.closed
            for subscription in fake_ca.subscriptions.values()
        )

        assert all(
            options["all_updates"]
            and options["notify_disconnect"]
            and options["format"] == 1
            for options in fake_ca.options.values()
        )

        directory = (
            tmp_path / started.date().isoformat()
        )

        with (
            directory / "genesis_master.csv"
        ).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)

        assert len(fields) == 1717        # 5 columns x 343 PVs + 2 timestamps
        assert len(rows) == 3

        for tick, row in enumerate(rows, 1):
            assert row["timestamp_utc"] == iso_utc(
                started + timedelta(seconds=tick)
            )

            assert row["timestamp_local"]

            for subsystem in ("Cryo", "ssRF", "Microwave"):
                pv = next(
                    name for name in pvs
                    if registry[name]["subsystem"] == subsystem
                )
                assert row[pv + "|quality"] == "valid"
                assert row[pv + "|source_utc"]

        with (
            directory / "genesis_master.jsonl"
        ).open(encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle]

        assert len(events) == 1029        # 3 events x 343 PVs
        assert all(event["connected"] for event in events)
        assert all(event["source_utc"] for event in events)

        assert recorder.closed

    asyncio.run(scenario())
