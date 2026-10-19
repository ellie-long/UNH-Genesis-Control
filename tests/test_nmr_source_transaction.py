"""Offline transaction regressions; no EPICS or RF hardware."""

import asyncio

import pytest

from logic_devices.nmr_source_transaction import (
    execute_nmr_source_change,
)


class FakeSequencer:
    def __init__(self, ready=True):
        self.reserved = False
        self.ready = ready
        self.releases = 0

    async def _acquire_nmr_source_change_reservation(self):
        if not self.ready or self.reserved:
            raise RuntimeError("Reservation rejected")
        self.reserved = True

    def _cancel_nmr_source_change_reservation(self):
        assert self.reserved
        self.reserved = False

    def _complete_nmr_source_change_reservation(
        self, *, confirmed
    ):
        assert confirmed is True
        assert self.reserved
        self.reserved = False
        self.releases += 1


def run(requested="labview", *, observations, fail_write=False,
        ready=True):
    sequencer = FakeSequencer(ready=ready)
    remaining = list(observations)
    writes = []

    async def read_fitter():
        if not remaining:
            raise RuntimeError("No fitter readback")
        return remaining.pop(0)

    async def write_fitter(source):
        writes.append(source)
        if fail_write:
            raise RuntimeError("Command failed")

    async def execute():
        return await execute_nmr_source_change(
            requested=requested,
            sequencer=sequencer,
            read_fitter=read_fitter,
            write_fitter=write_fitter,
            poll_attempts=2,
            poll_interval_s=0,
        )

    return execute, sequencer, writes


BEFORE = {"selected_source": "pynmr", "route_epoch": 12}
AFTER = {"selected_source": "labview", "route_epoch": 13}


def test_confirmed_source_change_releases_reservation():
    execute, seq, writes = run(observations=[BEFORE, AFTER])
    assert asyncio.run(execute()) == AFTER
    assert writes == ["labview"]
    assert seq.reserved is False
    assert seq.releases == 1


def test_wrong_source_retains_reservation():
    execute, seq, _ = run(
        observations=[BEFORE, BEFORE, BEFORE]
    )
    with pytest.raises(TimeoutError):
        asyncio.run(execute())
    assert seq.reserved is True
    assert seq.releases == 0


def test_stale_epoch_retains_reservation():
    stale = {"selected_source": "labview", "route_epoch": 12}
    execute, seq, _ = run(
        observations=[BEFORE, stale, stale]
    )
    with pytest.raises(TimeoutError):
        asyncio.run(execute())
    assert seq.reserved is True


def test_failed_write_retains_reservation():
    execute, seq, writes = run(
        observations=[BEFORE], fail_write=True
    )
    with pytest.raises(RuntimeError, match="Command failed"):
        asyncio.run(execute())
    assert writes == ["labview"]
    assert seq.reserved is True


def test_reservation_rejection_never_writes():
    execute, seq, writes = run(
        observations=[BEFORE, AFTER], ready=False
    )
    with pytest.raises(RuntimeError, match="Reservation rejected"):
        asyncio.run(execute())
    assert writes == []
    assert seq.reserved is False


def test_invalid_source_never_reserves_or_writes():
    execute, seq, writes = run(
        requested="invalid", observations=[BEFORE, AFTER]
    )
    with pytest.raises(ValueError):
        asyncio.run(execute())
    assert seq.reserved is False
    assert writes == []


def test_missing_initial_epoch_cancels_reservation():
    bad = {"selected_source": "pynmr", "route_epoch": -1}
    execute, seq, writes = run(observations=[bad])
    with pytest.raises(RuntimeError, match="epoch"):
        asyncio.run(execute())
    assert seq.reserved is False
    assert writes == []


def test_same_source_cancels_without_command():
    execute, seq, writes = run(
        requested="pynmr", observations=[BEFORE]
    )
    with pytest.raises(RuntimeError, match="already selected"):
        asyncio.run(execute())
    assert writes == []
    assert seq.reserved is False
