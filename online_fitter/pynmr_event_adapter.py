"""PyNMR completed-event admission for Genesis.

This adapter is an internal verification component.
It MUST be invoked only after the producer's identity and
event provenance have been independently authenticated.

It does not authenticate remote callers or open a socket.
It does not operate the NMR switch or authorize RF transmission.
"""

import math

from .pynmr_event_clock import PyNMREventClock


class PyNMRCompletedEventAdapter:
    def __init__(
        self,
        source_state,
        packet_binding,
        *,
        event_clock=None,
    ):
        self.source_state = source_state
        self.packet_binding = packet_binding
        self.event_clock = (
            event_clock
            if event_clock is not None
            else PyNMREventClock()
        )
        self._armed_epoch = None

    @staticmethod
    def _route_ready(snapshot):
        age = snapshot.get("route_age_s")

        return (
            snapshot.get("selected_source") == "pynmr"
            and type(snapshot.get("nmr_mask")) is int
            and snapshot["nmr_mask"] == 3
            and type(snapshot.get("nmr_comm")) is int
            and snapshot["nmr_comm"] == 1
            and snapshot.get("stream_connected") is True
            and type(age) in (int, float)
            and math.isfinite(age)
            and 0 <= age <= 5.0
            and type(snapshot.get("route_epoch")) is int
        )

    def invalidate(self):
        """Discard the local event clock authorization."""
        with self.source_state.fit_guard():
            self._armed_epoch = None
            self.event_clock.invalidate()

    def arm(self):
        """Arm only after the selected PyNMR route is verified."""
        with self.source_state.fit_guard():
            self.invalidate()

            observation = self.source_state.snapshot()

            if not self._route_ready(observation):
                return False

            try:
                self.event_clock.arm()
            except (ValueError, OverflowError):
                self.invalidate()
                return False

            self._armed_epoch = observation["route_epoch"]
            return True

    @staticmethod
    def _matches_finished_event(event, message):
        """Require consistency with the producer's event object.

        The event object must originate from an authenticated
        producer callback, not from an incoming TCP payload.
        """
        if not isinstance(message, dict):
            return False

        if (
            message.get("type") != "sweep"
            or message.get("source") != "pynmr"
        ):
            return False

        try:
            stop = event.stop_stamp
            start = event.start_stamp

            if any(
                type(value) not in (int, float)
                or not math.isfinite(value)
                for value in (start, stop)
            ):
                return False

            packet_stop = message.get("stop_stamp")

            if (
                type(packet_stop) not in (int, float)
                or not math.isfinite(packet_stop)
                or packet_stop != stop
            ):
                return False

            npts = len(event.scan.freq_list)
            sweeps = int(event.scan.num)

            if (
                type(message.get("npts")) is not int
                or message["npts"] != npts
                or npts < 2
                or sweeps <= 0
                or type(message.get("sweeps")) is not int
                or message["sweeps"] != sweeps
            ):
                return False

            if (
                len(message["raw"]) != npts
                or len(message["baseline_sub"]) != npts
                or len(message["freqs_mhz"]) != npts
            ):
                return False

            if (
                list(message["freqs_mhz"])
                != list(event.scan.freq_list)
            ):
                return False

            return True

        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            return False

    def attest_completed_event(self, event, message):
        """Attest a producer-verified finished event and its spectrum.

        The caller must already have authenticated the producing
        PyNMR process and confirmed that 'message' was constructed
        from this specific finished EventData instance.

        Incoming NDJSON alone is never sufficient.
        """
        with self.source_state.fit_guard():
            observation = self.source_state.snapshot()

            if (
                self._armed_epoch is None
                or not self._route_ready(observation)
                or observation["route_epoch"] != self._armed_epoch
            ):
                self.invalidate()
                return False

            if not self._matches_finished_event(
                event, message
            ):
                return False

            try:
                started_at, completed_at = (
                    self.event_clock.convert(
                        event.start_stamp,
                        event.stop_stamp,
                    )
                )
            except (ValueError, OverflowError):
                return False

            return self.packet_binding.attest_finished_event(
                "pynmr",
                message,
                started_at,
                completed_at,
            )
