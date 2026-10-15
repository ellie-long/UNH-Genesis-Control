"""Restricted Linux Unix-socket control for Genesis NMR software selection.

Explicitly opt-in. No EPICS or RF hardware writes.
The serving process must pin the trusted sequencer's UID and PID.
"""

import asyncio
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys


SOURCES = frozenset(("labview", "pynmr"))
MAX_LINE = 512


def apply_source_request(*, peer_uid, peer_pid, allowed_uid, allowed_pid,
                         request, snapshot, select_source):
    """Validate kernel-authenticated peer and execute one source transition."""
    if (type(peer_uid) is not int or type(peer_pid) is not int
            or peer_uid != allowed_uid or peer_pid != allowed_pid
            or peer_pid <= 0):
        raise PermissionError("Unrecognized sequencer process")
    if type(request) is not dict or set(request) != {"source"}:
        raise ValueError("Invalid control request")
    target = request["source"]
    if type(target) is not str or target not in SOURCES:
        raise ValueError("Invalid NMR software source")

    before = snapshot()
    if type(before) is not dict:
        raise RuntimeError("Fitter source state unavailable")
    initial_source = before.get("selected_source")
    initial_epoch = before.get("route_epoch")
    if (initial_source not in SOURCES or type(initial_epoch) is not int
            or initial_epoch < 0):
        raise RuntimeError("Fitter source state unverified")
    if initial_source == target:
        raise ValueError("NMR source already selected")

    # ProtectedNMRRuntime.select_source() holds its fit_guard and revokes
    # prior event authorization. No Mini-Circuits or SMC command is issued.
    select_source(target)

    after = snapshot()
    if (type(after) is not dict or after.get("selected_source") != target
            or type(after.get("route_epoch")) is not int
            or after["route_epoch"] <= initial_epoch):
        raise RuntimeError("Fitter source transition unverified")
    return {"selected_source": target, "route_epoch": after["route_epoch"]}


def _secure_socket_parent(path):
    p = Path(path)
    if not p.is_absolute():
        raise ValueError("IPC socket path must be absolute")
    parent = p.parent
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError("IPC directory must belong to fitter user")
    if info.st_mode & 0o022:
        raise PermissionError("IPC directory must not be group/world writable")
    if p.exists() or p.is_symlink():
        raise FileExistsError("IPC socket path already exists")
    return p


class NMRSourceControlIPC:
    """Linux-only, bounded-line, one-request-per-connection IPC server.

    Construct/start only when the trusted sequencer's UID and PID have
    been pinned independently. Never infer them from the connecting peer.
    """

    def __init__(self, path, *, allowed_uid, allowed_pid, snapshot, select_source):
        if type(allowed_uid) is not int or allowed_uid < 0:
            raise ValueError("Explicit Linux sequencer UID required")
        if type(allowed_pid) is not int or allowed_pid <= 0:
            raise ValueError("Explicit Linux sequencer PID required")
        if not callable(snapshot) or not callable(select_source):
            raise TypeError("Protected runtime callbacks required")
        self.path = str(path)
        self.allowed_uid = allowed_uid
        self.allowed_pid = allowed_pid
        self.snapshot = snapshot
        self.select_source = select_source
        self.server = None
        self._inode = None

    async def start(self):
        if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("NMR source control IPC requires Linux SO_PEERCRED")
        if self.server is not None:
            raise RuntimeError("NMR source control IPC already running")
        p = _secure_socket_parent(self.path)
        self.server = await asyncio.start_unix_server(
            self._handle_client, path=self.path, limit=MAX_LINE + 1,
        )
        try:
            os.chmod(self.path, 0o600)
            self._inode = os.stat(self.path).st_ino
        except BaseException:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            raise
        return self

    async def _handle_client(self, reader, writer):
        try:
            peer_socket = writer.get_extra_info("socket")
            if peer_socket is None:
                raise PermissionError("Missing authenticated peer socket")
            credentials = peer_socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            peer_pid, peer_uid, _peer_gid = struct.unpack("3i", credentials)
            if peer_uid != self.allowed_uid or peer_pid != self.allowed_pid:
                raise PermissionError("Unrecognized sequencer process")
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise ValueError("Invalid request framing")
            request = json.loads(line.decode("utf-8"))
            result = apply_source_request(
                peer_uid=peer_uid, peer_pid=peer_pid,
                allowed_uid=self.allowed_uid, allowed_pid=self.allowed_pid,
                request=request, snapshot=self.snapshot,
                select_source=self.select_source,
            )
            reply = {"ok": True, **result}
        except (Exception, asyncio.CancelledError):
            # Do not expose internal state or credentials to untrusted peers.
            reply = {"ok": False, "error": "rejected"}
        try:
            writer.write((json.dumps(reply, separators=(",", ":")) + "\n").encode())
            await asyncio.wait_for(writer.drain(), timeout=2.0)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def close(self):
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None
        try:
            if self._inode is not None and os.lstat(self.path).st_ino == self._inode:
                os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._inode = None
