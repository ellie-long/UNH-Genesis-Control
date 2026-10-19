"""Offline Genesis NMR end-to-end software acceptance.

Exercises actual local IPC, protected TCP server,
packet binding, source gate, and fitter callback.

The fitter callback is a harmless test double.
No real PyNMR, EPICS IOC, NMR hardware, or RF is used.
"""

import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace

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
            return 3
        raise AssertionError(self.name)

    def put(self, *args, **kwargs):
        raise AssertionError(
            "Hardware or EPICS write attempted"
        )


def make_runtime(path, fitted):
    state = SimpleNamespace(n_received=0)

    runtime = ProtectedNMRRuntime(
        state,
        selected_source="pynmr",
        pv_factory=lambda name, **kw: FakePV(name),
        labview_port=0,
        pynmr_port=0,
        poll_interval_s=0.5,
        pynmr_ipc_path=path,
        pynmr_ipc_allowed_uids=(os.getuid(),),
    )

    def fake_fit(fitter_state, message):
        fitted.append(copy.deepcopy(message))
        return {
            "ok": True,
            "fit_mode": "offline-simulated",
        }

    # The real SourceAwareProcessor and source gate remain
    # in use. Only the numerical fitting function is mocked.
    assert hasattr(runtime._inner_processor, "fit")
    runtime._inner_processor.fit = fake_fit

    return runtime


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)

    return bool(predicate())


def tcp_connection(runtime):
    port = runtime.transport.ports["pynmr"]

    connection = socket.create_connection(
        ("127.0.0.1", port), timeout=3.0
    )

    connection.settimeout(3.0)
    return connection


def tcp_sweep(stream, message):
    frame = (
        json.dumps(
            message,
            allow_nan=False,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")

    stream.write(frame)
    stream.flush()

    response = stream.readline()
    assert response, "No protected TCP response"

    return json.loads(response)


def fresh_finished_event():
    start = time.time()
    time.sleep(0.03)
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


def attest_simulated_event(runtime, ipc_path):
    # Manual test-only arming simulates an independently
    # verified acquisition session. Runtime startup itself
    # must never arm the adapter.
    assert runtime.event_adapter.arm()

    # Allow the conservative UTC/monotonic arm boundary.
    time.sleep(0.40)

    event, message = fresh_finished_event()

    # Allow the conservative event completion boundary.
    time.sleep(0.36)

    envelope = build_event_envelope(event, message)

    assert publish_event(
        ipc_path,
        envelope,
        timeout_s=2.0,
    )

    return message


def test_complete_ipc_tcp_source_gate_fit_path():
    with tempfile.TemporaryDirectory() as directory:
        ipc_path = Path(directory) / "pynmr.sock"
        fitted = []
        runtime = make_runtime(ipc_path, fitted)

        try:
            runtime.start()
            assert ipc_path.is_socket()

            with tcp_connection(runtime) as connection:
                with connection.makefile("rwb") as stream:
                    assert wait_for(
                        lambda: runtime.snapshot()[
                            "stream_connected"
                        ]
                    )

                    # A connected client cannot fit by itself.
                    _, unverified = fresh_finished_event()

                    rejected = tcp_sweep(
                        stream, unverified
                    )
                    assert rejected["ok"] is False
                    assert fitted == []

                    message = attest_simulated_event(
                        runtime, ipc_path
                    )

                    # Wrong spectrum must not consume the
                    # authorized event's packet binding.
                    altered = copy.deepcopy(message)
                    altered["raw"][0] += 10.0

                    rejected = tcp_sweep(
                        stream, altered
                    )
                    assert rejected["ok"] is False
                    assert fitted == []

                    # Matching event + packet is accepted.
                    accepted = tcp_sweep(
                        stream, message
                    )

                    assert accepted["ok"] is True
                    assert len(fitted) == 1
                    assert fitted[0] == message

                    # The identical packet cannot fit twice.
                    replay = tcp_sweep(
                        stream, message
                    )

                    assert replay["ok"] is False
                    assert len(fitted) == 1

            assert wait_for(
                lambda: not runtime.snapshot()[
                    "stream_connected"
                ]
            )

        finally:
            runtime.shutdown()

        assert not ipc_path.exists()
        assert len(fitted) == 1


def test_tcp_disconnect_revokes_unspent_event():
    with tempfile.TemporaryDirectory() as directory:
        ipc_path = Path(directory) / "pynmr.sock"
        fitted = []
        runtime = make_runtime(ipc_path, fitted)

        try:
            runtime.start()

            # First TCP session receives an authorization
            # but disconnects before sending its spectrum.
            with tcp_connection(runtime) as connection:
                with connection.makefile("rwb") as stream:
                    assert wait_for(
                        lambda: runtime.snapshot()[
                            "stream_connected"
                        ]
                    )

                    message = attest_simulated_event(
                        runtime, ipc_path
                    )

                    assert runtime.snapshot()[
                        "post_switch_ready"
                    ] is True

            assert wait_for(
                lambda: not runtime.snapshot()[
                    "stream_connected"
                ]
            )

            # A new connection must not inherit the old
            # connection's pending event authorization.
            with tcp_connection(runtime) as connection:
                with connection.makefile("rwb") as stream:
                    assert wait_for(
                        lambda: runtime.snapshot()[
                            "stream_connected"
                        ]
                    )

                    rejected = tcp_sweep(
                        stream, message
                    )

                    assert rejected["ok"] is False
                    assert fitted == []

        finally:
            runtime.shutdown()

        assert not ipc_path.exists()
        assert fitted == []
