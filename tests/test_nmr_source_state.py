"""Offline tests for the trusted NMR acquisition barrier."""

from online_fitter.source_state import NMRSourceState
from online_fitter.source_receiver import SourceAwareProcessor


def prepare(source="labview"):
    clock = [100.0]
    state = NMRSourceState(clock=lambda: clock[0])

    state.select(source)
    state.update_stream(source, True)

    clock[0] = 101.0
    state.update_route(
        1, 0
    )
    return clock, state


def test_default_fails_closed():
    state = NMRSourceState()
    assert not state.snapshot()["post_switch_ready"]


def test_new_event_required_after_route_change():
    clock, state = prepare()

    clock[0] = 102.0
    assert not state.completed_event(
        "labview", 100.0, 102.0
    )
    assert not state.snapshot()["post_switch_ready"]

    assert state.completed_event(
        "labview", 101.5, 102.0
    )
    assert state.snapshot()["post_switch_ready"]

    state.select("pynmr")
    assert not state.snapshot()["post_switch_ready"]

    state.update_stream("pynmr", True)
    clock[0] = 103.0
    state.update_route(1, 0)

    clock[0] = 104.0
    assert not state.completed_event(
        "pynmr", 100.0, 104.0
    )
    assert state.completed_event(
        "pynmr", 103.5, 104.0
    )
    assert state.snapshot()["post_switch_ready"]


def test_disconnect_invalidates_event():
    clock, state = prepare("pynmr")

    clock[0] = 102.0
    assert state.completed_event(
        "pynmr", 101.2, 102.0
    )

    state.update_stream("pynmr", False)
    assert not state.snapshot()["post_switch_ready"]

    state.update_stream("pynmr", True)
    assert not state.completed_event(
        "pynmr", 102.01, 102.01
    )

    clock[0] = 103.0
    state.update_route(1, 0)

    clock[0] = 104.0
    assert state.completed_event(
        "pynmr", 103.5, 104.0
    )


def test_stale_route_and_event():
    clock, state = prepare()

    clock[0] = 102.0
    assert state.completed_event(
        "labview", 101.3, 102.0
    )

    clock[0] = 107.0
    assert not state.snapshot()["post_switch_ready"]

    state.update_route(1, 0)
    assert state.snapshot()["post_switch_ready"]

    clock[0] = 133.0
    state.update_route(1, 0)
    assert not state.snapshot()["post_switch_ready"]


def test_mixed_and_unknown_never_ready():
    clock, state = prepare()

    clock[0] = 102.0
    assert state.completed_event(
        "labview", 101.3, 102.0
    )

    state.update_route(1, 2)
    assert not state.snapshot()["post_switch_ready"]

    state.update_route(0, 0)
    assert not state.snapshot()["post_switch_ready"]


def test_trusted_event_requires_valid_times():
    clock, state = prepare()
    clock[0] = 102.0

    cases = [
        (None, 102),
        (float("nan"), 102),
        (101.5, 105),
        (102, 101.5),
    ]

    for started, completed in cases:
        assert not state.completed_event(
            "labview", started, completed
        )


def test_receiver_rejects_then_accepts():
    clock, state = prepare("pynmr")
    calls = []

    receiver = SourceAwareProcessor(
        state.take_for_sweep,
        fit=lambda _, msg: (
            calls.append(msg) or {"ok": True}
        ),
    )

    assert not receiver(
        None, {"source": "pynmr"}, "pynmr"
    )["ok"]

    clock[0] = 102.0
    assert state.completed_event(
        "pynmr", 101.5, 102.0
    )

    assert receiver(
        None, {"source": "pynmr"}, "pynmr"
    )["ok"]

    assert len(calls) == 1


def test_event_ticket_cannot_be_reused():
    clock, state = prepare("pynmr")
    clock[0] = 102.0

    assert state.completed_event(
        "pynmr", 101.5, 102.0
    )

    first = state.take_for_sweep()
    second = state.take_for_sweep()

    assert first["post_switch_ready"] is True
    assert second["post_switch_ready"] is False


def test_duplicate_event_cannot_create_ticket():
    clock, state = prepare()
    clock[0] = 102.0

    assert state.completed_event(
        "labview", 101.5, 102.0
    )
    assert state.take_for_sweep()["post_switch_ready"]

    assert not state.completed_event(
        "labview", 101.5, 102.0
    )
    assert not state.take_for_sweep()["post_switch_ready"]


def test_labview_untagged_packet_with_ticket():
    clock, state = prepare()
    clock[0] = 102.0

    assert state.completed_event(
        "labview", 101.5, 102.0
    )

    calls = []

    receiver = SourceAwareProcessor(
        state.take_for_sweep,
        fit=lambda _, msg: (
            calls.append(msg) or {"ok": True}
        ),
    )

    assert receiver(
        None, {"type": "sweep"}, "labview"
    )["ok"]

    assert not receiver(
        None, {"type": "sweep"}, "labview"
    )["ok"]

    assert len(calls) == 1
