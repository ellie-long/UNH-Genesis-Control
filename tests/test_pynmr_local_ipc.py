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



def test_multiple_users_require_explicit_shared_group():
    uid = os.getuid()

    with pytest.raises(ValueError, match="shared_gid"):
        PyNMRLocalIPC(
            "/tmp/not_started.sock",
            lambda event, message: False,
            allowed_uids=(uid, uid + 1),
        )


def test_shared_group_socket_permissions():
    """Exercise group permissions with the current test GID."""

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        directory.chmod(0o2770)

        socket_path = directory / "pynmr.sock"
        uid = os.getuid()
        second_uid = 1000 if uid != 1000 else 1001

        server = PyNMRLocalIPC(
            socket_path,
            lambda event, message: True,
            allowed_uids=(uid, second_uid),
            shared_gid=os.getegid(),
        )

        try:
            server.start()

            info = socket_path.stat()

            assert socket_path.is_socket()
            assert info.st_gid == os.getegid()
            assert info.st_mode & 0o777 == 0o660

            result = request(
                socket_path,
                encode(envelope()),
            )

            assert result["ok"] is True

        finally:
            server.close()


def test_shared_socket_rejects_unlisted_peer_uid():
    """Simulated credentials test; no second-user login needed."""

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        directory.chmod(0o2770)

        socket_path = directory / "pynmr.sock"
        uid = os.getuid()
        second_uid = 1000 if uid != 1000 else 1001

        server = PyNMRLocalIPC(
            socket_path,
            lambda event, message: True,
            allowed_uids=(uid, second_uid),
            shared_gid=os.getegid(),
        )

        try:
            server.start()

            # Inject a synthetic, unauthorized peer identity
            # to test the application's allowlist decision.
            server._peer_credentials = (
                lambda connection: (12345, 999999, 999999)
            )

            result = request(
                socket_path,
                encode(envelope()),
            )

            assert result["ok"] is False
            assert result["reason"] == "peer not permitted"

        finally:
            server.close()


def test_shared_mode_rejects_private_only_directory():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)

        # A shared socket must not silently operate
        # within an incorrectly configured directory.
        directory.chmod(0o700)

        uid = os.getuid()

        server = PyNMRLocalIPC(
            directory / "pynmr.sock",
            lambda event, message: False,
            allowed_uids=(uid, uid + 1),
            shared_gid=os.getegid(),
        )

        with pytest.raises(PermissionError, match="2770"):
            server.start()
