"""Offline tests of the opt-in protected NMR coordinator."""

import json
import socket
from types import SimpleNamespace

import pytest

from online_fitter.protected_runtime import ProtectedNMRRuntime


class FakePV:
    connected = True

    def __init__(self, name):
        self.name = name
        self.reads = 0

    def get(self, **kwargs):
        assert kwargs["use_monitor"] is False
        self.reads += 1
        return 1 if self.name.endswith(":COMM") else 0

    def put(self, *args, **kwargs):
        raise AssertionError("EPICS write attempted")


def make_runtime():
    pvs = {}

    def factory(name, **kwargs):
        assert kwargs == {"auto_monitor": False}
        assert name.endswith((":COMM", ":MASK_RBV"))
        pv = FakePV(name)
        pvs[name] = pv
        return pv

    runtime = ProtectedNMRRuntime(
        SimpleNamespace(n_received=0),
        selected_source="labview",
        pv_factory=factory,
        labview_port=0,
        pynmr_port=0,
        poll_interval_s=0.1,
    )

    return runtime, pvs


def send(port, message):
    with socket.create_connection(
        ("127.0.0.1", port), timeout=3
    ) as sock:
        sock.settimeout(3)
        with sock.makefile("rwb") as stream:
            stream.write(
                (json.dumps(message) + "\n").encode()
            )
            stream.flush()
            return json.loads(stream.readline())


def test_protected_runtime_fails_closed_without_events():
    runtime, pvs = make_runtime()

    try:
        runtime.start()

        ports = runtime.transport.ports
        assert set(ports) == {"labview", "pynmr"}
        assert ports["labview"] != ports["pynmr"]

        snapshot = runtime.snapshot()
        assert snapshot["selected_source"] == "labview"
        assert snapshot["nmr_mask"] == 0
        assert snapshot["post_switch_ready"] is False

        # Correct route alone cannot authorize fitting.
        assert not send(
            ports["labview"],
            {"type": "sweep"},
        )["ok"]

        assert not send(
            ports["pynmr"],
            {"type": "sweep", "source": "pynmr"},
        )["ok"]

        runtime.select_source("pynmr")
        assert not runtime.snapshot()["post_switch_ready"]

        assert not send(
            ports["pynmr"],
            {"type": "sweep", "source": "pynmr"},
        )["ok"]

        assert all(pv.reads >= 2 for pv in pvs.values())

    finally:
        runtime.shutdown()

    assert runtime.transport.ports == {}


def test_unbound_event_ticket_cannot_fit_arbitrary_tcp_packet():
    """A fresh event alone must never authorize another spectrum."""
    import time

    runtime, _ = make_runtime()

    try:
        runtime.start()

        # Simulate a trusted event without associating its
        # acquisition identity with an incoming TCP spectrum.
        runtime.source_state.update_stream("labview", True)

        started = time.monotonic()
        time.sleep(0.002)
        completed = time.monotonic()

        assert runtime.source_state.completed_event(
            "labview", started, completed
        )

        # The event ticket exists, but has no packet binding.
        assert runtime.snapshot()["post_switch_ready"] is True

        reply = send(
            runtime.transport.ports["labview"],
            {"type": "sweep"},
        )

        assert reply["ok"] is False
        assert "post-switch" in reply["error"]

        # An unrelated TCP packet cannot consume the event.
        assert runtime.snapshot()["post_switch_ready"] is True

    finally:
        runtime.shutdown()


def test_invalid_software_source_rejected():
    runtime, _ = make_runtime()
    with pytest.raises(ValueError):
        runtime.select_source("invalid")


def test_second_start_rejected():
    runtime, _ = make_runtime()
    try:
        runtime.start()
        with pytest.raises(RuntimeError):
            runtime.start()
    finally:
        runtime.shutdown()


def test_shutdown_without_start():
    runtime, _ = make_runtime()
    runtime.shutdown()


def test_runtime_stays_read_only():
    runtime, pvs = make_runtime()
    try:
        runtime.start()
        runtime.select_source("pynmr")
    finally:
        runtime.shutdown()

    assert pvs
    assert all(pv.reads >= 2 for pv in pvs.values())


def test_pynmr_only_runtime_no_labview_listener_or_switching():
    pvs = {}
    def factory(name, **kwargs):
        pv = FakePV(name)
        pvs[name] = pv
        return pv
    runtime = ProtectedNMRRuntime(
        SimpleNamespace(n_received=0), selected_source="pynmr",
        pv_factory=factory, labview_port=0, pynmr_port=0,
        pynmr_only=True, poll_interval_s=0.1,
    )
    try:
        runtime.start()
        assert set(runtime.transport.ports) == {"pynmr"}
        assert runtime.snapshot()["selected_source"] == "pynmr"
        with pytest.raises(ValueError):
            runtime.select_source("labview")
        assert not send(
            runtime.transport.ports["pynmr"],
            {"type": "sweep", "source": "pynmr"},
        )["ok"]
    finally:
        runtime.shutdown()
    assert runtime.transport.ports == {}
