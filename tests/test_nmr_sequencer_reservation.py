"""Offline source-change reservation regressions.

No IOC startup, EPICS connections, or physical RF commands.
"""

import ast
from pathlib import Path
from types import MethodType, SimpleNamespace


SOURCE = Path("logic_devices/ssrf_sequencer.py")


def methods():
    tree = ast.parse(SOURCE.read_text())
    device = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "Device"
    )
    return {
        node.name: node
        for node in device.body
        if isinstance(node, ast.FunctionDef)
    }


def load_other_busy():
    node = methods()["_other_busy"]
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope["_other_busy"]


def idle_device(reserved=False):
    device = SimpleNamespace(
        _nmr_source_change_reserved=reserved,
        _nmr_source_request=None,
        _nmr_source_task=None,
        _start_requested=False,
        _prepare_requested=False,
        _waveform_stage_requested=False,
        _execution_armed=False,
        _execution_task=None,
        _prepare_task=None,
        _waveform_stage_task=None,
    )

    # Exercise the production guard, rather than a simplified mock.
    device._nmr_source_activity = MethodType(
        load_reservation_method("_nmr_source_activity"),
        device,
    )
    return device


def references_reservation(method):
    return any(
        isinstance(node, ast.Attribute)
        and node.attr in (
            "_nmr_source_change_reserved",
            "_nmr_source_activity",
            "_other_busy",
        )
        for node in ast.walk(methods()[method])
    )


def test_idle_sequencer_is_not_busy():
    assert load_other_busy()(idle_device()) is False


def test_source_change_reservation_marks_sequencer_busy():
    assert load_other_busy()(idle_device(reserved=True)) is True


def test_prepare_checks_reservation():
    assert references_reservation("_prepare_command")


def test_arm_and_start_check_reservation():
    assert references_reservation("_execution_command")


def test_manual_commands_use_busy_guard():
    method = methods()["_manual_command"]
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_other_busy"
        for node in ast.walk(method)
    )


def test_waveform_staging_checks_reservation():
    candidates = [
        method for name, method in methods().items()
        if "waveform" in name.lower()
        and "stage" in name.lower()
    ]
    assert any(
        any(
            isinstance(node, ast.Attribute)
            and node.attr in (
            "_nmr_source_change_reserved",
            "_nmr_source_activity",
            "_other_busy",
        )
            for node in ast.walk(method)
        )
        for method in candidates
    )




def test_queued_source_request_marks_sequencer_busy():
    device = idle_device()
    device._nmr_source_request = "labview"

    assert load_other_busy()(device) is True
    assert load_other_busy()(device, include_nmr=False) is False


def test_active_source_task_marks_sequencer_busy():
    device = idle_device()
    device._nmr_source_task = SimpleNamespace(done=lambda: False)

    assert load_other_busy()(device) is True
    assert load_other_busy()(device, include_nmr=False) is False


def test_completed_source_task_no_longer_marks_busy():
    device = idle_device()
    device._nmr_source_task = SimpleNamespace(done=lambda: True)

    assert load_other_busy()(device) is False


def test_excluding_nmr_does_not_exclude_other_operations():
    device = idle_device(reserved=True)
    device._start_requested = True

    assert load_other_busy()(device, include_nmr=False) is True

# Reservation lifecycle tests: no real sequencer or EPICS IOC.
import asyncio


def load_reservation_method(name):
    tree = ast.parse(SOURCE.read_text())
    device = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "Device"
    )
    method = next(
        n for n in device.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
    )
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope[name]


class FakeSafetyIO:
    execution_enabled = True

    def __init__(self, rf=0, error=None):
        self.rf = rf
        self.error = error

    async def read_safety(self, pv):
        assert pv == "UNH:GEN:SSRF:GEN:RF_RBV"
        if self.error:
            raise self.error
        return self.rf


def reservation_device(rf=0, error=None, busy=False):
    device = idle_device()
    device._execution_io = FakeSafetyIO(rf, error)
    device.settings = {"nmr_source_selection_enabled": True}
    device._manual = SimpleNamespace(
        state="DISARMED", fault=""
    )
    device._manual_busy = lambda: busy
    device._other_busy = lambda *, include_nmr=True: (
        busy or load_other_busy()(
            device,
            include_nmr=include_nmr,
        )
    )
    return device


def acquire(device):
    return asyncio.run(
        load_reservation_method(
            "_acquire_nmr_source_change_reservation"
        )(device)
    )


def test_reservation_requires_rf_off():
    device = reservation_device(rf=1)
    try:
        acquire(device)
    except RuntimeError:
        pass
    else:
        raise AssertionError("RF ON was accepted")
    assert device._nmr_source_change_reserved is False


def test_reservation_rejects_unavailable_rf_readback():
    device = reservation_device(
        error=RuntimeError("Safety readback unavailable")
    )
    try:
        acquire(device)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Unavailable RF readback accepted")
    assert device._nmr_source_change_reserved is False


def test_reservation_rejects_busy_sequencer():
    device = reservation_device(busy=True)
    try:
        acquire(device)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Busy sequencer accepted")
    assert device._nmr_source_change_reserved is False


def test_reservation_acquires_only_after_rf_off():
    device = reservation_device()
    acquire(device)
    assert device._nmr_source_change_reserved is True


def test_unconfirmed_completion_keeps_reservation():
    device = reservation_device()
    acquire(device)
    complete = load_reservation_method(
        "_complete_nmr_source_change_reservation"
    )
    try:
        complete(device, confirmed=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Unconfirmed completion accepted")
    assert device._nmr_source_change_reserved is True


def test_confirmed_completion_releases_reservation():
    device = reservation_device()
    acquire(device)
    complete = load_reservation_method(
        "_complete_nmr_source_change_reservation"
    )
    complete(device, confirmed=True)
    assert device._nmr_source_change_reserved is False


def test_software_selection_disabled_by_default():
    device = reservation_device()
    device.settings = {}

    try:
        acquire(device)
    except RuntimeError as exc:
        assert "not authorized" in str(exc)
    else:
        raise AssertionError("Default-disabled selection was accepted")

    assert device._nmr_source_change_reserved is False


def test_software_selection_does_not_require_rf_execution():
    device = reservation_device()
    device._execution_io.execution_enabled = False

    acquire(device)

    assert device._nmr_source_change_reserved is True
    assert device._execution_io.execution_enabled is False


def test_string_authorization_does_not_enable_selection():
    device = reservation_device()
    device.settings["nmr_source_selection_enabled"] = "true"

    try:
        acquire(device)
    except RuntimeError as exc:
        assert "not authorized" in str(exc)
    else:
        raise AssertionError("Non-boolean authorization accepted")

    assert device._nmr_source_change_reserved is False
