"""Simulated Step 21 IPC-to-reservation integration, without EPICS or RF."""
import asyncio
from unittest.mock import patch

import pytest

from logic_devices.nmr_source_ipc_transaction import (
    RF_OUTPUT_RBV, execute_nmr_source_change_via_ipc,
)


BEFORE = {'selected_source': 'pynmr', 'route_epoch': 20}
AFTER = {'selected_source': 'labview', 'route_epoch': 21}


class FakeSafetyIO:
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = []

    async def read_safety(self, pv):
        self.calls.append(pv)
        if not self.readings:
            raise RuntimeError('No valid RF readback')
        return self.readings.pop(0)


class FakeSequencer:
    def __init__(self, rf=(0, 0, 0)):
        self._execution_io = FakeSafetyIO(rf)
        self.reserved = False
        self.releases = 0

    async def _acquire_nmr_source_change_reservation(self):
        if self.reserved:
            raise RuntimeError('Already reserved')
        self.reserved = True
        try:
            if await self._execution_io.read_safety(RF_OUTPUT_RBV) != 0:
                raise RuntimeError('RF not OFF at acquire')
        except BaseException:
            self.reserved = False
            raise

    def _cancel_nmr_source_change_reservation(self):
        assert self.reserved
        self.reserved = False

    def _complete_nmr_source_change_reservation(self, *, confirmed):
        assert confirmed is True and self.reserved
        self.reserved = False
        self.releases += 1


def execute_case(*, rf=(0, 0, 0), readbacks=None, ipc_ack=None,
                 ipc_error=None, requested='labview', **kwargs):
    sequencer = FakeSequencer(rf=rf)
    queued = list(readbacks if readbacks is not None else (BEFORE, AFTER))
    sent = []

    async def read():
        if not queued:
            raise RuntimeError('No more FIT readbacks')
        return queued.pop(0)

    async def ipc(**args):
        sent.append(args)
        if ipc_error:
            raise ipc_error
        return ipc_ack if ipc_ack is not None else AFTER

    async def run():
        with patch('logic_devices.nmr_source_ipc_transaction.request_source_change', ipc):
            return await execute_nmr_source_change_via_ipc(
                requested=requested,
                sequencer=sequencer,
                read_fitter=read,
                path='/run/user/1000/genesis-nmr-source.sock',
                allowed_fitter_uid=1000,
                allowed_fitter_pid=4000,
                poll_attempts=2, poll_interval_s=0,
                **kwargs,
            )

    return run, sequencer, sent


def test_confirmed_change_rechecks_rf_and_releases():
    run, sequencer, sent = execute_case()
    assert asyncio.run(run()) == AFTER
    assert sequencer.releases == 1
    assert sequencer.reserved is False
    assert sequencer._execution_io.calls == [RF_OUTPUT_RBV] * 3
    assert sent[0]['allowed_fitter_pid'] == 4000
    assert sent[0]['allowed_fitter_uid'] == 1000


def test_uncertain_ipc_write_retains_reservation():
    run, sequencer, sent = execute_case(ipc_error=TimeoutError('Unknown result'))
    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert len(sent) == 1
    assert sequencer.reserved
    assert sequencer.releases == 0


def test_rf_on_before_command_rejects_and_retains():
    run, sequencer, sent = execute_case(rf=(0, 1))
    with pytest.raises(RuntimeError, match='RF OFF'):
        asyncio.run(run())
    assert sent == []
    assert sequencer.reserved


def test_rf_on_after_ack_prevents_release():
    run, sequencer, sent = execute_case(rf=(0, 0, 1))
    with pytest.raises(RuntimeError, match='RF OFF'):
        asyncio.run(run())
    assert len(sent) == 1
    assert sequencer.reserved
    assert sequencer.releases == 0


def test_unknown_rf_readback_never_releases():
    run, sequencer, sent = execute_case(rf=(0, 0))
    with pytest.raises(RuntimeError, match='No valid RF readback'):
        asyncio.run(run())
    assert len(sent) == 1
    assert sequencer.reserved


def test_stale_fitter_epoch_not_accepted():
    stale = {'selected_source': 'labview', 'route_epoch': 21}
    run, sequencer, sent = execute_case(
        ipc_ack={'selected_source': 'labview', 'route_epoch': 22},
        readbacks=[BEFORE, stale, stale],
        rf=(0, 0),
    )
    with pytest.raises(TimeoutError, match='unconfirmed'):
        asyncio.run(run())
    assert len(sent) == 1
    assert sequencer.reserved


def test_invalid_ipc_ack_retains_reservation():
    run, sequencer, sent = execute_case(ipc_ack={
        'selected_source': 'pynmr', 'route_epoch': 21
    })
    with pytest.raises(RuntimeError, match='Unverified'):
        asyncio.run(run())
    assert sequencer.reserved
    assert len(sent) == 1


def test_already_selected_pre_dispatch_cancels_reservation():
    run, sequencer, sent = execute_case(
        requested='pynmr', readbacks=[BEFORE], rf=(0,)
    )
    with pytest.raises(RuntimeError, match='already selected'):
        asyncio.run(run())
    assert sequencer.reserved is False
    assert sent == []


def test_bad_ipc_path_rejected_before_reservation():
    seq = FakeSequencer()
    async def read(): return BEFORE
    with pytest.raises(ValueError, match='Absolute'):
        asyncio.run(execute_nmr_source_change_via_ipc(
            requested='labview', sequencer=seq, read_fitter=read,
            path='relative.sock', allowed_fitter_uid=1000,
            allowed_fitter_pid=4000,
        ))
    assert not seq.reserved


def test_noop_false_epoch_does_not_confirm():
    run, seq, sent = execute_case(
        readbacks=[BEFORE, {'selected_source': 'labview', 'route_epoch': True},
                   {'selected_source': 'labview', 'route_epoch': True}],
        rf=(0, 0),
    )
    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert seq.reserved
    assert len(sent) == 1
