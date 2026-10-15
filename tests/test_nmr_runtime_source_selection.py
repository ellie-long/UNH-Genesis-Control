"""Offline regression tests for protected NMR software selection."""

from online_fitter.protected_runtime import ProtectedNMRRuntime
from online_fitter.source_state import NMRSourceState


class EventAdapterSpy:
    def __init__(self):
        self.invalidations = 0

    def invalidate(self):
        self.invalidations += 1


def make_runtime():
    runtime = object.__new__(ProtectedNMRRuntime)
    runtime.source_state = NMRSourceState()
    runtime.source_state.select("pynmr")
    runtime.event_adapter = EventAdapterSpy()
    runtime._commissioning_armed = True
    runtime._commissioning_invalid = False
    runtime._commissioning_route_epoch = 42
    return runtime


def test_source_change_invalidates_prior_authorization():
    runtime = make_runtime()
    previous_epoch = runtime.source_state.snapshot()["route_epoch"]

    runtime.select_source("labview")

    state = runtime.source_state.snapshot()
    assert state["selected_source"] == "labview"
    assert state["route_epoch"] > previous_epoch
    assert runtime.event_adapter.invalidations == 1
    assert runtime._commissioning_armed is False
    assert runtime._commissioning_route_epoch is None


def test_same_source_does_not_invalidate():
    runtime = make_runtime()
    previous_epoch = runtime.source_state.snapshot()["route_epoch"]

    runtime.select_source("pynmr")

    assert runtime.source_state.snapshot()["route_epoch"] == previous_epoch
    assert runtime.event_adapter.invalidations == 0


def test_invalid_source_rejected_without_mutation():
    runtime = make_runtime()
    previous_state = runtime.source_state.snapshot()

    try:
        runtime.select_source("invalid")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid source was accepted")

    assert runtime.source_state.snapshot() == previous_state
    assert runtime.event_adapter.invalidations == 0


def test_commissioning_failure_latch_preserved():
    runtime = make_runtime()
    runtime._commissioning_invalid = True

    runtime.select_source("labview")

    assert runtime._commissioning_invalid is True
    assert runtime._commissioning_armed is False
    assert runtime.event_adapter.invalidations == 1
