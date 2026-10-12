"""Offline TCP lifecycle tests using fake EPICS readbacks."""

import socket
import time
from types import SimpleNamespace

from online_fitter.protected_runtime import (
    ProtectedNMRRuntime,
)


class FakePV:
    connected = True

    def __init__(self, name):
        self.name = name

    def get(self, **kwargs):
        if self.name.endswith(":COMM"):
            return 1
        if self.name.endswith(":MASK_RBV"):
            return 3
        raise AssertionError(self.name)

    def put(self, *args, **kwargs):
        raise AssertionError("EPICS writes prohibited")


def runtime():
    return ProtectedNMRRuntime(
        SimpleNamespace(n_received=0),
        selected_source="pynmr",
        pv_factory=lambda name, **kwargs: FakePV(name),
        labview_port=0,
        pynmr_port=0,
        poll_interval_s=1.0,
    )


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)

    return bool(predicate())


def stream_connected(instance):
    return instance.source_state.snapshot()[
        "stream_connected"
    ]


def connect(instance):
    port = instance.transport.ports["pynmr"]
    return socket.create_connection(
        ("127.0.0.1", port), timeout=2.0
    )


def test_tcp_connection_tracks_connect_and_disconnect():
    instance = runtime()

    try:
        instance.start()

        assert not stream_connected(instance)

        connection = connect(instance)

        try:
            assert wait_for(
                lambda: stream_connected(instance)
            )

            # TCP connection alone cannot attest any event.
            assert instance.source_state.snapshot()[
                "post_switch_ready"
            ] is False

        finally:
            connection.close()

        assert wait_for(
            lambda: not stream_connected(instance)
        )

    finally:
        instance.shutdown()


def test_one_disconnect_does_not_clear_another_connection():
    instance = runtime()

    try:
        instance.start()

        first = connect(instance)
        second = connect(instance)

        try:
            assert wait_for(
                lambda: stream_connected(instance)
            )

            first.close()

            # Allow the first handler to process its EOF.
            time.sleep(0.15)

            assert stream_connected(instance)

        finally:
            first.close()
            second.close()

        assert wait_for(
            lambda: not stream_connected(instance)
        )

    finally:
        instance.shutdown()


def test_shutdown_revokes_connected_stream():
    instance = runtime()
    connection = None

    try:
        instance.start()
        connection = connect(instance)

        assert wait_for(
            lambda: stream_connected(instance)
        )

        instance.shutdown()

        assert not stream_connected(instance)

    finally:
        if connection is not None:
            connection.close()

        instance.shutdown()


def test_unconnected_stream_cannot_be_attested():
    instance = runtime()

    try:
        instance.start()

        assert not stream_connected(instance)
        assert instance.event_adapter is None

        observation = instance.packet_binding.observe_packet(
            {
                "type": "sweep",
                "source": "pynmr",
                "npts": 3,
                "raw": [1.0, 2.0, 3.0],
                "baseline_sub": [0.0, 0.0, 0.0],
            },
            "pynmr",
        )

        assert observation["post_switch_ready"] is False

    finally:
        instance.shutdown()
