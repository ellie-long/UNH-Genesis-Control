"""Offline concurrency tests for NMR software transitions."""

import threading
import time
from types import SimpleNamespace

from online_fitter.source_state import NMRSourceState
from online_fitter.protected_runtime import ProtectedNMRRuntime


def test_route_update_waits_for_active_fit_guard():
    state = NMRSourceState()
    attempted = threading.Event()
    finished = threading.Event()

    def update():
        attempted.set()
        state.update_route(1, 3)
        finished.set()

    worker = threading.Thread(target=update, daemon=True)

    with state.fit_guard():
        worker.start()
        assert attempted.wait(2)
        assert not finished.wait(0.08)

    worker.join(timeout=2)

    assert finished.is_set()
    assert state.snapshot()["nmr_mask"] == 3


class FakePV:
    connected = True

    def __init__(self, name):
        self.name = name

    def get(self, **kwargs):
        assert kwargs["use_monitor"] is False
        return 1 if self.name.endswith(":COMM") else 0

    def put(self, *args, **kwargs):
        raise AssertionError("Hardware/EPICS write attempted")


def test_protected_fit_finishes_before_source_selection():
    def pv_factory(name, **kwargs):
        assert kwargs == {"auto_monitor": False}
        return FakePV(name)

    runtime = ProtectedNMRRuntime(
        SimpleNamespace(n_received=0),
        selected_source="labview",
        pv_factory=pv_factory,
        labview_port=0,
        pynmr_port=0,
    )

    source_state = runtime.source_state
    source_state.update_stream("labview", True)
    source_state.update_route(1, 0)

    started = time.monotonic()
    time.sleep(0.002)
    completed = time.monotonic()

    message = {
        "type": "sweep",
        "centfreq_mhz": 32.7,
        "span_mhz": 0.4,
        "npts": 3,
        "raw": [1.0, 2.0, 3.0],
        "baseline_sub": [0.1, 0.2, 0.3],
    }

    assert runtime.packet_binding.attest_finished_event(
        "labview", message, started, completed
    )

    fit_entered = threading.Event()
    release_fit = threading.Event()
    selection_attempted = threading.Event()
    selection_finished = threading.Event()

    results = []

    def controlled_fit(state, packet):
        fit_entered.set()
        assert release_fit.wait(3)
        return {"ok": True}

    runtime._inner_processor.fit = controlled_fit

    def process():
        try:
            results.append(
                runtime.processor(
                    None, message, "labview"
                )
            )
        except BaseException as exc:
            results.append(exc)

    def select():
        selection_attempted.set()
        runtime.select_source("pynmr")
        selection_finished.set()

    fit_thread = threading.Thread(
        target=process, daemon=True
    )
    select_thread = threading.Thread(
        target=select, daemon=True
    )

    try:
        fit_thread.start()
        assert fit_entered.wait(2), (
            "Attested spectrum did not reach the fit callback"
        )

        select_thread.start()
        assert selection_attempted.wait(2)

        # Selection must wait until the admitted fit completes.
        assert not selection_finished.wait(0.08)

    finally:
        release_fit.set()
        fit_thread.join(timeout=3)
        select_thread.join(timeout=3)

    assert results == [{"ok": True}]
    assert selection_finished.is_set()
    assert source_state.snapshot()["selected_source"] == "pynmr"
