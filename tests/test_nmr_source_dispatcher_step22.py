"""Offline sequencer callback and async dispatch tests (no EPICS/hardware)."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakePV:
    def __init__(self, value=None):
        self.value = value
    def set(self, value, **kwargs):
        self.value = value
    def get(self):
        return self.value


def load_sequencer_methods(monkeypatch):
    tree = ast.parse(Path('logic_devices/ssrf_sequencer.py').read_text())
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Device')
    methods = {
        '_nmr_source_activity', '_nmr_source_command', '_service_nmr_source_request',
        '_run_nmr_source_request', '_set_nmr_source_status',
        '_validated_nmr_source_configuration',
    }
    klass.body = [node for node in klass.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods]
    klass.decorator_list = []
    namespace = {'asyncio': asyncio}
    namespace['validate_ipc_configuration'] = lambda **kwargs: None
    namespace['read_fitter_source_snapshot'] = None
    namespace['verify_ssrf_rf_off_fresh'] = None
    namespace['execute_nmr_source_change_via_ipc'] = None
    exec(compile(ast.fix_missing_locations(ast.Module(body=[klass], type_ignores=[])), '<offline Device>', 'exec'), namespace)
    return namespace, namespace['Device']


def device():
    ns, Device = load_sequencer_methods(None)
    d = Device.__new__(Device)
    d.settings = {
        'nmr_source_selection_enabled': True,
        'nmr_source_control_path': '/run/genesis/nmr-control.sock',
        'nmr_source_control_fitter_uid': 1000,
        'nmr_source_control_fitter_pid': 4321,
    }
    d._nmr_source_change_reserved = False
    d._nmr_source_task = None
    d._nmr_source_request = None
    d._manual = SimpleNamespace(state='DISARMED', fault='')
    d._manual_busy = lambda: False
    d._other_busy = lambda: False
    d._reset_momentary_command = lambda name: None
    d.pvs = {name: FakePV() for name in (
        'NMR_SOURCE_STATE', 'NMR_SOURCE_REASON',
        'NMR_SOURCE_SELECTED_RBV', 'NMR_SOURCE_EPOCH_RBV',
    )}
    return ns, d


def test_callback_latches_without_performing_io():
    _, d = device()
    d._nmr_source_command('LABVIEW', 'pv')
    assert d._nmr_source_request == 'labview'
    assert d._nmr_source_task is None
    assert d.pvs['NMR_SOURCE_STATE'].value == 'PENDING'
    assert d._nmr_source_activity()


def test_reject_invalid_and_disabled_requests():
    _, d = device()
    d._nmr_source_command('invalid', 'pv')
    assert d._nmr_source_request is None
    d.settings['nmr_source_selection_enabled'] = False
    d._nmr_source_command('PYNMR', 'pv')
    assert d._nmr_source_request is None
    assert d.pvs['NMR_SOURCE_STATE'].value == 'DISABLED'


def test_duplicate_request_rejected_while_queued():
    _, d = device()
    d._nmr_source_command('LABVIEW', 'pv')
    d._nmr_source_command('PYNMR', 'pv')
    assert d._nmr_source_request == 'labview'


def test_manual_rf_busy_rejects_request():
    _, d = device()
    d._manual_busy = lambda: True
    d._nmr_source_command('LABVIEW', 'pv')
    assert d._nmr_source_request is None


def test_only_dispatcher_creates_async_task():
    ns, d = device()
    result = {'selected_source': 'labview', 'route_epoch': 22}
    async def read(**kwargs):
        return result
    async def rf(**kwargs):
        return None
    async def tx(**kwargs):
        assert kwargs['requested'] == 'labview'
        assert kwargs['read_rf_off'] is not None
        return await kwargs['read_fitter']()
    ns['read_fitter_source_snapshot'] = read
    ns['verify_ssrf_rf_off_fresh'] = rf
    ns['execute_nmr_source_change_via_ipc'] = tx

    async def go():
        d._nmr_source_command('LABVIEW', 'pv')
        d._service_nmr_source_request()
        assert d._nmr_source_task is not None
        assert d._nmr_source_activity()
        await d._nmr_source_task
    asyncio.run(go())
    assert d.pvs['NMR_SOURCE_STATE'].value == 'CONFIRMED'
    assert d.pvs['NMR_SOURCE_EPOCH_RBV'].value == 22
    assert not d._nmr_source_activity()


def test_uncertain_ipc_result_is_fault_without_retry():
    ns, d = device()
    d._nmr_source_change_reserved = False
    calls = []
    async def rf(**kwargs):
        return None
    async def tx(**kwargs):
        calls.append(kwargs['requested'])
        d._nmr_source_change_reserved = True
        raise TimeoutError('uncertain IPC result')
    ns['verify_ssrf_rf_off_fresh'] = rf
    ns['execute_nmr_source_change_via_ipc'] = tx

    async def go():
        d._nmr_source_command('PYNMR', 'pv')
        d._service_nmr_source_request()
        await d._nmr_source_task
    asyncio.run(go())
    assert calls == ['pynmr']
    assert d._nmr_source_change_reserved
    assert d.pvs['NMR_SOURCE_STATE'].value == 'FAULT'
