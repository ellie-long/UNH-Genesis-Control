"""Trusted NMR route and post-switch acquisition barrier.

No hardware access or EPICS writes.

Only the trusted EPICS readback adapter may call update_route.
Only a source-specific acquisition adapter may call completed_event.

All acquisition times must use the same local monotonic clock.
Never use timestamps copied directly from incoming JSON as proof.
"""

from __future__ import annotations

import math
import threading
import time


SOURCES = frozenset(("labview", "pynmr"))
NMR_CONNECTED_MASK = 0


class NMRSourceState:

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()

        self._selected = None
        self._mask = None
        self._comm = None

        self._route_at = None
        self._confirmed_at = None
        self._transition_at = None
        self._last_event_at = None
        self._pending = False
        self._route_epoch = 0

        self._streams = {
            "labview": False,
            "pynmr": False,
        }

    def _invalidate(self, now):
        self._route_epoch += 1
        self._transition_at = now
        self._route_at = None
        self._confirmed_at = None
        self._last_event_at = None
        self._pending = False

    def select(self, source):
        if source not in SOURCES:
            raise ValueError("invalid NMR source")

        with self._lock:
            if source != self._selected:
                self._selected = source
                self._invalidate(self._clock())

    def update_route(self, comm, mask):
        with self._lock:
            now = self._clock()

            if (comm, mask) != (self._comm, self._mask):
                self._invalidate(now)

            self._comm = comm
            self._mask = mask
            self._route_at = now

            if (
                type(comm) is int
                and comm == 1
                and type(mask) is int
                and mask == NMR_CONNECTED_MASK
                and self._selected in SOURCES
                and self._confirmed_at is None
            ):
                self._confirmed_at = now

    def update_stream(self, source, connected):
        if (
            source not in SOURCES
            or type(connected) is not bool
        ):
            raise ValueError("invalid trusted stream status")

        with self._lock:
            previously = self._streams[source]
            self._streams[source] = connected

            if (
                source == self._selected
                and previously
                and not connected
            ):
                self._invalidate(self._clock())

    def completed_event(
        self,
        source,
        started_at,
        completed_at,
    ):
        """Record one trusted, newly completed acquisition event."""

        with self._lock:
            now = self._clock()
            times = (started_at, completed_at)

            if any(
                type(t) not in (int, float)
                or not math.isfinite(t)
                for t in times
            ):
                return False

            if (
                source != self._selected
                or self._streams.get(source) is not True
                or self._confirmed_at is None
                or self._route_at is None
                or type(self._comm) is not int
                or self._comm != 1
                or type(self._mask) is not int
                or self._mask != NMR_CONNECTED_MASK
                or not 0 <= now - self._route_at <= 5.0
                or not (
                    self._confirmed_at
                    < started_at
                    <= completed_at
                    <= now
                )
                or now - completed_at > 30.0
                or (
                    self._last_event_at is not None
                    and completed_at <= self._last_event_at
                )
            ):
                return False

            self._last_event_at = completed_at
            self._pending = True
            return True

    def snapshot(self):
        """Return an atomic status observation."""

        with self._lock:
            now = self._clock()

            route_age = (
                None if self._route_at is None
                else now - self._route_at
            )

            data_age = (
                None if self._last_event_at is None
                else now - self._last_event_at
            )

            return {
                "selected_source": self._selected,
                "route_epoch": self._route_epoch,
                "nmr_mask": self._mask,
                "nmr_comm": self._comm,
                "route_age_s": route_age,
                "data_age_s": data_age,
                "stream_connected": self._streams.get(
                    self._selected, False
                ),
                "post_switch_ready": (
                    self._pending
                    and self._confirmed_at is not None
                    and self._last_event_at is not None
                    and route_age is not None
                    and 0 <= route_age <= 5.0
                    and data_age is not None
                    and 0 <= data_age <= 30.0
                ),
            }

    def fit_guard(self):
        """Serialize fitting with observed route/selection updates.

        All NMRSourceState transitions use this same RLock.
        This does not control an independently operated
        physical switch before its EPICS readback changes.
        """
        return self._lock

    def take_for_sweep(self):
        """Consume one validated event ticket.

        Bind this method to SourceAwareProcessor.observe.
        The trusted input adapter must associate each ticket
        with the corresponding acquisition packet.
        """

        with self._lock:
            observation = self.snapshot()
            self._pending = False
            return observation
