"""Packet-bound NMR acquisition admission.

A trusted producer adapter must attest the finished event and
the exact message together, before the message is queued.

An NDJSON packet, source tag, timestamp, or TCP connection
must never mint its own acquisition attestation.

This module has no EPICS or instrument operations.
"""

import hashlib
import hmac
import json
import threading


def packet_digest(message):
    """Fingerprint the full, canonical JSON spectrum."""
    if not isinstance(message, dict):
        raise ValueError("Expected JSON spectrum dictionary")

    encoded = json.dumps(
        message,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).digest()


class PacketBoundAdmission:
    def __init__(self, source_state):
        self.source_state = source_state
        self._lock = threading.RLock()
        self._pending = None

    def attest_finished_event(
        self,
        source,
        message,
        started_at,
        completed_at,
    ):
        """Called ONLY by a trusted acquisition-event adapter.

        Times must be measured in the same monotonic clock domain
        as source_state. Do not derive them from incoming NDJSON.
        """

        with self._lock:
            self._pending = None

            if source not in ("labview", "pynmr"):
                return False

            if not isinstance(message, dict):
                return False

            if message.get("type", "sweep") != "sweep":
                return False

            tag = message.get("source")

            if source == "pynmr" and tag != "pynmr":
                return False

            if source == "labview" and tag not in (
                None, "labview"
            ):
                return False

            try:
                digest = packet_digest(message)
            except (TypeError, ValueError, OverflowError):
                return False

            if not self.source_state.completed_event(
                source, started_at, completed_at
            ):
                return False

            observation = self.source_state.snapshot()

            if observation.get("post_switch_ready") is not True:
                return False

            self._pending = (
                source,
                digest,
                observation["route_epoch"],
            )
            return True

    def observe_packet(self, message, trusted_source):
        """Observe exactly one matching packet, once."""

        with self._lock:
            observation = self.source_state.snapshot()
            pending = self._pending

            if pending is None:
                observation["post_switch_ready"] = False
                return observation

            expected_source, expected_digest, epoch = pending

            try:
                actual_digest = packet_digest(message)
            except (TypeError, ValueError, OverflowError):
                observation["post_switch_ready"] = False
                return observation

            if (
                trusted_source != expected_source
                or observation.get("route_epoch") != epoch
                or not hmac.compare_digest(
                    actual_digest, expected_digest
                )
            ):
                observation["post_switch_ready"] = False
                return observation

            # Consume the matching ticket exactly once.
            self._pending = None

            observation = self.source_state.take_for_sweep()

            if observation.get("route_epoch") != epoch:
                observation["post_switch_ready"] = False

            return observation
