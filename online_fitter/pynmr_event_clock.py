"""Convert same-host PyNMR UTC event times to Genesis monotonic time.

PyNMR EventData.start_stamp and stop_stamp are UTC Unix seconds.
Genesis NMRSourceState uses time.monotonic seconds.

This helper establishes and checks a bounded local clock offset.
It returns conservative acquisition boundaries, accounting for
the allowed offset uncertainty.

IMPORTANT:
- This does not authenticate the producer.
- This does not attest a packet.
- The source must share the Ubuntu/WSL host timebase.
- The trusted adapter must arm this bridge before acquisition.
- Remote acquisition computers require another clock contract.
- No EPICS or hardware operations are performed here.
"""

import math
import threading
import time


class PyNMREventClock:
    def __init__(
        self,
        *,
        monotonic=time.monotonic,
        utc=time.time,
        max_offset_shift_s=0.2,
        max_sample_latency_s=0.05,
        post_arm_guard_s=0.1,
        max_event_age_s=30.0,
    ):
        self.monotonic = monotonic
        self.utc = utc
        self.max_offset_shift_s = max_offset_shift_s
        self.max_sample_latency_s = max_sample_latency_s
        self.post_arm_guard_s = post_arm_guard_s
        self.max_event_age_s = max_event_age_s

        self._lock = threading.RLock()
        self._offset = None
        self._armed_at = None

    @staticmethod
    def _number(value):
        if type(value) not in (int, float):
            raise ValueError("Invalid event timestamp")

        value = float(value)

        if not math.isfinite(value):
            raise ValueError("Nonfinite event timestamp")

        return value

    def _sample(self):
        before = self._number(self.monotonic())
        wall = self._number(self.utc())
        after = self._number(self.monotonic())

        latency = after - before

        if (
            latency < 0
            or latency > self.max_sample_latency_s
        ):
            raise ValueError("Unreliable clock sample")

        midpoint = (before + after) / 2.0
        offset = wall - midpoint

        return offset, after, latency

    def arm(self):
        """Arm clock conversion before new acquisitions begin."""
        with self._lock:
            self._offset = None
            self._armed_at = None

            offset, now, _ = self._sample()

            self._offset = offset
            self._armed_at = now

    def invalidate(self):
        """Require fresh arming after clock or source transitions."""
        with self._lock:
            self._offset = None
            self._armed_at = None

    def convert(self, start_stamp, stop_stamp):
        """Return conservative monotonic (started_at, completed_at).

        These UTC stamps must come from a trusted PyNMR EventData
        object, never from an unauthenticated TCP spectrum.
        """

        with self._lock:
            if self._offset is None:
                raise ValueError("Event clock not armed")

            start_utc = self._number(start_stamp)
            stop_utc = self._number(stop_stamp)

            if start_utc >= stop_utc:
                raise ValueError("Invalid acquisition interval")

            current_offset, now, sample_latency = self._sample()

            if (
                abs(current_offset - self._offset)
                > self.max_offset_shift_s
            ):
                self.invalidate()
                raise ValueError("Host clock offset changed")

            # Widen the interval, never narrow it:
            # earliest plausible start and latest plausible end.
            uncertainty = (
                self.max_offset_shift_s
                + sample_latency / 2.0
            )

            start_mono = (
                start_utc - current_offset - uncertainty
            )
            stop_mono = (
                stop_utc - current_offset + uncertainty
            )

            if (
                start_mono
                <= self._armed_at + self.post_arm_guard_s
            ):
                raise ValueError(
                    "Acquisition predates verified clock arm"
                )

            if stop_mono > now:
                raise ValueError(
                    "Completion time not yet safely established"
                )

            if now - stop_mono > self.max_event_age_s:
                raise ValueError("Stale acquisition event")

            return start_mono, stop_mono
