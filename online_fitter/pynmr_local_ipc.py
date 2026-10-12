"""Opt-in, local PyNMR completed-event IPC.

Linux AF_UNIX and SO_PEERCRED verify the connecting Unix UID.
They do NOT independently authenticate the PyNMR process or
prove that the reported event was physically acquired.

The producer must supply an event record generated directly
from EVENT_FINISHED, before its spectrum enters the TCP queue.

No listener starts on import. Runtime integration is separate.
"""

import json
import math
import os
from pathlib import Path
import socket
import stat
import struct
import threading
from types import SimpleNamespace


PROTOCOL_VERSION = 1
KIND = "pynmr_finished_event"
MAX_FRAME_BYTES = 2_000_000


def _finite_number(value):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
    )


def decode_event_envelope(envelope):
    """Validate an event envelope, not its producer's identity."""

    if not isinstance(envelope, dict):
        raise ValueError("Expected JSON object")

    if (
        type(envelope.get("version")) is not int
        or envelope["version"] != PROTOCOL_VERSION
        or envelope.get("kind") != KIND
    ):
        raise ValueError("Unsupported event protocol")

    info = envelope.get("event")
    message = envelope.get("sweep")

    if not isinstance(info, dict):
        raise ValueError("Missing event metadata")

    if not isinstance(message, dict):
        raise ValueError("Missing sweep")

    start = info.get("start_stamp")
    stop = info.get("stop_stamp")
    sweeps = info.get("sweeps")
    freqs = info.get("freqs_mhz")

    if (
        not _finite_number(start)
        or not _finite_number(stop)
        or start >= stop
        or type(sweeps) is not int
        or sweeps <= 0
        or not isinstance(freqs, list)
        or not 2 <= len(freqs) <= 8192
        or not all(_finite_number(f) for f in freqs)
    ):
        raise ValueError("Invalid acquisition metadata")

    npts = len(freqs)

    if (
        message.get("type") != "sweep"
        or message.get("source") != "pynmr"
        or message.get("stop_stamp") != stop
        or type(message.get("sweeps")) is not int
        or message["sweeps"] != sweeps
        or type(message.get("npts")) is not int
        or message["npts"] != npts
        or message.get("freqs_mhz") != freqs
    ):
        raise ValueError("Event and sweep mismatch")

    for key in ("raw", "baseline_sub"):
        values = message.get(key)

        if (
            not isinstance(values, list)
            or len(values) != npts
            or not all(_finite_number(v) for v in values)
        ):
            raise ValueError(
                "Invalid spectrum array: " + key
            )

    event = SimpleNamespace(
        start_stamp=start,
        stop_stamp=stop,
        scan=SimpleNamespace(
            num=sweeps,
            freq_list=freqs,
        ),
    )

    return event, message


class PyNMRLocalIPC:
    """A manually started, UID-checked Unix socket receiver."""

    def __init__(
        self,
        path,
        on_event,
        *,
        allowed_uid=None,
        allowed_uids=None,
        shared_gid=None,
        max_frame_bytes=MAX_FRAME_BYTES,
    ):
        if not callable(on_event):
            raise TypeError("on_event must be callable")

        if (allowed_uid is None) == (allowed_uids is None):
            raise ValueError(
                "Specify exactly one of allowed_uid or allowed_uids"
            )

        if allowed_uid is not None:
            allowed_uids = (allowed_uid,)

        if (
            not isinstance(
                allowed_uids, (tuple, list, set, frozenset)
            )
            or not allowed_uids
            or any(
                type(uid) is not int or uid < 0
                for uid in allowed_uids
            )
        ):
            raise ValueError("Invalid Unix UID allowlist")

        allowed = frozenset(allowed_uids)

        if shared_gid is None:
            if len(allowed) != 1:
                raise ValueError(
                    "Multiple UIDs require a dedicated shared_gid"
                )
        elif type(shared_gid) is not int or shared_gid < 0:
            raise ValueError("Invalid shared Unix GID")

        if (
            type(max_frame_bytes) is not int
            or max_frame_bytes < 1024
        ):
            raise ValueError("Invalid IPC frame limit")

        if not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("Linux SO_PEERCRED is required")

        self.path = Path(path)
        self.on_event = on_event
        self.allowed_uid = allowed_uid
        self.allowed_uids = allowed
        self.shared_gid = shared_gid
        self.max_frame_bytes = max_frame_bytes

        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._bound_socket_identity = None

    @property
    def running(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
        )

    def start(self):
        """Explicitly create the private socket listener."""

        if self._listener is not None:
            raise RuntimeError("IPC listener already started")

        parent = self.path.parent
        info = parent.stat()

        if (
            not stat.S_ISDIR(info.st_mode)
            or parent.is_symlink()
            or info.st_uid != os.getuid()
        ):
            raise PermissionError(
                "IPC parent must be an owned, real directory"
            )

        mode = stat.S_IMODE(info.st_mode)

        if self.shared_gid is None:
            if mode & 0o077:
                raise PermissionError(
                    "Single-user IPC directory must be private"
                )
        else:
            # 2770: owner/group rwx, setgid inheritance,
            # no access for other Unix accounts.
            if (
                info.st_gid != self.shared_gid
                or mode != 0o2770
            ):
                raise PermissionError(
                    "Shared IPC parent requires mode 2770 "
                    "and the configured group ownership"
                )

            if self.shared_gid not in (
                os.getegid(), *os.getgroups()
            ):
                raise PermissionError(
                    "Genesis process is not a member "
                    "of the configured IPC group"
                )

        if os.path.lexists(self.path):
            raise FileExistsError(
                "Refusing to replace existing IPC socket"
            )

        listener = socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM
        )

        try:
            listener.bind(str(self.path))
            bound = self.path.lstat()
            self._bound_socket_identity = (
                bound.st_dev, bound.st_ino
            )

            if self.shared_gid is not None:
                if self.path.stat().st_gid != self.shared_gid:
                    raise PermissionError(
                        "IPC socket did not inherit shared group"
                    )
                os.chmod(self.path, 0o660)
            else:
                os.chmod(self.path, 0o600)

            listener.listen(4)
            listener.settimeout(0.1)
        except BaseException:
            listener.close()
            self._remove_owned_socket()
            raise

        self._stop.clear()
        self._listener = listener

        self._thread = threading.Thread(
            target=self._serve,
            daemon=True,
            name="genesis-pynmr-local-ipc",
        )
        self._thread.start()

    @staticmethod
    def _peer_credentials(connection):
        data = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        return struct.unpack("3i", data)

    @staticmethod
    def _reply(connection, accepted, reason):
        response = {
            "ok": bool(accepted),
            "reason": reason,
        }

        try:
            connection.sendall(
                (
                    json.dumps(response) + "\n"
                ).encode("utf-8")
            )
        except OSError:
            pass

    def _handle(self, connection):
        connection.settimeout(2.0)

        try:
            pid, uid, gid = self._peer_credentials(
                connection
            )

            if pid <= 0 or uid not in self.allowed_uids:
                self._reply(
                    connection, False, "peer not permitted"
                )
                return

            with connection.makefile("rb") as reader:
                frame = reader.readline(
                    self.max_frame_bytes + 1
                )

            if (
                not frame
                or len(frame) > self.max_frame_bytes
                or not frame.endswith(b"\n")
            ):
                self._reply(
                    connection, False, "invalid frame"
                )
                return

            def reject_constant(value):
                raise ValueError(
                    "Nonfinite JSON constant: " + value
                )

            envelope = json.loads(
                frame.decode("utf-8"),
                parse_constant=reject_constant,
            )

            event, message = decode_event_envelope(
                envelope
            )

            # Only a trusted application-level integration
            # may install an event attestation callback.
            accepted = self.on_event(event, message) is True

            self._reply(
                connection,
                accepted,
                "accepted" if accepted else "event rejected",
            )

        except (
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            TimeoutError,
        ):
            self._reply(
                connection, False, "invalid event envelope"
            )
        except Exception:
            # A producer callback failure must fail closed.
            self._reply(
                connection, False, "event handler failed"
            )

    def _serve(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with connection:
                self._handle(connection)

    def _remove_owned_socket(self):
        identity = self._bound_socket_identity
        self._bound_socket_identity = None

        if identity is None:
            return

        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return

        if (
            stat.S_ISSOCK(info.st_mode)
            and (info.st_dev, info.st_ino) == identity
        ):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def close(self):
        self._stop.set()

        listener = self._listener
        self._listener = None

        if listener is not None:
            listener.close()

        thread = self._thread

        if thread is not None:
            thread.join(timeout=3.0)

            if thread.is_alive():
                raise RuntimeError(
                    "IPC listener did not stop"
                )

        self._thread = None

        self._remove_owned_socket()
