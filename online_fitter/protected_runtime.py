"""Opt-in protected LabVIEW/PyNMR fitter runtime.

This coordinator reads switch EPICS PVs but never writes to them.
An acquisition-event adapter is not installed yet, so all sweeps
remain blocked by the post-switch barrier.

The two TCP receivers are loopback-only.
"""

import threading

from .dual_source_transport import DualSourceTransport
from .nmr_switch_readback import NMRReadbackPoller
from .source_receiver import SourceAwareProcessor
from .source_state import NMRSourceState


class ProtectedNMRRuntime:

    def __init__(
        self,
        fitter_state,
        *,
        selected_source,
        pv_factory,
        labview_port=8777,
        pynmr_port=8779,
        verbose=False,
        poll_interval_s=0.5,
    ):
        if (
            type(poll_interval_s) not in (int, float)
            or not 0.05 <= poll_interval_s <= 10.0
        ):
            raise ValueError("invalid polling interval")

        self.source_state = NMRSourceState()
        self.source_state.select(selected_source)

        self.poller = NMRReadbackPoller(
            self.source_state, pv_factory
        )

        self.processor = SourceAwareProcessor(
            self.source_state.take_for_sweep
        )

        self.transport = DualSourceTransport(
            fitter_state,
            self.processor,
            labview_port=labview_port,
            pynmr_port=pynmr_port,
            verbose=verbose,
        )

        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread = None

    def select_source(self, source):
        """Change software selection only; NEVER switch hardware."""
        self.source_state.select(source)

    def _poll_loop(self):
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.poller.poll_once()
            except Exception:
                self.source_state.update_route(None, None)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("protected runtime already started")

        # An unavailable EPICS readback fails closed.
        self.poller.poll_once()

        self.transport.start()

        self._stop.clear()

        try:
            thread = threading.Thread(
                target=self._poll_loop,
                name="genesis-nmr-readback",
                daemon=True,
            )
            thread.start()
            self._thread = thread
        except BaseException:
            self._stop.set()
            self.transport.close()
            raise

        return self

    def shutdown(self):
        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        self.transport.close()

    def snapshot(self):
        return self.source_state.snapshot()
