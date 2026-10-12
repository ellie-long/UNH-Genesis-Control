"""Synthetic tests; no actual EPICS connections."""

from online_fitter.nmr_switch_readback import NMRReadbackPoller
from online_fitter.source_state import NMRSourceState


class FakePV:

    def __init__(self, values):
        self.values = list(values)
        self.connected = True
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.values.pop(0)


def setup(comm=(1, 1), mask=(3, 3)):
    clock = [100.0]
    state = NMRSourceState(clock=lambda: clock[0])
    state.select("pynmr")
    state.update_stream("pynmr", True)

    pvs = {}

    def factory(name, **kwargs):
        assert kwargs == {"auto_monitor": False}
        assert name.endswith((":COMM", ":MASK_RBV"))

        values = comm if name.endswith(":COMM") else mask
        pv = FakePV(values)
        pvs[name.split(":")[-1]] = pv
        return pv

    poller = NMRReadbackPoller(
        state, factory, clock=lambda: clock[0]
    )

    return clock, state, poller, pvs


def test_verified_port2_readback():
    clock, state, poller, pvs = setup()
    clock[0] = 101.0

    assert poller.poll_once()
    assert state.snapshot()["nmr_mask"] == 3
    assert not state.snapshot()["post_switch_ready"]

    for pv in pvs.values():
        assert len(pv.calls) == 2
        assert all(
            call["use_monitor"] is False
            for call in pv.calls
        )


def test_mismatched_samples_fail_closed():
    clock, state, poller, _ = setup(mask=(0, 3))
    clock[0] = 101.0

    assert not poller.poll_once()
    assert state.snapshot()["nmr_mask"] is None
    assert not state.snapshot()["post_switch_ready"]


def test_disconnected_pv_fails_closed():
    clock, state, poller, pvs = setup()
    pvs["COMM"].connected = False

    clock[0] = 101.0
    assert not poller.poll_once()
    assert state.snapshot()["nmr_comm"] is None


def test_invalid_comm_and_mixed_mask():
    for comm, mask in ((0, 3), (1, 2)):
        clock, state, poller, _ = setup(
            comm=(comm, comm),
            mask=(mask, mask),
        )
        clock[0] = 101.0

        assert not poller.poll_once()
        assert not state.snapshot()["post_switch_ready"]


def test_failed_read_invalidates_previous_event():
    clock, state, poller, pvs = setup()
    clock[0] = 101.0
    assert poller.poll_once()

    clock[0] = 102.0
    assert state.completed_event(
        "pynmr", 101.5, 102.0
    )
    assert state.snapshot()["post_switch_ready"]

    pvs["COMM"].connected = False
    clock[0] = 103.0

    assert not poller.poll_once()
    assert not state.snapshot()["post_switch_ready"]

    pvs["COMM"].connected = True
    pvs["COMM"].values = [1, 1]
    pvs["MASK_RBV"].values = [3, 3]

    clock[0] = 104.0
    assert poller.poll_once()

    clock[0] = 105.0

    assert not state.completed_event(
        "pynmr", 101.5, 105.0
    )

    assert state.completed_event(
        "pynmr", 104.5, 105.0
    )


def test_invalid_epics_value_fails_closed():
    clock, state, poller, _ = setup(
        mask=(None, None)
    )
    clock[0] = 101.0

    assert not poller.poll_once()
    assert not state.snapshot()["post_switch_ready"]
