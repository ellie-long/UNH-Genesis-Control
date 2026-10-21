"""Genesis opt-in PyNMR IPC runtime integration tests.

All PVs are fake and all sockets are local test sockets.
No IOC or physical hardware is accessed.
"""

import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace

import pytest

from online_fitter.protected_runtime import (
    ProtectedNMRRuntime,
)
from online_fitter.pynmr_event_publisher import (
    build_event_envelope,
    publish_event,
)


class FakePV:
    connected = True

    def __init__(self, name):
        self.name = name

    def get(self, **kwargs):
        if self.name.endswith(":COMM"):
            return 1
        if self.name.endswith(":MASK_RBV"):
            return 0
        raise AssertionError(self.name)

    def put(self, *args, **kwargs):
        raise AssertionError("EPICS write attempted")


def make_runtime(path=None):
    options = {}

    if path is not None:
        options.update(
            pynmr_ipc_path=path,
            pynmr_ipc_allowed_uids=(os.getuid(),),
        )

    return ProtectedNMRRuntime(
        SimpleNamespace(n_received=0),
        selected_source="pynmr",
        pv_factory=lambda name, **kwargs: FakePV(name),
        labview_port=0,
        pynmr_port=0,
        poll_interval_s=1.0,
        **options,
    )


def example_event():
    start = time.time()
    time.sleep(0.025)
    stop = time.time()

    event = SimpleNamespace(
        start_stamp=start,
        stop_stamp=stop,
        scan=SimpleNamespace(
            num=32,
            freq_list=[32.6, 32.7, 32.8],
        ),
    )

    message = {
        "type": "sweep",
        "source": "pynmr",
        "centfreq_mhz": 32.7,
        "span_mhz": 0.3,
        "npts": 3,
        "raw": [1.0, 2.0, 3.0],
        "baseline_sub": [0.1, 0.2, 0.3],
        "freqs_mhz": [32.6, 32.7, 32.8],
        "sweeps": 32,
        "stop_stamp": stop,
    }

    return event, message


def test_default_runtime_does_not_create_ipc():
    runtime = make_runtime()

    assert runtime.event_ipc is None
    assert runtime.event_adapter is None
    runtime.shutdown()


def test_ipc_credentials_require_explicit_path():
    with pytest.raises(ValueError, match="without IPC path"):
        ProtectedNMRRuntime(
            SimpleNamespace(n_received=0),
            selected_source="pynmr",
            pv_factory=lambda name, **kw: FakePV(name),
            pynmr_ipc_allowed_uids=(os.getuid(),),
        )


def test_unarmed_ipc_fails_closed():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "event.sock"
        runtime = make_runtime(path)

        try:
            runtime.start()
            assert path.is_socket()
            assert runtime.event_adapter is not None

            event, message = example_event()

            assert not publish_event(
                path,
                build_event_envelope(event, message),
            )

            assert (
                runtime.source_state.snapshot()[
                    "post_switch_ready"
                ] is False
            )

        finally:
            runtime.shutdown()

        assert not path.exists()


def test_armed_simulated_event_is_single_use():
    """Arm manually in the test, not from runtime startup."""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "event.sock"
        runtime = make_runtime(path)

        try:
            runtime.start()

            # Simulates a future independently verified
            # producer-session handshake. No real handshake
            # is implemented or claimed here.
            runtime.source_state.update_stream(
                "pynmr", True
            )
            assert runtime.event_adapter.arm()

            # The event must START after the clock arm,
            # including its conservative timestamp guard.
            time.sleep(0.4)
            event, message = example_event()

            # Allow conservative completion uncertainty
            # before attempting finished-event admission.
            time.sleep(0.35)

            assert publish_event(
                path,
                build_event_envelope(event, message),
            )

            first = runtime.packet_binding.observe_packet(
                message, "pynmr"
            )
            second = runtime.packet_binding.observe_packet(
                message, "pynmr"
            )

            assert first["post_switch_ready"] is True
            assert second["post_switch_ready"] is False

        finally:
            runtime.shutdown()

        assert not path.exists()


def test_failed_bind_preserves_foreign_socket():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "event.sock"

        foreign = socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM
        )

        try:
            foreign.bind(str(path))
            runtime = make_runtime(path)

            with pytest.raises(FileExistsError):
                runtime.start()

            assert path.is_socket()
            assert runtime.transport.ports == {}

            runtime.shutdown()
            assert path.is_socket()

        finally:
            foreign.close()
            if path.is_socket():
                path.unlink()
