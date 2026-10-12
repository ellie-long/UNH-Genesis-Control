"""Hardware-free regression tests for Genesis CA monitoring."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from archive.master_ca import (
    MasterCACollector,
    ca_scalar,
    source_timestamp,
)
from archive.master_recorder import MasterRecorder


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
        subscription = FakeSubscription(callback)
        self.subscriptions[pv] = subscription
        return subscription

    def publish(self, pv, value):
        self.subscriptions[pv].callback(value)


def test_scalar_and_source_timestamp():
    sample = SimpleNamespace(
        value=12.3456789,
        timestamp=1791813600.125,
    )

    assert ca_scalar(sample) == 12.3456789

    stamp = source_timestamp(sample)
    assert stamp.tzinfo == timezone.utc

    assert source_timestamp(
        SimpleNamespace(timestamp=None)
    ) is None

    with pytest.raises(TypeError):
        ca_scalar([1, 2, 3])


def test_readonly_monitor_events_and_disconnect(tmp_path):
    async def scenario():
        pv = "UNH:GEN:CRYO:FRIDGE:F1_TI"
        recorder = MasterRecorder(tmp_path, {pv: {}})
        fake = FakeCA()

        collector = MasterCACollector(
            recorder,
            monitor_factory=fake.monitor,
        )

        await collector.start()

        assert fake.options[pv]["format"] == 1
        assert fake.options[pv]["all_updates"] is True
        assert fake.options[pv]["notify_disconnect"] is True

        fake.publish(
            pv,
            SimpleNamespace(
                value=1.23456789,
                timestamp=1791813600.125,
            ),
        )

        fake.publish(pv, None)

        await collector.drain()
        await collector.stop()

        assert fake.subscriptions[pv].closed
        assert collector.accepted_events == 2
        assert collector.written_events == 2
        assert collector.dropped_events == 0

        recorder.close()

        files = list(
            tmp_path.glob("*/genesis_master.jsonl")
        )
        assert len(files) == 1

        events = [
            json.loads(line)
            for line in files[0].read_text().splitlines()
        ]

        assert len(events) == 2
        assert events[0]["value"] == 1.23456789
        assert events[0]["connected"] is True
        assert events[0]["source_utc"] is not None

        assert events[1]["connected"] is False
        assert events[1]["value"] is None

    asyncio.run(scenario())


def test_queue_overflow_is_explicit_fault(tmp_path):
    async def scenario():
        pv = "UNH:GEN:TEST:PV"
        recorder = MasterRecorder(tmp_path, {pv: {}})
        fake = FakeCA()

        collector = MasterCACollector(
            recorder,
            monitor_factory=fake.monitor,
            max_queue=1,
        )

        await collector.start()

        # Callbacks run synchronously before the consumer is scheduled.
        fake.publish(pv, 1)
        fake.publish(pv, 2)

        assert collector.dropped_events >= 1

        with pytest.raises(RuntimeError, match="incomplete"):
            await collector.drain()

        with pytest.raises(RuntimeError, match="incomplete"):
            await collector.stop()

        recorder.close()

    asyncio.run(scenario())


def test_multiple_pvs_share_one_consumer(tmp_path):
    async def scenario():
        names = [
            "UNH:GEN:MW:SRC:MW_FI",
            "UNH:GEN:SSRF:FIT:P_FIT",
        ]

        recorder = MasterRecorder(
            tmp_path,
            {name: {} for name in names},
        )

        fake = FakeCA()
        collector = MasterCACollector(
            recorder,
            monitor_factory=fake.monitor,
        )

        await collector.start()

        for i in range(10):
            for name in names:
                fake.publish(name, float(i))

        await collector.drain()
        await collector.stop()

        assert collector.written_events == 20
        assert collector.dropped_events == 0

        recorder.close()

    asyncio.run(scenario())
