"""Portable PyNMR -> Genesis completed-event publisher.

This module uses only Python's standard library.

Integration contract:
1. Construct the event envelope from the real PyNMR EventData
   and its corresponding built sweep on EVENT_FINISHED.
2. Put both the sweep and envelope into the same worker queue.
3. The worker sends the envelope to Genesis IPC first.
4. Only after a successful acknowledgement should the worker
   transmit that sweep to the protected fitter TCP socket.

Never perform socket I/O on the PyNMR GUI/event-bus thread.

IPC peer UID verification does not uniquely authenticate
the PyNMR process or prove acquisition provenance.
No instrument or EPICS operations are implemented here.
"""

import copy
import json
import math
import socket


PROTOCOL_VERSION = 1
KIND = "pynmr_finished_event"
MAX_FRAME_BYTES = 2_000_000


def _finite(value):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
    )


def build_event_envelope(event, sweep):
    """Capture one completed event and its exact sweep packet."""

    if not isinstance(sweep, dict):
        raise ValueError("Expected sweep dictionary")

    if (
        sweep.get("type") != "sweep"
        or sweep.get("source") != "pynmr"
    ):
        raise ValueError("Not a PyNMR sweep")

    try:
        start = float(event.start_stamp)
        stop = float(event.stop_stamp)
        sweeps = int(event.scan.num)

        freqs = [
            float(value)
            for value in event.scan.freq_list
        ]
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        raise ValueError(
            "Invalid finished PyNMR event"
        ) from exc

    if (
        not math.isfinite(start)
        or not math.isfinite(stop)
        or start >= stop
        or sweeps <= 0
        or not 2 <= len(freqs) <= 8192
        or not all(math.isfinite(f) for f in freqs)
    ):
        raise ValueError("Invalid acquisition metadata")

    message = copy.deepcopy(sweep)

    if (
        message.get("stop_stamp") != stop
        or message.get("sweeps") != sweeps
        or message.get("npts") != len(freqs)
        or message.get("freqs_mhz") != freqs
    ):
        raise ValueError(
            "Finished event and sweep mismatch"
        )

    for key in ("raw", "baseline_sub"):
        values = message.get(key)

        if (
            not isinstance(values, list)
            or len(values) != len(freqs)
            or not all(_finite(x) for x in values)
        ):
            raise ValueError(
                "Invalid spectrum array: " + key
            )

    return {
        "version": PROTOCOL_VERSION,
        "kind": KIND,
        "event": {
            "start_stamp": start,
            "stop_stamp": stop,
            "sweeps": sweeps,
            "freqs_mhz": freqs,
        },
        "sweep": message,
    }


def publish_event(
    socket_path,
    envelope,
    *,
    timeout_s=0.75,
):
    """Submit from the worker thread; never from the GUI thread.

    Returns True only if Genesis explicitly accepted the event.
    Connection, timeout, or protocol errors fail closed.
    """

    if (
        type(timeout_s) not in (int, float)
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
        or timeout_s > 5
    ):
        raise ValueError("Invalid IPC timeout")

    try:
        frame = (
            json.dumps(
                envelope,
                allow_nan=False,
                separators=(",", ":"),
            ) + "\n"
        ).encode("utf-8")

        if len(frame) > MAX_FRAME_BYTES:
            return False

        with socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        ) as connection:
            connection.settimeout(timeout_s)
            connection.connect(str(socket_path))
            connection.sendall(frame)

            with connection.makefile("rb") as reader:
                response = reader.readline(4096)

        if (
            not response
            or len(response) >= 4096
            or not response.endswith(b"\n")
        ):
            return False

        reply = json.loads(response.decode("utf-8"))

        return (
            isinstance(reply, dict)
            and reply.get("ok") is True
        )

    except (
        OSError,
        TimeoutError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
    ):
        return False
