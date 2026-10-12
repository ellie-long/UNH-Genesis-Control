"""Offline verification of PyNMR completed-event admission."""

from types import SimpleNamespace

from online_fitter.source_state import NMRSourceState
from online_fitter.packet_binding import PacketBoundAdmission
from online_fitter.pynmr_event_clock import PyNMREventClock
from online_fitter.pynmr_event_adapter import (
    PyNMRCompletedEventAdapter,
)


class FakeClock:
    def __init__(self):
        self.mono = 100.0
        self.offset = 1700000000.0

    def monotonic(self):
        return self.mono

    def utc(self):
        return self.mono + self.offset


def setup():
    clock = FakeClock()

    state = NMRSourceState(
        clock=clock.monotonic
    )
    state.select("pynmr")
    state.update_stream("pynmr", True)
    state.update_route(1, 3)

    binding = PacketBoundAdmission(state)

    converter = PyNMREventClock(
        monotonic=clock.monotonic,
        utc=clock.utc,
    )

    adapter = PyNMRCompletedEventAdapter(
        state,
        binding,
        event_clock=converter,
    )

    return clock, state, binding, adapter


def finished_event(clock, start=101.0, stop=101.5):
    event = SimpleNamespace(
        start_stamp=clock.offset + start,
        stop_stamp=clock.offset + stop,
        scan=SimpleNamespace(
            num=32,
            freq_list=[32.6, 32.7, 32.8],
        ),
    )

    message = {
        "type": "sweep",
        "source": "pynmr",
        "centfreq_mhz": 32.7,
        "span_mhz": 0.3,
        "npts": 3,
        "raw": [1.0, 2.0, 3.0],
        "baseline_sub": [0.1, 0.2, 0.3],
        "freqs_mhz": [32.6, 32.7, 32.8],
        "sweeps": 32,
        "stop_stamp": event.stop_stamp,
    }

    return event, message


def test_requires_verified_route():
    _, state, _, adapter = setup()

    state.update_route(1, 0)

    assert not adapter.arm()


def test_valid_completed_event_can_be_bound_once():
    clock, _, binding, adapter = setup()

    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(clock)

    assert adapter.attest_completed_event(event, message)

    first = binding.observe_packet(message, "pynmr")
    second = binding.observe_packet(message, "pynmr")

    assert first["post_switch_ready"] is True
    assert second["post_switch_ready"] is False


def test_unarmed_adapter_rejects_event():
    clock, _, _, adapter = setup()

    clock.mono = 102.0
    event, message = finished_event(clock)

    assert not adapter.attest_completed_event(
        event, message
    )


def test_wrong_event_timestamp_rejected():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(clock)

    message["stop_stamp"] += 1.0

    assert not adapter.attest_completed_event(
        event, message
    )


def test_spectrum_geometry_mismatch_rejected():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(clock)

    message["freqs_mhz"] = [1.0, 2.0, 3.0]

    assert not adapter.attest_completed_event(
        event, message
    )


def test_prior_route_epoch_cannot_attest():
    clock, state, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 101.0
    state.update_route(1, 0)
    state.update_route(1, 3)

    clock.mono = 102.0
    event, message = finished_event(clock)

    assert not adapter.attest_completed_event(
        event, message
    )


def test_acquisition_before_route_arm_rejected():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(
        clock, start=99.0, stop=101.5
    )

    assert not adapter.attest_completed_event(
        event, message
    )


def test_stale_queued_event_rejected():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 140.0
    event, message = finished_event(clock)

    assert not adapter.attest_completed_event(
        event, message
    )


def test_wrong_source_tag_rejected():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(clock)

    message["source"] = "labview"

    assert not adapter.attest_completed_event(
        event, message
    )


def test_clock_jump_fails_closed():
    clock, _, _, adapter = setup()
    assert adapter.arm()

    clock.mono = 102.0
    event, message = finished_event(clock)

    clock.offset += 1.0

    assert not adapter.attest_completed_event(
        event, message
    )


def test_source_change_revokes_arm():
    clock, state, _, adapter = setup()
    assert adapter.arm()

    state.select("labview")
    state.select("pynmr")

    clock.mono = 102.0
    event, message = finished_event(clock)

    assert not adapter.attest_completed_event(
        event, message
    )
