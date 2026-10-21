"""Restart recovery tests: temporary durable state, no EPICS or devices."""
import asyncio
import os
from pathlib import Path

import pytest

from logic_devices.nmr_source_recovery import NMRSourceRecovery
from logic_devices.nmr_source_ipc_client import request_source_snapshot
from online_fitter.nmr_source_control_ipc import NMRSourceControlIPC

PYNMR = {'selected_source': 'pynmr', 'route_epoch': 12}
LABVIEW = {'selected_source': 'labview', 'route_epoch': 13}


def journal(tmp_path, name='nmr.json'):
    tmp_path.chmod(0o700)
    obj = NMRSourceRecovery(str(tmp_path / name), fitter_uid=os.getuid(),
                            fitter_pid=os.getpid())
    return obj


def restarted(old, tmp_path):
    old.close()
    return journal(tmp_path)


def test_initial_commissioning_explicit_and_durable(tmp_path):
    one = journal(tmp_path)
    assert not one.ready and one.record is None
    with pytest.raises(RuntimeError, match='Initial'):
        one.reconcile(PYNMR)
    one.commission_initial(PYNMR)
    two = restarted(one, tmp_path)
    assert not two.ready  # Startup must reconcile before authorizing.
    assert two.reconcile(PYNMR) == PYNMR
    assert two.ready
    two.close()


def test_pre_dispatch_crash_recovered_only_if_unchanged(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    two = restarted(one, tmp_path)
    assert two.record['state'] == 'reserved'
    assert not two.ready
    with pytest.raises(RuntimeError, match='baseline changed'):
        two.reconcile(LABVIEW)
    assert not two.ready
    assert two.reconcile(PYNMR) == PYNMR
    two.close()


def test_dispatch_crash_recovers_only_fresh_new_epoch(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    one.dispatch('labview', PYNMR)
    two = restarted(one, tmp_path)
    assert not two.ready
    with pytest.raises(RuntimeError, match='no verified acknowledgment'):
        two.reconcile(PYNMR)  # Lost reply: no proof transaction succeeded.
    with pytest.raises(RuntimeError, match='no verified acknowledgment'):
        two.reconcile({'selected_source': 'labview', 'route_epoch': 12})
    assert not two.ready
    assert two.reconcile(LABVIEW) == LABVIEW
    assert two.ready and two.record['state'] == 'ready'
    two.close()


def test_unknown_result_is_persistently_held(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    one.dispatch('labview', PYNMR)
    two = restarted(one, tmp_path)
    with pytest.raises(RuntimeError):
        two.reconcile({'selected_source': 'pynmr', 'route_epoch': 16})
    three = restarted(two, tmp_path)
    assert three.record['state'] == 'dispatching'
    assert not three.ready
    three.close()


def test_fitter_restart_identity_blocks_old_ack(tmp_path, monkeypatch):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    one.dispatch('labview', PYNMR)
    two = restarted(one, tmp_path)
    monkeypatch.setattr(two, 'process_identity', lambda: 'new-fitter-instance')
    with pytest.raises(RuntimeError, match='identity changed'):
        two.reconcile(LABVIEW)
    assert not two.ready
    with pytest.raises(PermissionError):
        two.operator_rebaseline(LABVIEW, expected_identity='other-instance')
    two.operator_rebaseline(LABVIEW, expected_identity='new-fitter-instance')
    assert two.record['previous_state'] == 'dispatching'
    assert two.ready
    two.close()


def test_no_release_from_stale_epoch_or_duplicate_source(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    with pytest.raises(RuntimeError, match='baseline changed'):
        one.dispatch('labview', {'selected_source': 'pynmr', 'route_epoch': 11})
    assert one.record['state'] == 'reserved'
    one.dispatch('labview', PYNMR)
    with pytest.raises(RuntimeError, match='no verified acknowledgment'):
        one.confirm({'selected_source': 'labview', 'route_epoch': 12})
    assert not one.ready and one.record['state'] == 'dispatching'
    one.close()


def test_stale_journal_does_not_silently_reinitialize(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    two = restarted(one, tmp_path)
    with pytest.raises(RuntimeError, match='history'):
        two.commission_initial(LABVIEW)
    with pytest.raises(RuntimeError, match='regressed'):
        two.reconcile({'selected_source': 'pynmr', 'route_epoch': 11})
    assert not two.ready
    two.close()


def test_recovery_lock_enforces_single_owner(tmp_path):
    one = journal(tmp_path)
    with pytest.raises(BlockingIOError):
        journal(tmp_path)
    one.close()
    two = journal(tmp_path)
    two.close()


def test_corrupt_record_fails_closed(tmp_path):
    one = journal(tmp_path)
    one.close()
    (tmp_path / 'nmr.json').write_text('{bad json}')
    (tmp_path / 'nmr.json').chmod(0o600)
    with pytest.raises(ValueError):
        journal(tmp_path)


def test_authenticated_snapshot_is_read_only_and_process_pinned(tmp_path):
    async def go():
        sock = str(tmp_path / 'control.sock')
        state = {'selected_source': 'pynmr', 'route_epoch': 8}
        commands = []
        server = NMRSourceControlIPC(
            sock, allowed_uid=os.getuid(), allowed_pid=os.getpid(),
            snapshot=lambda: state.copy(), select_source=lambda source: commands.append(source),
        )
        await server.start()
        try:
            received = await request_source_snapshot(
                path=sock, allowed_fitter_uid=os.getuid(),
                allowed_fitter_pid=os.getpid(),
            )
            assert received == state and commands == []
            with pytest.raises(PermissionError):
                await request_source_snapshot(
                    path=sock, allowed_fitter_uid=os.getuid(),
                    allowed_fitter_pid=os.getpid() + 1000,
                )
        finally:
            await server.close()
    tmp_path.chmod(0o700)
    asyncio.run(go())


def test_manual_override_cannot_erase_live_uncertain_transaction(tmp_path):
    one = journal(tmp_path)
    one.commission_initial(PYNMR)
    one.reserve()
    one.dispatch('labview', PYNMR)
    with pytest.raises(RuntimeError, match='retired fitter'):
        one.operator_rebaseline(LABVIEW, expected_identity=one.process_identity())
    assert one.record['state'] == 'dispatching' and not one.ready
    one.close()
