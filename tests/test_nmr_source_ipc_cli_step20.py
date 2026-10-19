"""Offline checks that source-control IPC is opt-in and forwarded unchanged.

AST-only: never starts fitter, IOC, source-control socket, or RF hardware.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

RUN = Path('online_fitter/run.py')
EPICS = Path('online_fitter/epics_run.py')
FLAGS = (
    '--nmr-source-control-path',
    '--nmr-source-control-allowed-uid',
    '--nmr-source-control-allowed-pid',
)


def _build():
    tree = ast.parse(EPICS.read_text())
    func = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                and x.name == '_build_run_args')
    module = ast.Module(body=[func], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {'DEFAULT_HOST': '127.0.0.1'}
    exec(compile(module, str(EPICS), 'exec'), ns)
    return ns['_build_run_args']


def _args(**updates):
    base = dict(port=8777, publish_port=8778, material='d-butanol',
                mode='baseline', eq_buffer=10, verbose=False, replace=False,
                nmr_protected=True, nmr_source='labview', pynmr_port=8779,
                pynmr_ipc_path=None, pynmr_ipc_allowed_uid=None,
                pynmr_ipc_shared_gid=None, nmr_source_control_path=None,
                nmr_source_control_allowed_uid=None,
                nmr_source_control_allowed_pid=None)
    base.update(updates)
    return SimpleNamespace(**base)


def test_default_does_not_enable_source_ipc():
    result = _build()(_args())
    assert all(flag not in result for flag in FLAGS)


def test_legacy_mode_does_not_enable_source_ipc():
    result = _build()(_args(nmr_protected=False, nmr_source=None))
    assert all(flag not in result for flag in FLAGS)


def test_explicit_source_ipc_settings_forwarded_exactly():
    result = _build()(_args(nmr_source_control_path='/tmp/secure/nmr.sock',
                            nmr_source_control_allowed_uid=1001,
                            nmr_source_control_allowed_pid=3456))
    for flag, value in zip(FLAGS, ('/tmp/secure/nmr.sock', '1001', '3456')):
        assert result.count(flag) == 1
        assert result[result.index(flag) + 1] == value


def test_both_parsers_expose_all_three_opt_in_flags():
    for path in (RUN, EPICS):
        content = path.read_text()
        for flag in FLAGS:
            assert content.count('"' + flag + '"') == (2 if path == EPICS else 1)


def test_run_propagates_all_options_to_protected_runtime():
    content = RUN.read_text()
    for param in ('path', 'allowed_uid', 'allowed_pid'):
        name = 'nmr_source_control_' + param
        assert f'{name}=a.{name}' in content


def test_both_parsers_reject_incomplete_credentials():
    for path in (RUN, EPICS):
        content = path.read_text()
        assert 'source-control IPC requires trusted UID' in content
        assert 'source-control IPC requires trusted PID' in content
        assert 'source-control IPC requires absolute socket path' in content
        assert 'source-control IPC requires protected NMR mode' in content
        assert 'source-control and PyNMR event sockets must differ' in content
