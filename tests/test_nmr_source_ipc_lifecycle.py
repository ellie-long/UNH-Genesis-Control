"""Offline NMR source IPC lifecycle tests; no hardware, sockets, or EPICS."""

import asyncio
import threading

import pytest

from online_fitter.nmr_source_ipc_lifecycle import NMRSourceIPCWorker
from online_fitter.protected_runtime import ProtectedNMRRuntime


class Endpoint:
    def __init__(self, fail=False):
        self.fail = fail
        self.started = False
        self.closed = False
        self.thread_id = None

    async def start(self):
        self.thread_id = threading.get_ident()
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("synthetic endpoint failure")
        self.started = True

    async def close(self):
        await asyncio.sleep(0)
        self.closed = True


def test_worker_starts_and_stops_endpoint_on_its_event_loop():
    endpoint = Endpoint()
    worker = NMRSourceIPCWorker(endpoint)
    worker.start()
    assert endpoint.started is True
    assert endpoint.thread_id != threading.get_ident()
    worker.close()
    assert endpoint.closed is True
    worker.close()  # idempotent


def test_worker_rejects_duplicate_start():
    worker = NMRSourceIPCWorker(Endpoint())
    worker.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            worker.start()
    finally:
        worker.close()


def test_endpoint_failure_fails_closed_and_cleans_up():
    endpoint = Endpoint(fail=True)
    worker = NMRSourceIPCWorker(endpoint)
    with pytest.raises(RuntimeError, match="startup failed"):
        worker.start()
    assert endpoint.closed is True
    assert worker._thread is None


def make_runtime(worker, event=None):
    runtime = object.__new__(ProtectedNMRRuntime)
    calls = []
    runtime.poller = type("Poller", (), {"poll_once": lambda self: calls.append("poll")})()
    runtime.transport = type("Transport", (), {
        "start": lambda self: calls.append("transport-start"),
        "close": lambda self: calls.append("transport-close"),
    })()
    runtime.event_ipc = event
    runtime.event_adapter = None
    runtime._source_control_worker = worker
    runtime._thread = None
    runtime._stop = threading.Event()
    runtime._poll_loop = lambda: None
    return runtime, calls


class WorkerSpy:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def start(self):
        self.calls.append("start")
        if self.fail:
            raise RuntimeError("IPC startup failed")

    def close(self):
        self.calls.append("close")


def test_runtime_starts_and_stops_control_worker():
    worker = WorkerSpy()
    runtime, calls = make_runtime(worker)
    runtime.start()
    assert calls[:2] == ["poll", "transport-start"]
    assert worker.calls == ["start"]
    runtime.shutdown()
    assert worker.calls == ["start", "close"]
    assert "transport-close" in calls


def test_runtime_rolls_back_failed_control_start():
    worker = WorkerSpy(fail=True)
    runtime, calls = make_runtime(worker)
    with pytest.raises(RuntimeError, match="IPC startup failed"):
        runtime.start()
    assert worker.calls == ["start", "close"]
    assert calls[-1] == "transport-close"
    assert runtime._thread is None


def test_runtime_without_control_worker_stays_legacy_compatible():
    runtime, calls = make_runtime(None)
    runtime.start()
    runtime.shutdown()
    assert calls == ["poll", "transport-start", "transport-close"]
