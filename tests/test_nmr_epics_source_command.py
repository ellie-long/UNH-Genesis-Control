"""Offline, fail-closed NMR source-command regressions."""

import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path("online_fitter/epics_run.py")


def load_callback():
    tree = ast.parse(SOURCE.read_text())
    owner = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "FitterEpicsInterface"
    )
    method = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_on_nmr_source"
    )

    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {}
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope["_on_nmr_source"]


class Record:
    def __init__(self):
        self.value = None

    def set(self, value, process=True):
        self.value = value


class Runtime:
    def __init__(self, source="pynmr"):
        self.source = source
        self.change_calls = []

    def snapshot(self):
        return {"selected_source": self.source}

    def select_source(self, source):
        self.change_calls.append(source)
        self.source = source


def make_interface(runtime=None):
    state = (
        SimpleNamespace(genesis_nmr_protected_runtime=runtime)
        if runtime is not None else None
    )

    interface = SimpleNamespace(
        holder=SimpleNamespace(state=state),
        pvs={
            "NMR_SOURCE_CMD": Record(),
            "NMR_SOURCE_CHANGE_STATUS": Record(),
            "WARNING": Record(),
        },
        _as_text=lambda value: str(value).strip(),
    )
    return interface


def test_source_change_blocked_without_sequencer_authorization():
    runtime = Runtime("pynmr")
    interface = make_interface(runtime)

    load_callback()(interface, "labview", None)

    assert runtime.source == "pynmr"
    assert runtime.change_calls == []
    assert interface.pvs["NMR_SOURCE_CMD"].value == "pynmr"
    assert "Rejected" in interface.pvs["NMR_SOURCE_CHANGE_STATUS"].value


def test_invalid_source_rejected():
    runtime = Runtime()
    interface = make_interface(runtime)

    load_callback()(interface, "invalid", None)

    assert runtime.change_calls == []
    assert runtime.source == "pynmr"
    assert "invalid" in interface.pvs["NMR_SOURCE_CHANGE_STATUS"].value


def test_unavailable_runtime_rejected():
    interface = make_interface()

    load_callback()(interface, "labview", None)

    assert interface.pvs["NMR_SOURCE_CMD"].value == ""
    assert "unavailable" in interface.pvs["NMR_SOURCE_CHANGE_STATUS"].value


def test_same_source_is_idempotent():
    runtime = Runtime("pynmr")
    interface = make_interface(runtime)

    load_callback()(interface, "pynmr", None)

    assert runtime.change_calls == []
    assert interface.pvs["NMR_SOURCE_CMD"].value == "pynmr"
    assert "unchanged" in interface.pvs["NMR_SOURCE_CHANGE_STATUS"].value
