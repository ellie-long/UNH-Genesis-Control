"""Offline Genesis NMR EPICS integration regressions.

Do not import/start the real soft IOC.
"""

import ast
from pathlib import Path
from types import SimpleNamespace


EPICS = Path("online_fitter/epics_run.py")
RUN = Path("online_fitter/run.py")


def function_from_source(path, name, class_name=None):
    tree = ast.parse(path.read_text())

    if class_name is None:
        nodes = tree.body
    else:
        owner = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == class_name
        )
        nodes = owner.body

    method = next(
        node for node in nodes
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )

    scope = {"DEFAULT_HOST": "127.0.0.1"}
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), scope)
    return scope[name]


def args(protected=False):
    return SimpleNamespace(
        port=8777,
        publish_port=8778,
        material="d-butanol",
        mode="baseline",
        eq_buffer=10,
        verbose=False,
        replace=False,
        nmr_protected=protected,
        nmr_source="pynmr" if protected else None,
        pynmr_port=8779,
    )


def test_legacy_fitter_arguments_unchanged():
    build = function_from_source(
        EPICS, "_build_run_args"
    )
    command = build(args(False))

    assert "--nmr-protected" not in command
    assert "--nmr-source" not in command
    assert "--pynmr-port" not in command
    assert "--no-gui" in command


def test_protected_fitter_arguments_forwarded():
    build = function_from_source(
        EPICS, "_build_run_args"
    )
    command = build(args(True))

    assert "--nmr-protected" in command
    assert command[
        command.index("--nmr-source") + 1
    ] == "pynmr"
    assert command[
        command.index("--pynmr-port") + 1
    ] == "8779"


class Record:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


def make_interface(runtime=None):
    names = (
        "NMR_PROTECTED",
        "NMR_SOURCE_RBV",
        "NMR_SOURCE_EPOCH_RBV",
        "NMR_PHYSICAL_RBV",
        "NMR_MASK_RBV",
        "NMR_ROUTE_OK",
        "NMR_STREAM_OK",
        "NMR_EVENT_READY",
        "NMR_FIT_ELIGIBLE",
        "NMR_STATUS",
    )

    holder = SimpleNamespace(
        state=(
            SimpleNamespace(
                genesis_nmr_protected_runtime=runtime
            ) if runtime is not None else None
        )
    )

    return SimpleNamespace(
        holder=holder,
        pvs={name: Record() for name in names},
    )


def sync(interface):
    method = function_from_source(
        EPICS,
        "_sync_nmr_source_readbacks",
        class_name="FitterEpicsInterface",
    )
    method(interface)


def test_legacy_readbacks_remain_disabled():
    interface = make_interface()
    sync(interface)

    assert interface.pvs["NMR_PROTECTED"].value == 0
    assert interface.pvs["NMR_ROUTE_OK"].value == 0
    assert interface.pvs["NMR_FIT_ELIGIBLE"].value == 0


def test_protected_source_status_is_observable():
    runtime = SimpleNamespace(
        snapshot=lambda: {
            "selected_source": "pynmr",
            "route_epoch": 7,
            "nmr_mask": 0,
            "nmr_comm": 1,
            "route_age_s": 0.2,
            "stream_connected": True,
            "post_switch_ready": False,
        }
    )

    interface = make_interface(runtime)
    sync(interface)

    assert interface.pvs["NMR_PROTECTED"].value == 1
    assert interface.pvs["NMR_SOURCE_RBV"].value == "pynmr"
    assert interface.pvs["NMR_SOURCE_EPOCH_RBV"].value == 7
    assert interface.pvs["NMR_PHYSICAL_RBV"].value == "CONNECTED"
    assert interface.pvs["NMR_ROUTE_OK"].value == 1
    assert interface.pvs["NMR_STREAM_OK"].value == 1
    assert interface.pvs["NMR_FIT_ELIGIBLE"].value == 0


def test_stale_readback_fails_closed():
    runtime = SimpleNamespace(
        snapshot=lambda: {
            "selected_source": "labview",
            "nmr_mask": 0,
            "nmr_comm": 1,
            "route_age_s": 20.0,
            "stream_connected": True,
            "post_switch_ready": True,
        }
    )

    interface = make_interface(runtime)
    sync(interface)

    assert interface.pvs["NMR_ROUTE_OK"].value == 0
    assert interface.pvs["NMR_FIT_ELIGIBLE"].value == 0


def test_runtime_snapshot_exception_fails_closed():
    def broken():
        raise RuntimeError("Unavailable")

    interface = make_interface(
        SimpleNamespace(snapshot=broken)
    )
    sync(interface)

    assert interface.pvs["NMR_ROUTE_OK"].value == 0
    assert interface.pvs["NMR_FIT_ELIGIBLE"].value == 0


def test_runtime_is_registered_only_in_protected_path():
    text = RUN.read_text()
    assert (
        "state.genesis_nmr_protected_runtime = srv"
        in text
    )
    assert "--nmr-protected" in text


def test_status_pvs_are_read_only():
    source = EPICS.read_text()

    for name in (
        "NMR_PROTECTED",
        "NMR_SOURCE_RBV",
        "NMR_PHYSICAL_RBV",
        "NMR_MASK_RBV",
        "NMR_ROUTE_OK",
        "NMR_STREAM_OK",
        "NMR_EVENT_READY",
        "NMR_FIT_ELIGIBLE",
        "NMR_STATUS",
    ):
        assert f'"{name}"' in source

    assert 'p["NMR_FIT_ELIGIBLE"].set(0)' in source
