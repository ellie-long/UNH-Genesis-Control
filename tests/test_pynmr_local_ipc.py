"""Offline Linux Unix-socket tests for PyNMR event IPC."""

import json
import os
from pathlib import Path
import socket
import tempfile

import pytest

from online_fitter.pynmr_local_ipc import (
    PyNMRLocalIPC,
    decode_event_envelope,
)


def envelope():
    return {
        "version": 1,
        "kind": "pynmr_finished_event",
        "event": {
            "start_stamp": 1700000100.0,
            "stop_stamp": 1700000101.0,
            "sweeps": 32,
            "freqs_mhz": [32.6, 32.7, 32.8],
        },
        "sweep": {
            "type": "sweep",
            "source": "pynmr",
            "stop_stamp": 1700000101.0,
            "sweeps": 32,
            "npts": 3,
            "freqs_mhz": [32.6, 32.7, 32.8],
            "raw": [1.0, 2.0, 3.0],
            "baseline_sub": [0.1, 0.2, 0.3],
        },
    }


def request(path, data):
    with socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    ) as client:
        client.settimeout(2.0)
        client.connect(str(path))
        client.sendall(data)

        with client.makefile("rb") as reader:
            return json.loads(reader.readline())


def encode(value):
    return (json.dumps(value) + "\n").encode("utf-8")


def test_valid_event_envelope():
    event, message = decode_event_envelope(
        envelope()
    )

    assert event.scan.num == 32
    assert event.start_stamp < event.stop_stamp
    assert message["source"] == "pynmr"


def test_inconsistent_packet_is_rejected():
    data = envelope()
    data["sweep"]["sweeps"] = 64

    with pytest.raises(ValueError, match="mismatch"):
        decode_event_envelope(data)


def test_unsupported_protocol_is_rejected():
    data = envelope()
    data["version"] = 2

    with pytest.raises(ValueError, match="protocol"):
        decode_event_envelope(data)


def test_private_ipc_accepts_matching_unix_uid():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "pynmr.sock"
        events = []

        def callback(event, message):
            events.append((event, message))
            return True

        server = PyNMRLocalIPC(
            path,
            callback,
            allowed_uid=os.getuid(),
        )

        assert not path.exists()
        assert not server.running

        try:
            server.start()
            assert path.is_socket()
            assert path.stat().st_mode & 0o777 == 0o600

            reply = request(
                path,
                encode(envelope()),
            )

            assert reply["ok"] is True
            assert len(events) == 1
            assert events[0][0].scan.num == 32
        finally:
            server.close()

        assert not path.exists()


def test_wrong_unix_uid_is_rejected():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "pynmr.sock"
        events = []

        server = PyNMRLocalIPC(
            path,
            lambda event, message: (
                events.append(message) or True
            ),
            allowed_uid=os.getuid() + 1,
        )

        try:
            server.start()

            reply = request(
                path,
                encode(envelope()),
            )

            assert reply["ok"] is False
            assert events == []
        finally:
            server.close()


def test_malformed_ipc_packet_fails_closed():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "pynmr.sock"

        server = PyNMRLocalIPC(
            path,
            lambda event, message: True,
            allowed_uid=os.getuid(),
        )

        try:
            server.start()

            reply = request(
                path, b'{"bad":true}\n'
            )

            assert reply["ok"] is False
        finally:
            server.close()


def test_refuses_nonprivate_parent_directory():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        root.chmod(0o755)

        server = PyNMRLocalIPC(
            root / "pynmr.sock",
            lambda event, message: True,
            allowed_uid=os.getuid(),
        )

        with pytest.raises(PermissionError):
            server.start()
