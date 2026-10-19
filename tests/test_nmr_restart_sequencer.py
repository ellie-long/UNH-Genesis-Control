"""Test production sequencer method bodies with fake EPICS and IPC functions."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

from logic_devices.nmr_source_recovery import NMRSourceRecovery

SOURCE = Path('logic_devices/ssrf_sequencer.py')
PYNMR = {'selected_source': 'pynmr', 'route_epoch': 12}
LABVIEW = {'selected_source': 'labview', 'route_epoch': 13}


class PV:
    def __init__(self):
        self.value = None
    def set(self, value, **kwargs):
        self.value = value


def harness(tmp_path, *, state='ready', new_source=LABVIEW, epics=LABVIEW):
    tree = ast.parse(SOURCE.read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Device')
    accepted = {'_nmr_source_activity', '_reconcile_nmr_snapshot',
                '_service_nmr_recovery', '_run_nmr_source_request',
                '_set_nmr_source_status'}
    klass.body = [n for n in klass.body if getattr(n, 'name', None) in accepted]
    scope = {'asyncio': asyncio}
    async def rf(**kwargs):
        return None
    async def snapshot(**kwargs):
        return dict(new_source)
    async def read(**kwargs):
        return dict(epics)
    scope.update(verify_ssrf_rf_off_fresh=rf,
                 request_source_snapshot=snapshot,
                 read_fitter_source_snapshot=read)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[klass], type_ignores=[])),
                 str(SOURCE), 'exec'), scope)
    Device = scope['Device']
    d = Device.__new__(Device)
    d.settings = {'nmr_source_selection_enabled': True}
    d._nmr_recovery_required = True
    d._nmr_recovery_attempted = False
    d._nmr_source_change_reserved = False
    d._nmr_source_request = None
    d._nmr_source_task = None
    d.pvs = {name: PV() for name in (
        'NMR_SOURCE_STATE', 'NMR_SOURCE_REASON', 'NMR_SOURCE_SELECTED_RBV',
        'NMR_SOURCE_EPOCH_RBV',
    )}
    d._set_execution_armed = lambda value: None
    d._set_prepared = lambda value: None
    d._validated_nmr_source_configuration = lambda: dict(
        path='/tmp/mock.sock', uid=os.getuid(), pid=os.getpid(),
        timeout_s=0.1, max_age_s=3.0,
    )
    d._complete_nmr_source_change_reservation = lambda *, confirmed: (
        setattr(d, '_nmr_source_change_reserved', False)
        if confirmed and d._nmr_recovery.ready else
        (_ for _ in ()).throw(RuntimeError('Not confirmed'))
    )
    d._nmr_recovery = NMRSourceRecovery(
        str(tmp_path / 'recovery.json'), fitter_uid=os.getuid(), fitter_pid=os.getpid(),
    )
    d._nmr_recovery.commission_initial(PYNMR)
    if state == 'dispatching':
        d._nmr_recovery.reserve()
        d._nmr_recovery.dispatch('labview', PYNMR)
    else:
        d._nmr_recovery.ready = False
    return d, scope


def test_clean_startup_requires_verified_fitter_status(tmp_path):
    d, _ = harness(tmp_path, new_source=PYNMR, epics=PYNMR)
    assert d._nmr_source_activity()
    asyncio.run(d._service_nmr_recovery())
    assert d.pvs['NMR_SOURCE_STATE'].value == 'RECONCILED'
    assert not d._nmr_source_activity()
    d._nmr_recovery.close()


def test_startup_refuses_epics_ipc_disagreement(tmp_path):
    d, _ = harness(tmp_path, new_source=PYNMR, epics=LABVIEW)
    asyncio.run(d._service_nmr_recovery())
    assert d.pvs['NMR_SOURCE_STATE'].value == 'RECOVERY_REQUIRED'
    assert d._nmr_source_activity()
    d._nmr_recovery.close()


def test_startup_recovers_inflight_only_with_verified_post_epoch(tmp_path):
    d, _ = harness(tmp_path, state='dispatching')
    asyncio.run(d._service_nmr_recovery())
    assert d.pvs['NMR_SOURCE_STATE'].value == 'RECONCILED'
    assert d._nmr_recovery.ready
    d._nmr_recovery.close()


def test_lost_ack_verified_by_live_readbacks_before_release(tmp_path):
    d, ns = harness(tmp_path, state='dispatching')
    d._nmr_source_change_reserved = True
    async def failed_ipc(**kwargs):
        raise TimeoutError('Reply lost')
    ns['execute_nmr_source_change_via_ipc'] = failed_ipc
    asyncio.run(d._run_nmr_source_request('labview'))
    assert d.pvs['NMR_SOURCE_STATE'].value == 'RECONCILED'
    assert d._nmr_recovery.ready and not d._nmr_source_change_reserved
    d._nmr_recovery.close()


def test_lost_ack_with_stale_epics_never_releases(tmp_path):
    d, ns = harness(tmp_path, state='dispatching', epics=PYNMR)
    d._nmr_source_change_reserved = True
    async def failed_ipc(**kwargs):
        raise TimeoutError('Reply lost')
    ns['execute_nmr_source_change_via_ipc'] = failed_ipc
    asyncio.run(d._run_nmr_source_request('labview'))
    assert d.pvs['NMR_SOURCE_STATE'].value == 'FAULT'
    assert not d._nmr_recovery.ready
    assert d._nmr_source_change_reserved
    d._nmr_recovery.close()
