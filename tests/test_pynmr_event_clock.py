"""Offline tests for PyNMR UTC-to-monotonic event boundaries."""

import pytest

from online_fitter.pynmr_event_clock import PyNMREventClock


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

    bridge = PyNMREventClock(
        monotonic=clock.monotonic,
        utc=clock.utc,
    )

    return clock, bridge


def stamp(clock, mono):
    return clock.offset + mono


def test_unarmed_clock_rejects_event():
    clock, bridge = setup()
    clock.mono = 103.0

    with pytest.raises(ValueError, match="not armed"):
        bridge.convert(
            stamp(clock, 101.0),
            stamp(clock, 102.0),
        )


def test_completed_event_has_conservative_bounds():
    clock, bridge = setup()
    bridge.arm()

    clock.mono = 103.0

    started, completed = bridge.convert(
        stamp(clock, 101.0),
        stamp(clock, 102.0),
    )

    assert started < 101.0
    assert completed > 102.0
    assert started > 100.0
    assert completed < 103.0


def test_prearm_acquisition_is_rejected():
    clock, bridge = setup()
    bridge.arm()

    clock.mono = 103.0

    with pytest.raises(ValueError, match="predates"):
        bridge.convert(
            stamp(clock, 99.0),
            stamp(clock, 102.0),
        )


def test_future_completion_is_rejected():
    clock, bridge = setup()
    bridge.arm()

    clock.mono = 102.0

    with pytest.raises(ValueError, match="Completion"):
        bridge.convert(
            stamp(clock, 101.0),
            stamp(clock, 103.0),
        )


def test_old_event_is_rejected():
    clock, bridge = setup()
    bridge.arm()

    clock.mono = 150.0

    with pytest.raises(ValueError, match="Stale"):
        bridge.convert(
            stamp(clock, 101.0),
            stamp(clock, 102.0),
        )


def test_clock_jump_invalidates_bridge():
    clock, bridge = setup()
    bridge.arm()

    clock.mono = 103.0
    old_start = stamp(clock, 101.0)
    old_end = stamp(clock, 102.0)

    clock.offset += 1.0

    with pytest.raises(ValueError, match="offset changed"):
        bridge.convert(old_start, old_end)

    with pytest.raises(ValueError, match="not armed"):
        bridge.convert(old_start, old_end)


@pytest.mark.parametrize(
    "start, stop",
    [
        (float("nan"), 102.0),
        (101.0, float("inf")),
        (102.0, 101.0),
        (101.0, 101.0),
        ("101", 102.0),
    ],
)
def test_invalid_intervals_rejected(start, stop):
    _, bridge = setup()
    bridge.arm()

    with pytest.raises(ValueError):
        bridge.convert(start, stop)


def test_explicit_invalidation_requires_rearming():
    clock, bridge = setup()
    bridge.arm()
    bridge.invalidate()

    clock.mono = 103.0

    with pytest.raises(ValueError, match="not armed"):
        bridge.convert(
            stamp(clock, 101.0),
            stamp(clock, 102.0),
        )
