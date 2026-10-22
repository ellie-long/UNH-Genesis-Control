"""Opt-in protected LabVIEW/PyNMR fitter runtime.

This coordinator reads switch EPICS PVs but never writes to them.
An acquisition-event adapter is not installed yet, so all sweeps
remain blocked by the post-switch barrier.

The two TCP receivers are loopback-only.
"""

import threading
from .pynmr_producer_session import PinnedPyNMRProducer

from .dual_source_transport import DualSourceTransport
from .nmr_switch_readback import NMRReadbackPoller
from .source_receiver import SourceAwareProcessor
from .packet_binding import PacketBoundAdmission
from .source_state import NMRSourceState
from .pynmr_event_adapter import PyNMRCompletedEventAdapter
from .pynmr_local_ipc import PyNMRLocalIPC


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
        pynmr_ipc_path=None,
        pynmr_ipc_allowed_uids=None,
        pynmr_ipc_shared_gid=None,
        nmr_source_control_path=None,
        nmr_source_control_allowed_uid=None,
        nmr_source_control_allowed_pid=None,
        pynmr_only=False,
    ):
        if (
            type(poll_interval_s) not in (int, float)
            or not 0.05 <= poll_interval_s <= 10.0
        ):
            raise ValueError("invalid polling interval")

        if type(pynmr_only) is not bool:
            raise TypeError("pynmr_only must be bool")
        if pynmr_only and selected_source != "pynmr":
            raise ValueError("PyNMR-only runtime requires PyNMR source")
        self.pynmr_only = pynmr_only

        self.source_state = NMRSourceState()
        self.source_state.select(selected_source)

        self.poller = NMRReadbackPoller(
            self.source_state, pv_factory
        )

        # A completed-event ticket does not establish which
        # TCP packet belongs to that event. Until a trusted
        # acquisition adapter binds both together, fail closed.
        self.packet_binding = PacketBoundAdmission(
            self.source_state
        )

        # No real acquisition adapter is registered yet.
        # Therefore no attestation can be minted by TCP alone.
        self.processor = SourceAwareProcessor(
            self._observe_unattested,
            packet_observer=self.packet_binding.observe_packet,
        )

        self._inner_processor = self.processor
        self.processor = self._process_sweep_serialized

        self.transport = DualSourceTransport(
            fitter_state,
            self.processor,
            labview_port=labview_port,
            pynmr_port=pynmr_port,
            verbose=verbose,
            connection_observer=(
                self._stream_connection_changed
            ),
            pynmr_only=pynmr_only,
        )

        # IPC is disabled unless an explicit path and UID
        # allowlist are supplied by the deployment layer.
        self.event_adapter = None
        self.event_ipc = None
        self.producer_session = (
            PinnedPyNMRProducer.from_environment()
            if pynmr_ipc_path is not None else None
        )
        self._commissioning_armed = False
        self._commissioning_invalid = False
        self._commissioning_route_epoch = None

        if pynmr_ipc_path is None:
            if (
                pynmr_ipc_allowed_uids is not None
                or pynmr_ipc_shared_gid is not None
            ):
                raise ValueError(
                    "IPC credentials supplied without IPC path"
                )
        else:
            if pynmr_ipc_allowed_uids is None:
                raise ValueError(
                    "IPC requires an explicit UID allowlist"
                )

            self.event_adapter = PyNMRCompletedEventAdapter(
                self.source_state,
                self.packet_binding,
            )

            self.event_ipc = PyNMRLocalIPC(
                pynmr_ipc_path,
                self._attest_pynmr_ipc,
                allowed_uids=pynmr_ipc_allowed_uids,
                shared_gid=pynmr_ipc_shared_gid,
                peer_authorizer=(
                    self.producer_session.peer_allowed
                    if self.producer_session is not None else None
                ),
            )

        # Source control is a SEPARATE, opt-in Linux IPC endpoint.
        # The PyNMR event stream retains its existing IPC and credentials.
        self.source_control_ipc = None
        self._source_control_worker = None
        if nmr_source_control_path is None:
            if (nmr_source_control_allowed_uid is not None
                    or nmr_source_control_allowed_pid is not None):
                raise ValueError("NMR source-control credentials without path")
        else:
            if (nmr_source_control_allowed_uid is None
                    or nmr_source_control_allowed_pid is None):
                raise ValueError("NMR source control requires pinned UID and PID")
            from .nmr_source_control_ipc import NMRSourceControlIPC
            from .nmr_source_ipc_lifecycle import NMRSourceIPCWorker

            self.source_control_ipc = NMRSourceControlIPC(
                nmr_source_control_path,
                allowed_uid=nmr_source_control_allowed_uid,
                allowed_pid=nmr_source_control_allowed_pid,
                snapshot=self.snapshot,
                select_source=self.select_source,
            )
            self._source_control_worker = NMRSourceIPCWorker(
                self.source_control_ipc
            )

        self.poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread = None

    def _observe_unattested(self):
        """Never authorize an unbound incoming TCP spectrum."""
        observation = self.source_state.snapshot()
        observation["post_switch_ready"] = False
        return observation

    def _process_sweep_serialized(
        self, state, message, trusted_source
    ):
        """Keep admission and fit atomic with observed route updates."""
        with self.source_state.fit_guard():
            return self._inner_processor(
                state, message, trusted_source
            )

    def _stream_connection_changed(
        self, source, connected
    ):
        # Actual TCP lifecycle, not producer authentication.
        with self.source_state.fit_guard():
            self.source_state.update_stream(
                source, connected
            )

            if (
                source == "pynmr"
                and not connected
                and self.event_adapter is not None
            ):
                self.event_adapter.invalidate()
                if (
                    self.producer_session is not None
                    and self._commissioning_armed
                ):
                    self._commissioning_armed = False
                    self._commissioning_invalid = True

    def _update_commissioning_arm(self):
        """Arm only after verifying the pinned producer and route."""
        if self.producer_session is None:
            return

        with self.source_state.fit_guard():
            if self._commissioning_invalid:
                return

            if not self.producer_session.matches_process():
                self.event_adapter.invalidate()
                self._commissioning_armed = False
                self._commissioning_invalid = True
                return

            state = self.source_state.snapshot()
            ready = self.event_adapter._route_ready(state)

            if self._commissioning_armed:
                if (
                    not ready
                    or state["route_epoch"]
                    != self._commissioning_route_epoch
                ):
                    self.event_adapter.invalidate()
                    self._commissioning_armed = False
                    self._commissioning_invalid = True
                return

            if ready and self.event_adapter.arm():
                self._commissioning_armed = True
                self._commissioning_route_epoch = (
                    state["route_epoch"]
                )

    def _attest_pynmr_ipc(self, event, message):
        """Admit only events from an already armed adapter.

        IPC UID permission is not sufficient to arm acquisition.
        The producer-session handshake remains a separate step.
        """
        if self.producer_session is not None:
            if (
                not self._commissioning_armed
                or self._commissioning_invalid
                or not self.producer_session.matches_process()
            ):
                print(
                    "[GENESIS ADMISSION] commissioning rejected: "
                    f"armed={self._commissioning_armed} "
                    f"invalid={self._commissioning_invalid} "
                    f"route={self.source_state.snapshot()}",
                    flush=True,
                )
                return False
        if (
            self._stop.is_set()
            or self.event_adapter is None
        ):
            return False

        accepted = self.event_adapter.attest_completed_event(
            event, message
        )
        print(
            f"[GENESIS ADMISSION] adapter accepted={accepted} "
            f"route={self.source_state.snapshot()}",
            flush=True,
        )
        return accepted

    def select_source(self, source):
        """Select software input; invalidate prior acquisition authorization."""
        with self.source_state.fit_guard():
            current = self.source_state.snapshot()["selected_source"]
            if source not in ("labview", "pynmr") or (
                self.pynmr_only and source != "pynmr"
            ):
                raise ValueError(f"Unsupported NMR source: {source!r}")
            if source == current:
                return

            # Prevent old PyNMR event credentials from authorizing
            # an acquisition under the new source-selection epoch.
            if self.event_adapter is not None:
                self.event_adapter.invalidate()

            self._commissioning_armed = False
            self._commissioning_route_epoch = None
            # Never clear _commissioning_invalid here. A prior
            # authentication failure remains latched.
            self.source_state.select(source)

    def _poll_loop(self):
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.poller.poll_once()
                if self.producer_session is not None:
                    self._update_commissioning_arm()
            except Exception:
                self.source_state.update_route(None, None)
                if self.producer_session is not None:
                    self.event_adapter.invalidate()
                    self._commissioning_armed = False
                    self._commissioning_invalid = True

    def start(self):
        if self._thread is not None:
            raise RuntimeError("protected runtime already started")

        # An unavailable EPICS readback fails closed.
        self.poller.poll_once()

        self.transport.start()

        self._stop.clear()

        try:
            if self.event_ipc is not None:
                self.event_ipc.start()
            if self._source_control_worker is not None:
                self._source_control_worker.start()

            thread = threading.Thread(
                target=self._poll_loop,
                name="genesis-nmr-readback",
                daemon=True,
            )
            thread.start()
            self._thread = thread
        except BaseException:
            self._stop.set()
            try:
                if self._source_control_worker is not None:
                    self._source_control_worker.close()
            finally:
                try:
                    if self.event_ipc is not None:
                        self.event_ipc.close()
                finally:
                    self.transport.close()
            raise

        return self

    def shutdown(self):
        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        try:
            if self._source_control_worker is not None:
                self._source_control_worker.close()
        finally:
            try:
                if self.event_ipc is not None:
                    self.event_ipc.close()
            finally:
                if self.event_adapter is not None:
                    self.event_adapter.invalidate()
                    self.source_state.update_stream(
                        "pynmr", False
                    )
                self.transport.close()

    def snapshot(self):
        return self.source_state.snapshot()
