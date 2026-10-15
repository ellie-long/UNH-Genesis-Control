"""Strict Linux-only client for the separate Genesis NMR source-control socket.

Software data-source selection only. Never operates RF or Mini-Circuits switches.
Call only from a sequencer-owned, verified source-change transaction.
"""

import asyncio
import json
import math
from pathlib import Path
import socket
import struct
import sys


MAX_REPLY = 512
SOURCES = frozenset(("labview", "pynmr"))


def verify_fitter_peer(peer_socket, *, allowed_uid, allowed_pid):
    """Pin the server to its independently configured Linux UID and PID."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED is required")
    if (type(allowed_uid) is not int or allowed_uid < 0
            or type(allowed_pid) is not int or allowed_pid <= 0):
        raise ValueError("Explicit trusted fitter UID and PID required")
    if peer_socket is None:
        raise PermissionError("Missing fitter peer socket")
    raw = peer_socket.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    pid, uid, _gid = struct.unpack("3i", raw)
    if pid != allowed_pid or uid != allowed_uid:
        raise PermissionError("Unrecognized fitter process")


def decode_source_reply(line, *, requested):
    """Validate the bounded acknowledgment; the transaction checks epoch change."""
    if type(requested) is not str or requested not in SOURCES:
        raise ValueError("Invalid NMR source")
    if (type(line) is not bytes or not line or len(line) > MAX_REPLY
            or not line.endswith(b"\n")):
        raise RuntimeError("Malformed fitter IPC acknowledgment")
    try:
        reply = json.loads(line.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("Malformed fitter IPC acknowledgment") from exc
    if type(reply) is not dict or reply.get("ok") is not True:
        raise RuntimeError("Fitter rejected source change")
    if set(reply) != {"ok", "selected_source", "route_epoch"}:
        raise RuntimeError("Unexpected fitter IPC acknowledgment")
    if (reply["selected_source"] != requested
            or type(reply["route_epoch"]) is not int
            or reply["route_epoch"] < 0):
        raise RuntimeError("Unverified fitter source acknowledgment")
    return {
        "selected_source": reply["selected_source"],
        "route_epoch": reply["route_epoch"],
    }


async def request_source_change(
    *, path, requested, allowed_fitter_uid, allowed_fitter_pid,
    timeout_s=2.0,
):
    """Issue one bounded request. Only invoke after sequencer reservation."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED is required")
    if type(path) is not str or not Path(path).is_absolute():
        raise ValueError("Absolute source-control socket path required")
    if type(requested) is not str or requested not in SOURCES:
        raise ValueError("Invalid NMR software source")
    if (type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("Positive finite IPC timeout required")
    if (type(allowed_fitter_uid) is not int or allowed_fitter_uid < 0
            or type(allowed_fitter_pid) is not int or allowed_fitter_pid <= 0):
        raise ValueError("Explicit trusted fitter UID and PID required")

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path, limit=MAX_REPLY + 1),
        timeout=timeout_s,
    )
    try:
        verify_fitter_peer(
            writer.get_extra_info("socket"),
            allowed_uid=allowed_fitter_uid,
            allowed_pid=allowed_fitter_pid,
        )
        message = (json.dumps({"source": requested}, separators=(",", ":"))
                   + "\n").encode("utf-8")
        writer.write(message)
        await asyncio.wait_for(writer.drain(), timeout=timeout_s)
        response = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
        return decode_source_reply(response, requested=requested)
    finally:
        writer.close()
        await writer.wait_closed()


async def request_source_snapshot(
    *, path, allowed_fitter_uid, allowed_fitter_pid, timeout_s=2.0,
):
    """Read authenticated fitter status without changing the source."""
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED is required")
    if type(path) is not str or not Path(path).is_absolute():
        raise ValueError("Absolute source-control socket path required")
    if (type(allowed_fitter_uid) is not int or allowed_fitter_uid < 0
            or type(allowed_fitter_pid) is not int or allowed_fitter_pid <= 0):
        raise ValueError("Explicit fitter identity required")
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Positive IPC timeout required")
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path, limit=MAX_REPLY + 1), timeout_s,
    )
    try:
        verify_fitter_peer(
            writer.get_extra_info("socket"),
            allowed_uid=allowed_fitter_uid, allowed_pid=allowed_fitter_pid,
        )
        writer.write(b'{"op":"snapshot"}\n')
        await asyncio.wait_for(writer.drain(), timeout_s)
        line = await asyncio.wait_for(reader.readline(), timeout_s)
        if (type(line) is not bytes or not line or len(line) > MAX_REPLY
                or not line.endswith(b"\n")):
            raise RuntimeError("Malformed fitter status reply")
        try:
            reply = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Malformed fitter status reply") from exc
        if (type(reply) is not dict or set(reply) !=
                {"ok", "selected_source", "route_epoch"}
                or reply.get("ok") is not True
                or reply.get("selected_source") not in SOURCES
                or type(reply.get("route_epoch")) is not int
                or reply["route_epoch"] < 0):
            raise RuntimeError("Fitter status unverified")
        return {"selected_source": reply["selected_source"],
                "route_epoch": reply["route_epoch"]}
    finally:
        writer.close()
        await writer.wait_closed()
