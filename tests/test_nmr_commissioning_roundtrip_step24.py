"""Offline regressions against the production commissioning methods.

AST extracts only two real ProtectedNMRRuntime methods. All collaborators
are simulation objects; no EPICS, TCP, IPC sockets, or RF hardware.
"""

import ast
from contextlib import nullcontext
from pathlib import Path
from types import MethodType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'online_fitter' / 'protected_runtime.py'


def load_methods():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == 'ProtectedNMRRuntime')
    wanted = {'select_source', '_update_commissioning_arm'}
    methods = [n for n in klass.body if isinstance(n, ast.FunctionDef)
               and n.name in wanted]
    assert {n.name for n in methods} == wanted, 'Protected runtime API changed'
    scope = {}
    module = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
    exec(compile(module, str(SOURCE), 'exec'), scope)
    return {name: scope[name] for name in wanted}


class SourceState:
    def __init__(self, source='pynmr', epoch=12):
        self.source = source
        self.epoch = epoch
        self.route_ok = True
        self.guard_entries = 0

    def fit_guard(self):
        self.guard_entries += 1
        return nullcontext()

    def snapshot(self):
        return {'selected_source': self.source, 'route_epoch': self.epoch,
                'route_ok': self.route_ok}

    def select(self, source):
        assert source in ('pynmr', 'labview')
        if source != self.source:
            self.source = source
            self.epoch += 1


class Adapter:
    def __init__(self):
        self.invalidations = 0
        self.arm_calls = 0
        self.arm_allowed = True

    def _route_ready(self, state):
        return state['selected_source'] == 'pynmr' and state['route_ok']

    def invalidate(self):
        self.invalidations += 1

    def arm(self):
        self.arm_calls += 1
        return self.arm_allowed


class Producer:
    def __init__(self):
        self.valid = True

    def matches_process(self):
        return self.valid


class Runtime:
    def __init__(self):
        self.source_state = SourceState()
        self.event_adapter = Adapter()
        self.producer_session = Producer()
        self._commissioning_armed = False
        self._commissioning_invalid = False
        self._commissioning_route_epoch = None
        for name, fn in load_methods().items():
            setattr(self, name, MethodType(fn, self))


def test_roundtrip_invalidates_old_authorization_and_rearms_with_new_epoch():
    rt = Runtime()
    rt._update_commissioning_arm()
    assert rt._commissioning_armed is True
    assert rt._commissioning_route_epoch == 12
    assert rt.event_adapter.arm_calls == 1

    rt.select_source('labview')
    assert rt.source_state.source == 'labview'
    assert rt.source_state.epoch == 13
    assert rt._commissioning_armed is False
    assert rt._commissioning_route_epoch is None
    assert rt.event_adapter.invalidations == 1

    rt._update_commissioning_arm()
    assert rt._commissioning_armed is False
    assert rt.event_adapter.arm_calls == 1
    assert rt._commissioning_invalid is False

    rt.select_source('pynmr')
    assert rt.source_state.epoch == 14
    assert rt.event_adapter.invalidations == 2
    assert rt._commissioning_armed is False

    rt._update_commissioning_arm()
    assert rt._commissioning_armed is True
    assert rt._commissioning_route_epoch == 14
    assert rt.event_adapter.arm_calls == 2
    assert rt._commissioning_invalid is False


def test_preexisting_authentication_failure_survives_roundtrip():
    rt = Runtime()
    rt._commissioning_invalid = True
    rt.select_source('labview')
    rt.select_source('pynmr')
    rt._update_commissioning_arm()
    assert rt._commissioning_invalid is True
    assert rt._commissioning_armed is False
    assert rt.event_adapter.arm_calls == 0


def test_lost_producer_identity_latches_failure_across_selection():
    rt = Runtime()
    rt.producer_session.valid = False
    rt._update_commissioning_arm()
    assert rt._commissioning_invalid is True
    assert rt._commissioning_armed is False
    assert rt.event_adapter.invalidations >= 1
    rt.producer_session.valid = True
    rt.select_source('labview')
    rt.select_source('pynmr')
    rt._update_commissioning_arm()
    assert rt._commissioning_invalid is True
    assert rt._commissioning_armed is False


def test_uncommanded_epoch_change_after_arm_latches_failure():
    rt = Runtime()
    rt._update_commissioning_arm()
    assert rt._commissioning_armed
    rt.source_state.epoch += 1  # simulated unsanctioned route transition
    rt._update_commissioning_arm()
    assert rt._commissioning_invalid is True
    assert rt._commissioning_armed is False
    assert rt.event_adapter.invalidations >= 1


def test_lost_route_after_arm_latches_failure():
    rt = Runtime()
    rt._update_commissioning_arm()
    rt.source_state.route_ok = False
    rt._update_commissioning_arm()
    assert rt._commissioning_invalid is True
    assert rt._commissioning_armed is False


def test_same_source_noop_preserves_epoch_and_does_not_clear_failure():
    rt = Runtime()
    rt._commissioning_invalid = True
    rt.select_source('pynmr')
    assert rt.source_state.epoch == 12
    assert rt.event_adapter.invalidations == 0
    assert rt._commissioning_invalid is True


def test_invalid_source_rejected_before_mutation():
    rt = Runtime()
    with pytest.raises(ValueError):
        rt.select_source('invalid')
    assert rt.source_state.source == 'pynmr'
    assert rt.source_state.epoch == 12
    assert rt.event_adapter.invalidations == 0


def test_new_epoch_without_ready_route_cannot_rearm():
    rt = Runtime()
    rt._update_commissioning_arm()
    rt.select_source('labview')
    rt.select_source('pynmr')
    rt.source_state.route_ok = False
    rt._update_commissioning_arm()
    assert rt._commissioning_armed is False
    assert rt._commissioning_route_epoch is None
    assert rt.event_adapter.arm_calls == 1


def test_failed_adapter_arm_is_not_treated_as_commissioned():
    rt = Runtime()
    rt.event_adapter.arm_allowed = False
    rt._update_commissioning_arm()
    assert rt._commissioning_armed is False
    assert rt._commissioning_route_epoch is None
