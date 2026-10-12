"""Offline producer-to-Genesis IPC regressions."""

import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from online_fitter.pynmr_event_publisher import (
    build_event_envelope,
    publish_event,
)
from online_fitter.pynmr_local_ipc import (
    PyNMRLocalIPC,
)


def example():
    event = SimpleNamespace(
        start_stamp=1700000100.0,
        stop_stamp=1700000101.0,
        scan=SimpleNamespace(
            num=32,
            freq_list=[32.6, 32.7, 32.8],
        ),
    )

    sweep = {
        "type": "sweep",
        "source": "pynmr",
        "centfreq_mhz": 32.7,
        "span_mhz": 0.3,
        "npts": 3,
        "raw": [1.0, 2.0, 3.0],
        "baseline_sub": [0.1, 0.2, 0.3],
        "freqs_mhz": [32.6, 32.7, 32.8],
        "sweeps": 32,
        "stop_stamp": 1700000101.0,
    }

    return event, sweep


def test_envelope_preserves_exact_spectrum():
    event, sweep = example()
    envelope = build_event_envelope(event, sweep)

    assert envelope["sweep"] == sweep
    assert envelope["event"]["start_stamp"] == (
        event.start_stamp
    )
    assert envelope["event"]["stop_stamp"] == (
        event.stop_stamp
    )

    # Future mutations cannot alter the queued attestation.
    sweep["raw"][0] = 999.0
    assert envelope["sweep"]["raw"][0] == 1.0


def test_mismatched_spectrum_rejected():
    event, sweep = example()
    sweep["stop_stamp"] += 1

    with pytest.raises(ValueError, match="mismatch"):
        build_event_envelope(event, sweep)


def test_invalid_acquisition_rejected():
    event, sweep = example()
    event.start_stamp = float("nan")

    with pytest.raises(ValueError):
        build_event_envelope(event, sweep)


def test_completed_event_reaches_private_ipc():
    event, sweep = example()
    envelope = build_event_envelope(event, sweep)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pynmr.sock"
        received = []

        def accept(record, message):
            received.append((record, message))
            return True

        server = PyNMRLocalIPC(
            path,
            accept,
            allowed_uid=os.getuid(),
        )

        try:
            server.start()

            assert publish_event(path, envelope)
            assert len(received) == 1
            assert received[0][1] == sweep
            assert received[0][0].scan.num == 32

        finally:
            server.close()


def test_rejected_event_does_not_receive_ack():
    event, sweep = example()
    envelope = build_event_envelope(event, sweep)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pynmr.sock"

        server = PyNMRLocalIPC(
            path,
            lambda record, message: False,
            allowed_uid=os.getuid(),
        )

        try:
            server.start()
            assert not publish_event(path, envelope)
        finally:
            server.close()


def test_unavailable_listener_fails_closed():
    event, sweep = example()
    envelope = build_event_envelope(event, sweep)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing.sock"

        assert not publish_event(
            path, envelope, timeout_s=0.1
        )


def test_wrong_peer_uid_fails_closed():
    event, sweep = example()
    envelope = build_event_envelope(event, sweep)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pynmr.sock"
        accepted = []

        server = PyNMRLocalIPC(
            path,
            lambda record, message: (
                accepted.append(message) or True
            ),
            allowed_uid=os.getuid() + 1,
        )

        try:
            server.start()
            assert not publish_event(path, envelope)
            assert accepted == []
        finally:
            server.close()
