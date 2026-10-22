"""
Genesis EPICS-facing runtime for the online NMR fitter.

This wrapper keeps the authoritative online_fitter FitEngine and TCP data path
unchanged, but exposes the same live fitter instance through EPICS so Phoebus
can be the operator interface.

Safety boundary:
- no WaveGen/SMC/switch/sequencer hardware authority
- no WAVEFORM_CMD/LOAD/OUTPUT/ARM/START authority
- all fitter networking remains loopback-only
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from softioc import builder, softioc, asyncio_dispatcher

from . import engine as E
from . import run as R
from . import server as S


log = logging.getLogger("online_fitter.epics_run")

DEFAULT_PREFIX = "UNH:GEN:SSRF:FIT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_FIT_PORT = 8777
DEFAULT_PUBLISH_PORT = 8778
MAX_NMR_POINTS = 8192

ALLOWED_MODES = tuple(E.ALL_MODES)
ALLOWED_MATERIALS = tuple(E.MATERIAL_NAMES)


@dataclass
class RuntimeHolder:
    engine: Any = None
    state: Any = None
    mode_hint: str = ""


class FitterEpicsInterface:
    """EPICS control/readback surface around one live FitEngine instance."""

    def __init__(
        self,
        holder: RuntimeHolder,
        *,
        prefix: str,
        publish_host: str,
        publish_port: int,
    ):
        if publish_host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("fitter publisher must remain loopback-only")

        self.holder = holder
        self.prefix = str(prefix)
        self.publish_host = "127.0.0.1"
        self.publish_port = int(publish_port)

        self._stop = threading.Event()
        self._update_lock = threading.Lock()
        self._subscriber_thread = None
        self._engine_watch_thread = None

        builder.SetDeviceName(self.prefix)
        self.pvs = {}
        self._create_pvs()

    # ------------------------------------------------------------------
    # PV construction
    # ------------------------------------------------------------------

    def _create_pvs(self):
        p = self.pvs

        # Runtime health / state.
        p["FITTER_UP"] = builder.boolIn(
            "FITTER_UP",
            ZNAM="Down",
            ONAM="Running",
        )
        p["PUBLISHER_CONNECTED"] = builder.boolIn(
            "PUBLISHER_CONNECTED",
            ZNAM="Disconnected",
            ONAM="Connected",
        )
        p["CALIBRATED"] = builder.boolIn(
            "CALIBRATED",
            ZNAM="Preset",
            ONAM="Calibrated",
        )
        p["UPDATE_BUSY"] = builder.boolIn(
            "UPDATE_BUSY",
            ZNAM="Idle",
            ONAM="Updating",
        )
        p["LAST_OK"] = builder.aIn(
            "LAST_OK",
            PREC=3,
        )
        p["LAST_TS"] = builder.stringIn("LAST_TS")
        p["WARNING"] = builder.longStringIn(
            "WARNING",
            length=256,
        )

        # Read-only dual-source NMR status.
        # No hardware source-switch commands are exposed here.
        p["NMR_PROTECTED"] = builder.boolIn(
            "NMR_PROTECTED",
            ZNAM="Legacy",
            ONAM="Protected",
        )
        p["NMR_SOURCE_RBV"] = builder.stringIn(
            "NMR_SOURCE_RBV"
        )
        p["NMR_SOURCE_EPOCH_RBV"] = builder.longIn(
            "NMR_SOURCE_EPOCH_RBV"
        )
        p["NMR_SOURCE_CMD"] = builder.stringOut(
            "NMR_SOURCE_CMD",
            on_update_name=self._on_nmr_source,
        )
        p["NMR_SOURCE_CHANGE_STATUS"] = builder.longStringIn(
            "NMR_SOURCE_CHANGE_STATUS",
            length=256,
        )
        p["NMR_PHYSICAL_RBV"] = builder.stringIn(
            "NMR_PHYSICAL_RBV"
        )
        p["NMR_MASK_RBV"] = builder.longIn(
            "NMR_MASK_RBV"
        )
        p["NMR_ROUTE_OK"] = builder.boolIn(
            "NMR_ROUTE_OK",
            ZNAM="Unverified",
            ONAM="Verified",
        )
        p["NMR_STREAM_OK"] = builder.boolIn(
            "NMR_STREAM_OK",
            ZNAM="Unavailable",
            ONAM="Connected",
        )
        p["NMR_EVENT_READY"] = builder.boolIn(
            "NMR_EVENT_READY",
            ZNAM="Not ready",
            ONAM="Pending event",
        )
        p["NMR_FIT_ELIGIBLE"] = builder.boolIn(
            "NMR_FIT_ELIGIBLE",
            ZNAM="Blocked",
            ONAM="Eligible",
        )
        p["NMR_STATUS"] = builder.longStringIn(
            "NMR_STATUS",
            length=256,
        )

        # Operator controls. These are fitter-software controls only.
        p["MODE_CMD"] = builder.stringOut(
            "MODE_CMD",
            on_update_name=self._on_mode,
        )
        p["MODE_RBV"] = builder.stringIn("MODE_RBV")

        p["MATERIAL_CMD"] = builder.stringOut(
            "MATERIAL_CMD",
            on_update_name=self._on_material,
        )
        p["MATERIAL_RBV"] = builder.stringIn("MATERIAL_RBV")

        p["LARMOR_KHZ_CMD"] = builder.aOut(
            "LARMOR_KHZ_CMD",
            DRVL=0.0,
            DRVH=200.0,
            PREC=2,
            on_update_name=self._on_larmor,
        )
        p["LARMOR_KHZ_RBV"] = builder.aIn(
            "LARMOR_KHZ_RBV",
            PREC=2,
        )

        p["UPDATE_CONSTANTS_CMD"] = builder.boolOut(
            "UPDATE_CONSTANTS_CMD",
            ZNAM="Idle",
            ONAM="Update",
            on_update_name=self._on_update_constants,
        )

        # Latest live fit summary.
        for name in (
            "P_AREA",
            "P_FIT",
            "Q_EQ",
            "Q_FIT",
            "R",
            "PHASE_DEG",
            "RED_CHISQ",
            "CC",
            "CC_DAQ",
            "OMEGD",
            "OMEGQ1",
            "OMEGQ2",
            "A1",
            "ETA1",
            "ETA2",
            "K",
            "SCALE",
        ):
            p[name] = builder.aIn(
                name,
                PREC=6,
            )

        p["NPTS"] = builder.longIn(
            "NPTS",
            LOPR=0,
            HOPR=MAX_NMR_POINTS,
        )
        p["FIT_TYPE"] = builder.stringIn("FIT_TYPE")
        p["MODEL"] = builder.stringIn("MODEL")
        p["CONSTANTS_SOURCE"] = builder.stringIn("CONSTANTS_SOURCE")

        # Arrays for Phoebus plots.
        for name in (
            "FREQS_MHZ",
            "DATA",
            "TOTAL_FIT",
            "RAW",
            "BASESUB",
        ):
            p[name] = builder.WaveformIn(
                name,
                length=MAX_NMR_POINTS,
                datatype="float32",
            )

        p["FITTER_UP"].set(0)
        p["PUBLISHER_CONNECTED"].set(0)
        p["CALIBRATED"].set(0)
        p["UPDATE_BUSY"].set(0)
        p["WARNING"].set("Waiting for fitter")
        p["NPTS"].set(0)

        p["NMR_PROTECTED"].set(0)
        p["NMR_SOURCE_RBV"].set("Disabled")
        p["NMR_SOURCE_EPOCH_RBV"].set(-1)
        p["NMR_PHYSICAL_RBV"].set("UNVERIFIED")
        p["NMR_MASK_RBV"].set(-1)
        p["NMR_ROUTE_OK"].set(0)
        p["NMR_STREAM_OK"].set(0)
        p["NMR_EVENT_READY"].set(0)
        p["NMR_FIT_ELIGIBLE"].set(0)
        p["NMR_STATUS"].set("Protected NMR inactive")

    # ------------------------------------------------------------------
    # Runtime ownership
    # ------------------------------------------------------------------

    def start_background_threads(self):
        self._engine_watch_thread = threading.Thread(
            target=self._watch_engine,
            name="fitter-epics-engine-watch",
            daemon=True,
        )
        self._engine_watch_thread.start()

        self._subscriber_thread = threading.Thread(
            target=self._subscriber_loop,
            name="fitter-epics-publisher-subscriber",
            daemon=True,
        )
        self._subscriber_thread.start()

    def stop(self):
        self._stop.set()

    def _watch_engine(self):
        while not self._stop.is_set():
            engine = self.holder.engine
            state = self.holder.state

            if engine is not None and state is not None:
                self.pvs["FITTER_UP"].set(1)
                self._sync_control_readbacks()
            else:
                self.pvs["FITTER_UP"].set(0)

            self._sync_nmr_source_readbacks()
            self._stop.wait(0.5)


    def _sync_nmr_source_readbacks(self):
        """Publish software/readback status without EPICS writes."""

        p = self.pvs

        state = self.holder.state
        runtime = getattr(
            state,
            "genesis_nmr_protected_runtime",
            None,
        ) if state is not None else None

        if runtime is None:
            p["NMR_PROTECTED"].set(0)
            p["NMR_SOURCE_RBV"].set("Disabled")
            p["NMR_SOURCE_EPOCH_RBV"].set(-1)
            p["NMR_PHYSICAL_RBV"].set("UNVERIFIED")
            p["NMR_MASK_RBV"].set(-1)
            p["NMR_ROUTE_OK"].set(0)
            p["NMR_STREAM_OK"].set(0)
            p["NMR_EVENT_READY"].set(0)
            p["NMR_FIT_ELIGIBLE"].set(0)
            p["NMR_STATUS"].set("Protected NMR inactive")
            return

        try:
            snapshot = runtime.snapshot()
            if not isinstance(snapshot, dict):
                raise ValueError("Invalid NMR source observation")
        except Exception:
            snapshot = {}

        selected = snapshot.get("selected_source")
        mask = snapshot.get("nmr_mask")
        comm = snapshot.get("nmr_comm")
        age = snapshot.get("route_age_s")

        if type(mask) is int and mask == 0:
            physical = "CONNECTED"
        elif type(mask) is int and mask == 3:
            physical = "TERMINATED"
        else:
            physical = None

        route_fresh = (
            type(age) in (int, float)
            and 0 <= age <= 5.0
        )

        route_ok = (
            type(comm) is int
            and comm == 1
            and physical == "CONNECTED"
            and route_fresh
        )

        stream_ok = (
            snapshot.get("stream_connected") is True
        )
        event_ready = (
            snapshot.get("post_switch_ready") is True
        )

        p["NMR_PROTECTED"].set(1)
        p["NMR_SOURCE_RBV"].set(
            str(selected or "None")[:39]
        )
        epoch = snapshot.get("route_epoch")
        p["NMR_SOURCE_EPOCH_RBV"].set(
            epoch if type(epoch) is int and epoch >= 0 else -1
        )
        p["NMR_PHYSICAL_RBV"].set(
            str(physical or "UNVERIFIED")[:39]
        )
        p["NMR_MASK_RBV"].set(
            mask if type(mask) is int else -1
        )
        p["NMR_ROUTE_OK"].set(int(route_ok))
        p["NMR_STREAM_OK"].set(int(stream_ok))
        p["NMR_EVENT_READY"].set(int(event_ready))

        # A pending ticket is NOT sufficient to establish
        # packet/event association. The protected deployment
        # remains blocked until that trusted adapter exists.
        p["NMR_FIT_ELIGIBLE"].set(0)

        if not route_ok:
            message = "NMR path disconnected, terminated or unverified"
        elif not stream_ok:
            message = "Waiting for trusted NMR acquisition stream"
        else:
            message = (
                "Fitting blocked: acquisition-to-spectrum "
                "binding not commissioned"
            )

        p["NMR_STATUS"].set(message)

    # ------------------------------------------------------------------
    # EPICS command callbacks
    # ------------------------------------------------------------------

    @staticmethod
    def _as_text(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace").strip()
        return str(value).strip()

    def _ready(self):
        if self.holder.engine is None or self.holder.state is None:
            self.pvs["WARNING"].set("Fitter engine not ready")
            return False
        return True

    def _on_nmr_source(self, new_value, _pv):
        """Fail-closed software source command.

        Source changes remain disabled until a coordinated sequencer
        authorization mechanism prevents concurrent RF/burn starts.
        This callback never operates physical switches.
        """
        requested = self._as_text(new_value).lower()

        state = self.holder.state
        runtime = getattr(
            state,
            "genesis_nmr_protected_runtime",
            None,
        ) if state is not None else None

        selected = None
        if runtime is not None:
            try:
                observation = runtime.snapshot()
                if isinstance(observation, dict):
                    selected = observation.get("selected_source")
            except Exception:
                pass

        if requested not in ("labview", "pynmr"):
            status = f"Rejected invalid NMR source: {requested}"
        elif selected not in ("labview", "pynmr"):
            status = "Rejected: protected NMR source unavailable"
        elif requested == selected:
            status = f"NMR source unchanged: {selected}"
        else:
            status = (
                "Rejected: coordinated sequencer source-change "
                "authorization not commissioned"
            )

        # A command request must never masquerade as the selected
        # source. Restore the operator command to the actual readback.
        self.pvs["NMR_SOURCE_CMD"].set(
            selected if selected is not None else "",
            process=False,
        )
        self.pvs["NMR_SOURCE_CHANGE_STATUS"].set(status)

        if requested != selected:
            self.pvs["WARNING"].set(status)

    def _on_mode(self, new_value, _pv):
        if not self._ready():
            return

        mode = self._as_text(new_value)

        if mode not in ALLOWED_MODES:
            self.pvs["WARNING"].set(
                f"Rejected mode: {mode}"
            )
            self._sync_control_readbacks()
            return

        try:
            self.holder.state.set_mode(mode)
            self.holder.mode_hint = mode
            self.pvs["MODE_RBV"].set(mode)
            self.pvs["MODE_CMD"].set(
                mode,
                process=False,
            )
            self.pvs["WARNING"].set("")
        except Exception as exc:
            self.pvs["WARNING"].set(
                f"Mode change failed: {exc}"
            )

    def _on_material(self, new_value, _pv):
        if not self._ready():
            return

        material = self._as_text(new_value)

        if material not in ALLOWED_MATERIALS:
            self.pvs["WARNING"].set(
                f"Rejected material: {material}"
            )
            self._sync_control_readbacks()
            return

        try:
            self.holder.engine.set_material(material)
            self.pvs["MATERIAL_RBV"].set(material)
            self.pvs["MATERIAL_CMD"].set(
                material,
                process=False,
            )
            self.pvs["CALIBRATED"].set(0)
            self._publish_constants_snapshot()
            self.pvs["WARNING"].set(
                "Material changed; Update Constants required"
            )
        except Exception as exc:
            self.pvs["WARNING"].set(
                f"Material change failed: {exc}"
            )

    def _on_larmor(self, new_value, _pv):
        if not self._ready():
            return

        try:
            value = float(new_value)
            if not math.isfinite(value):
                raise ValueError("non-finite value")
            if not 0.0 <= value <= 200.0:
                raise ValueError("outside 0..200 kHz")

            self.holder.engine.set_larmor_khz(value)
            self.pvs["LARMOR_KHZ_RBV"].set(value)
            self.pvs["LARMOR_KHZ_CMD"].set(
                value,
                process=False,
            )
            self.pvs["WARNING"].set("")
        except Exception as exc:
            self.pvs["WARNING"].set(
                f"Larmor update failed: {exc}"
            )

    def _on_update_constants(self, new_value, _pv):
        if not bool(new_value):
            return

        if not self._ready():
            self.pvs["UPDATE_CONSTANTS_CMD"].set(
                0,
                process=False,
            )
            return

        state = self.holder.state
        mode = self._state_mode(
            state,
            self.holder.mode_hint,
        )

        if mode != E.MODE_EQUILIBRIUM:
            self.pvs["WARNING"].set(
                "Update Constants requires equilibrium mode"
            )
            self.pvs["UPDATE_CONSTANTS_CMD"].set(
                0,
                process=False,
            )
            return

        if not self._update_lock.acquire(blocking=False):
            self.pvs["WARNING"].set(
                "Update Constants already running"
            )
            self.pvs["UPDATE_CONSTANTS_CMD"].set(
                0,
                process=False,
            )
            return

        self.pvs["UPDATE_BUSY"].set(1)
        self.pvs["WARNING"].set("Update Constants running")

        threading.Thread(
            target=self._update_constants_worker,
            name="fitter-update-constants",
            daemon=True,
        ).start()

    def _update_constants_worker(self):
        try:
            result = self.holder.engine.update_constants()
            self._publish_constants_snapshot()
            self.pvs["CALIBRATED"].set(1)

            p = None
            r = None
            if isinstance(result, dict):
                p = result.get("P")
                r = result.get("r")

            note = "Update Constants OK"
            if p is not None:
                note += f"; P={float(p):.2f}%"
            if r is not None:
                note += f"; r={float(r):.3f}"

            self.pvs["WARNING"].set(note)

        except Exception as exc:
            self.pvs["WARNING"].set(
                f"Update Constants failed: {exc}"
            )
        finally:
            self.pvs["UPDATE_BUSY"].set(0)
            self.pvs["UPDATE_CONSTANTS_CMD"].set(
                0,
                process=False,
            )
            self._update_lock.release()

    # ------------------------------------------------------------------
    # Publisher -> EPICS readbacks
    # ------------------------------------------------------------------

    def _subscriber_loop(self):
        """
        Subscribe to the loopback NDJSON publisher without socket.makefile().

        A timed-out buffered makefile reader becomes unusable on CPython
        ("cannot read from timed out object").  The fitter publisher can be
        legitimately idle between sweeps, so use raw recv() with an explicit
        byte buffer instead.  A socket timeout is then just an idle interval,
        not a disconnect.
        """
        while not self._stop.is_set():
            if self.holder.engine is None:
                self._stop.wait(0.1)
                continue

            try:
                with socket.create_connection(
                    (self.publish_host, self.publish_port),
                    timeout=2.0,
                ) as sock:
                    sock.settimeout(1.0)
                    self.pvs["PUBLISHER_CONNECTED"].set(1)
                    self.pvs["WARNING"].set("")

                    buffer = b""

                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(65536)
                        except socket.timeout:
                            continue

                        if not chunk:
                            raise ConnectionError(
                                "publisher closed"
                            )

                        buffer += chunk

                        while b"\n" in buffer:
                            raw_line, buffer = buffer.split(
                                b"\n",
                                1,
                            )

                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue

                            msg = json.loads(
                                raw_line.decode(
                                    "utf-8",
                                    "replace",
                                )
                            )

                            if msg.get("type") == "fit":
                                self._apply_fit_message(msg)

            except Exception as exc:
                self.pvs["PUBLISHER_CONNECTED"].set(0)

                if self.holder.engine is not None:
                    self.pvs["WARNING"].set(
                        f"Publisher reconnecting: {exc}"
                    )

                self._stop.wait(1.0)

    @staticmethod
    def _finite_or_zero(value):
        if value is None:
            return 0.0
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    @staticmethod
    def _array(values):
        if values is None:
            return np.asarray([], dtype=np.float32)
        return np.asarray(
            [
                np.nan if value is None else float(value)
                for value in values
            ],
            dtype=np.float32,
        )

    def _apply_fit_message(self, msg):
        p = self.pvs
        constants = msg.get("constants") or {}

        p["LAST_OK"].set(time.time())
        p["LAST_TS"].set(
            str(msg.get("ts") or "")[:39]
        )
        p["FIT_TYPE"].set(
            str(msg.get("fit_type") or "")[:39]
        )
        p["MODEL"].set(
            str(msg.get("model") or "")[:39]
        )
        message_mode = str(
            msg.get("mode") or ""
        )
        if message_mode:
            self.holder.mode_hint = message_mode

        p["MODE_RBV"].set(
            message_mode[:39]
        )
        p["MATERIAL_RBV"].set(
            str(msg.get("material") or "")[:39]
        )
        p["CALIBRATED"].set(
            1 if msg.get("constants_good") is True else 0
        )

        scalar_map = {
            "P_AREA": msg.get("P_area"),
            "P_FIT": msg.get("P_fit"),
            "Q_EQ": msg.get("Q_eq"),
            "Q_FIT": msg.get("Q_fit"),
            "R": msg.get("r"),
            "PHASE_DEG": msg.get("phase_deg"),
            "RED_CHISQ": msg.get("red_chisq"),
            "CC": constants.get("CC"),
            "CC_DAQ": constants.get("CC_DAQ"),
            "OMEGD": constants.get("omegD"),
            "OMEGQ1": constants.get("omegQ1"),
            "OMEGQ2": constants.get("omegQ2"),
            "A1": constants.get("A1"),
            "ETA1": constants.get("eta1"),
            "ETA2": constants.get("eta2"),
            "K": constants.get("K"),
            "SCALE": constants.get("scale"),
        }

        for name, value in scalar_map.items():
            p[name].set(
                self._finite_or_zero(value)
            )

        p["CONSTANTS_SOURCE"].set(
            str(constants.get("source") or "")[:39]
        )

        npts = int(msg.get("npts") or 0)
        p["NPTS"].set(
            max(0, min(npts, MAX_NMR_POINTS))
        )

        arrays = {
            "FREQS_MHZ": msg.get("freqs_mhz"),
            "DATA": msg.get("data"),
            "TOTAL_FIT": msg.get("total_fit"),
            "RAW": msg.get("raw"),
            "BASESUB": msg.get("baseline_sub"),
        }

        for name, values in arrays.items():
            arr = self._array(values)
            if arr.size > MAX_NMR_POINTS:
                arr = arr[:MAX_NMR_POINTS]
            p[name].set(arr)

        error = msg.get("error")
        if error:
            p["WARNING"].set(str(error)[:255])
        elif msg.get("constants_good") is True:
            p["WARNING"].set("")

    # ------------------------------------------------------------------
    # Engine -> control readbacks
    # ------------------------------------------------------------------

    @staticmethod
    def _state_mode(state, fallback=""):
        """
        Resolve the current SharedState mode across fitter revisions.

        The public control API is set_mode(); older/current copies have not
        guaranteed a public ``mode`` attribute.  Prefer a public attribute,
        then the common private attribute, then an optional getter, and finally
        the constructor/control hint captured by this wrapper.
        """
        for name in (
            "mode",
            "_mode",
        ):
            value = getattr(
                state,
                name,
                None,
            )

            if isinstance(value, str) and value:
                return value

        getter = getattr(
            state,
            "get_mode",
            None,
        )

        if callable(getter):
            value = getter()
            if isinstance(value, str) and value:
                return value

        return str(fallback or "")

    def _sync_control_readbacks(self):
        engine = self.holder.engine
        state = self.holder.state

        if engine is None or state is None:
            return

        material = str(
            getattr(engine, "material", "")
        )
        mode = self._state_mode(
            state,
            self.holder.mode_hint,
        )
        larmor = self._finite_or_zero(
            getattr(engine, "larmor_khz", 0.0)
        )

        self.pvs["MATERIAL_RBV"].set(material[:39])
        self.pvs["MODE_RBV"].set(mode[:39])
        self.pvs["LARMOR_KHZ_RBV"].set(larmor)

        self.pvs["MATERIAL_CMD"].set(
            material,
            process=False,
        )
        self.pvs["MODE_CMD"].set(
            mode,
            process=False,
        )
        self.pvs["LARMOR_KHZ_CMD"].set(
            larmor,
            process=False,
        )

        self._publish_constants_snapshot()

    def _publish_constants_snapshot(self):
        engine = self.holder.engine

        if engine is None:
            return

        try:
            snap = engine.constants_snapshot()
        except Exception as exc:
            self.pvs["WARNING"].set(
                f"Constants snapshot failed: {exc}"
            )
            return

        if not isinstance(snap, dict):
            return

        constants = snap.get("constants") or snap

        source = (
            constants.get("source")
            if isinstance(constants, dict)
            else None
        )
        if not source:
            source = getattr(
                engine,
                "const_source",
                "",
            )

        calibrated = str(source) == "fit"
        self.pvs["CALIBRATED"].set(
            1 if calibrated else 0
        )
        self.pvs["CONSTANTS_SOURCE"].set(
            str(source or "")[:39]
        )

        mapping = {
            "CC": snap.get("CC"),
            "CC_DAQ": snap.get("CC_DAQ"),
            "RED_CHISQ": snap.get("redchi"),
        }

        if isinstance(constants, dict):
            mapping.update(
                {
                    "OMEGD": constants.get("omegD"),
                    "OMEGQ1": constants.get("omegQ1"),
                    "OMEGQ2": constants.get("omegQ2"),
                    "A1": constants.get("A1"),
                    "ETA1": constants.get("eta1"),
                    "ETA2": constants.get("eta2"),
                    "K": constants.get("K"),
                    "SCALE": constants.get("scale"),
                    "R": constants.get("r"),
                }
            )

        for name, value in mapping.items():
            self.pvs[name].set(
                self._finite_or_zero(value)
            )


def _install_capture_hooks(holder: RuntimeHolder):
    """
    Capture the exact FitEngine and SharedState instances constructed by
    online_fitter.run. The numerical fitter and TCP protocol are otherwise
    untouched.
    """

    original_engine_init = E.FitEngine.__init__
    original_state_init = S.SharedState.__init__

    def engine_init(instance, *args, **kwargs):
        original_engine_init(
            instance,
            *args,
            **kwargs,
        )
        holder.engine = instance

    def state_init(instance, *args, **kwargs):
        original_state_init(
            instance,
            *args,
            **kwargs,
        )

        mode = kwargs.get("mode")

        if mode is None and len(args) >= 2:
            mode = args[1]

        if mode is None:
            mode = E.MODE_BASELINE

        holder.mode_hint = str(mode)
        holder.state = instance

    E.FitEngine.__init__ = engine_init
    S.SharedState.__init__ = state_init


def _build_run_args(args):
    run_args = [
        "--host",
        DEFAULT_HOST,
        "--port",
        str(args.port),
        "--publish",
        "--publish-port",
        str(args.publish_port),
        "--material",
        args.material,
        "--mode",
        args.mode,
        "--eq-buffer",
        str(args.eq_buffer),
        "--no-gui",
    ]

    if args.verbose:
        run_args.append("--verbose")

    if args.replace:
        run_args.append("--replace")

    if args.nmr_protected:
        run_args.extend([
            "--nmr-protected",
            "--nmr-source",
            args.nmr_source,
            "--pynmr-port",
            str(args.pynmr_port),
        ])
        if getattr(args, "pynmr_only", False):
            run_args.append("--pynmr-only")
        if getattr(args, "pynmr_ipc_path", None) is not None:
            run_args.extend([
                "--pynmr-ipc-path",
                args.pynmr_ipc_path,
            ])
            for uid in getattr(args, "pynmr_ipc_allowed_uid", None) or ():
                run_args.extend([
                    "--pynmr-ipc-allowed-uid",
                    str(uid),
                ])
            if getattr(args, "pynmr_ipc_shared_gid", None) is not None:
                run_args.extend([
                    "--pynmr-ipc-shared-gid",
                    str(args.pynmr_ipc_shared_gid),
                ])

    # Forward the pinned sequencer identity only when explicitly configured.
    control_path = getattr(args, "nmr_source_control_path", None)
    if control_path is not None:
        run_args.extend([
            "--nmr-source-control-path", control_path,
            "--nmr-source-control-allowed-uid",
            str(args.nmr_source_control_allowed_uid),
            "--nmr-source-control-allowed-pid",
            str(args.nmr_source_control_allowed_pid),
        ])

    return run_args


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Run the authoritative online fitter headlessly "
            "with an EPICS/Phoebus control/readback surface."
        )
    )

    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_FIT_PORT,
    )
    parser.add_argument(
        "--publish-port",
        type=int,
        default=DEFAULT_PUBLISH_PORT,
    )
    parser.add_argument(
        "--nmr-protected",
        action="store_true",
        help="Enable protected dual-source NMR receiving",
    )
    parser.add_argument(
        "--nmr-source",
        choices=("labview", "pynmr"),
        default=None,
        help="Software source selection only; no hardware switching",
    )
    parser.add_argument(
        "--pynmr-only", action="store_true",
        help="bind only the protected PyNMR receiver",
    )
    parser.add_argument(
        "--pynmr-port",
        type=int,
        default=8779,
        help="Protected PyNMR localhost receiver port",
    )
    parser.add_argument(
        "--pynmr-ipc-path",
        default=None,
        help="Opt-in absolute PyNMR IPC Unix socket path",
    )
    parser.add_argument(
        "--pynmr-ipc-allowed-uid",
        type=int,
        action="append",
        default=None,
        help="Allowed IPC peer UID; repeatable",
    )
    parser.add_argument(
        "--pynmr-ipc-shared-gid",
        type=int,
        default=None,
        help="Optional Unix socket shared-group GID",
    )
    parser.add_argument(
        "--material",
        default="d-butanol",
        choices=list(ALLOWED_MATERIALS),
    )
    parser.add_argument(
        "--mode",
        default=E.MODE_BASELINE,
        choices=list(ALLOWED_MODES),
    )
    parser.add_argument(
        "--eq-buffer",
        type=int,
        default=E.EQ_BUFFER_DEFAULT,
    )
    parser.add_argument(
        "--replace",
        action="store_true",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    parser.add_argument("--nmr-source-control-path", default=None,
                    help="Opt-in Linux source-control Unix socket path")
    parser.add_argument("--nmr-source-control-allowed-uid", type=int, default=None,
                    help="Trusted sequencer Linux UID for source-control IPC")
    parser.add_argument("--nmr-source-control-allowed-pid", type=int, default=None,
                    help="Trusted sequencer Linux PID for source-control IPC")

    args = parser.parse_args(argv)
    # All-or-none, opt-in, and separate from physical ssRF execution.
    control_path = args.nmr_source_control_path
    control_uid = args.nmr_source_control_allowed_uid
    control_pid = args.nmr_source_control_allowed_pid
    if any(value is not None for value in (control_path, control_uid, control_pid)):
        from pathlib import Path as _NMRControlPath
        if not args.nmr_protected:
            parser.error("NMR source-control IPC requires protected NMR mode")
        if (type(control_path) is not str or not control_path
                or not _NMRControlPath(control_path).is_absolute()):
            parser.error("NMR source-control IPC requires absolute socket path")
        if type(control_uid) is not int or control_uid < 0:
            parser.error("NMR source-control IPC requires trusted UID")
        if type(control_pid) is not int or control_pid <= 0:
            parser.error("NMR source-control IPC requires trusted PID")
        if control_path == args.pynmr_ipc_path:
            parser.error("NMR source-control and PyNMR event sockets must differ")

    # Protected PyNMR IPC is explicit and fail-closed.
    if args.pynmr_ipc_path is not None:
        from pathlib import Path as _IPCPath
        if not args.nmr_protected:
            parser.error("PyNMR IPC requires protected NMR mode")
        if not _IPCPath(args.pynmr_ipc_path).is_absolute():
            parser.error("PyNMR IPC socket path must be absolute")
        if not args.pynmr_ipc_allowed_uid:
            parser.error("PyNMR IPC requires explicit allowed UIDs")
        if any(uid < 0 for uid in args.pynmr_ipc_allowed_uid):
            parser.error("IPC UIDs must be nonnegative")
        if (
            args.pynmr_ipc_shared_gid is not None
            and args.pynmr_ipc_shared_gid < 0
        ):
            parser.error("IPC shared GID must be nonnegative")
    elif (
        args.pynmr_ipc_allowed_uid is not None
        or args.pynmr_ipc_shared_gid is not None
    ):
        parser.error("IPC credentials require --pynmr-ipc-path")

    if args.pynmr_only and (
        not args.nmr_protected or args.nmr_source != "pynmr"
    ):
        parser.error("--pynmr-only requires protected PyNMR source")
    if args.nmr_protected:
        if args.nmr_source is None:
            parser.error(
                "--nmr-protected requires --nmr-source"
            )
        if args.replace:
            parser.error(
                "--replace is disabled in protected NMR mode"
            )
        ports = (
            args.port,
            args.pynmr_port,
            args.publish_port,
        )
        if (
            any(not 1 <= port <= 65535 for port in ports)
            or len(set(ports)) != 3
        ):
            parser.error(
                "Protected NMR input and publisher ports "
                "must be distinct valid TCP ports"
            )
    elif args.nmr_source is not None:
        parser.error(
            "--nmr-source requires --nmr-protected"
        )

    if args.port == args.publish_port:
        raise SystemExit(
            "fit input port and publisher port must differ"
        )

    holder = RuntimeHolder()
    _install_capture_hooks(holder)

    interface = FitterEpicsInterface(
        holder,
        prefix=args.prefix,
        publish_host=DEFAULT_HOST,
        publish_port=args.publish_port,
    )

    dispatcher = asyncio_dispatcher.AsyncioDispatcher()
    builder.LoadDatabase()
    softioc.iocInit(dispatcher)

    interface.start_background_threads()

    try:
        # Keep online_fitter.run as the authoritative owner of the engine,
        # input server, publisher, logging, and fitting behavior.
        R.main(
            _build_run_args(args)
        )
    finally:
        interface.stop()


if __name__ == "__main__":
    main()
