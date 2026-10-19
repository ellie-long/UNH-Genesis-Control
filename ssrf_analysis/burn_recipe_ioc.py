"""Genesis ssRF burn-recipe analysis IOC.

This IOC owns no hardware.  It consumes the latest calibrated online-fitter
publisher message, builds the adaptive 8192-point waveform with the existing
planner, stages that waveform only through the sequencer staging boundary, and
asks the sequencer to perform the source-side PREPARE/LOAD phase.

Hardware authority remains in the sequencer -> EpicsIO -> device IOC path.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import threading
import time

import aioca
from softioc import builder

from ssrf_analysis.waveform_planner import (
    FitSnapshot,
    build_waveform_plan,
)
from ssrf_analysis.waveform_stager import WaveformStager
from archive.data_paths import ssrf_event_dir, ssrf_session_dir, resolve_recording_path
from ssrf_analysis.operator_waveform import build_operator_waveform_plan
from ssrf_analysis.event_logger import JsonlEventLogger, new_session_id
from ssrf_analysis.session_data import SessionDataWriter
from ssrf_analysis import legacy_deuts as deuts


FIT_PUBLISH_HOST = "127.0.0.1"
FIT_PUBLISH_PORT = 8778
SEQ = "UNH:GEN:SSRF:SEQ"
WAVEFORM_POINTS = 8192
PHYSICAL_VHIGH_V = 0.5
PHYSICAL_VLOW_V = -0.5
ALLOWED_OBJECTIVES = ("Enhancement", "Suppression", "Zero")


class Device:
    """Analysis-only recipe controller for one manual ssRF burn."""

    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.pvs = {}

        self.publish_host = str(settings.get("publish_host", FIT_PUBLISH_HOST))
        self.publish_port = int(settings.get("publish_port", FIT_PUBLISH_PORT))
        if self.publish_host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("burn recipe fitter publisher must remain loopback-only")

        self.burn_mode = str(os.environ.get("GENESIS_SSRF_BURN_MODE", settings.get("burn_mode", "fit"))).strip().lower()
        if self.burn_mode not in ("fit", "operator"):
            raise ValueError("burn_mode must be fit or operator")
        self.operator_material = str(os.environ.get("GENESIS_SSRF_OPERATOR_MATERIAL", settings.get("operator_material", "Butanol")))
        self.operator_polarization = float(os.environ.get("GENESIS_SSRF_OPERATOR_POLARIZATION", settings.get("operator_polarization", 0.30)))
        center = os.environ.get("GENESIS_SSRF_OPERATOR_CENTER_MHZ")
        self.operator_center_mhz = None if not center else float(center)
        self.max_fit_age_s = float(settings.get("max_fit_age_s", 60.0))
        if self.max_fit_age_s <= 0:
            raise ValueError("max_fit_age_s must be positive")

        self._latest_lock = threading.Lock()
        self._latest_message = None
        self._latest_received = 0.0
        self._stop = threading.Event()
        self._subscriber_thread = None

        self._generate_requested = False
        self._generate_task = None

        self._event_logger = JsonlEventLogger(
            directory=resolve_recording_path(
                self.settings.get("event_log_dir"), ssrf_event_dir()
            )
        )
        self._session_writer = SessionDataWriter(
            root=resolve_recording_path(
                self.settings.get("session_data_root"), ssrf_session_dir()
            )
        )
        self._active_session_id = ""
        self._last_execution_state = None

        self._create_pvs()

    def _create_pvs(self):
        p = self.pvs

        p["FITTER_CONNECTED"] = builder.boolIn(
            "FITTER_CONNECTED", ZNAM="Disconnected", ONAM="Connected"
        )
        p["LATEST_FIT_VALID"] = builder.boolIn(
            "LATEST_FIT_VALID", ZNAM="Invalid", ONAM="Valid"
        )
        p["BURN_MODE_RBV"] = builder.stringIn("BURN_MODE_RBV")
        p["MATERIAL"] = builder.stringIn("MATERIAL")
        p["FIT_TYPE"] = builder.stringIn("FIT_TYPE")
        p["FIT_TS"] = builder.stringIn("FIT_TS")
        p["FIT_AGE_S"] = builder.aIn("FIT_AGE_S", EGU="s", PREC=1)

        p["CURRENT_POLARIZATION"] = builder.aIn(
            "CURRENT_POLARIZATION", EGU="%", PREC=3
        )
        p["REFERENCE_POLARIZATION"] = builder.aIn(
            "REFERENCE_POLARIZATION", EGU="%", PREC=3
        )

        p["OBJECTIVE_CMD"] = builder.stringOut(
            "OBJECTIVE_CMD",
            initial_value=str(self.settings.get("objective", "Enhancement")),
            on_update_name=self._objective_command,
        )
        p["OBJECTIVE_RBV"] = builder.stringIn("OBJECTIVE_RBV")

        p["CARRIER_MHZ"] = builder.aOut(
            "CARRIER_MHZ",
            initial_value=float(self.settings.get("carrier_mhz", 33.0)),
            EGU="MHz",
            PREC=6,
        )
        p["START_LEVEL_DBM"] = builder.aOut(
            "START_LEVEL_DBM",
            initial_value=float(self.settings.get("start_level_dbm", -120.0)),
            EGU="dBm",
            PREC=2,
        )
        p["TARGET_LEVEL_DBM"] = builder.aOut(
            "TARGET_LEVEL_DBM",
            initial_value=float(self.settings.get("target_level_dbm", -22.0)),
            EGU="dBm",
            PREC=2,
        )
        p["FM_DEV_KHZ"] = builder.aOut(
            "FM_DEV_KHZ",
            initial_value=float(self.settings.get("fm_deviation_khz", 10.0)),
            EGU="kHz",
            PREC=3,
        )
        p["FM_REPEATS"] = builder.longOut(
            "FM_REPEATS",
            initial_value=int(self.settings.get("fm_repeats", 10)),
            LOPR=1,
            HOPR=100000,
        )

        p["FM_PERIOD_S"] = builder.aIn("FM_PERIOD_S", EGU="s", PREC=6)
        p["SWEEP_STEP_DB"] = builder.aIn("SWEEP_STEP_DB", EGU="dB", PREC=3)
        p["SWEEP_DWELL_MS"] = builder.aIn("SWEEP_DWELL_MS", EGU="ms", PREC=3)
        p["WAVEFORM_POINTS"] = builder.longIn(
            "WAVEFORM_POINTS", LOPR=0, HOPR=WAVEFORM_POINTS
        )
        p["VLOW_V"] = builder.aIn("VLOW_V", EGU="V", PREC=4)
        p["VHIGH_V"] = builder.aIn("VHIGH_V", EGU="V", PREC=4)

        p["PLAN_VALID"] = builder.boolIn("PLAN_VALID", ZNAM="Invalid", ONAM="Valid")
        p["SESSION_ID"] = builder.stringIn("SESSION_ID")
        p["ARCHIVE_STATUS"] = builder.stringIn("ARCHIVE_STATUS")
        p["STATE"] = builder.stringIn("STATE")
        p["REASON"] = builder.longStringIn("REASON", length=256)

        p["GENERATE_LOAD_CMD"] = builder.boolOut(
            "GENERATE_LOAD_CMD",
            ZNAM="IDLE",
            ONAM="GENERATE_LOAD",
            always_update=True,
            on_update_name=self._generate_load_command,
        )

        objective = str(p["OBJECTIVE_CMD"].get())
        if objective not in ALLOWED_OBJECTIVES:
            objective = "Enhancement"
            p["OBJECTIVE_CMD"].set(objective, process=False)
        p["OBJECTIVE_RBV"].set(objective)

        p["FITTER_CONNECTED"].set(0)
        p["LATEST_FIT_VALID"].set(0)
        p["BURN_MODE_RBV"].set(self.burn_mode.upper())
        p["PLAN_VALID"].set(0)
        p["SESSION_ID"].set("")
        p["ARCHIVE_STATUS"].set("IDLE")
        p["WAVEFORM_POINTS"].set(0)
        p["CURRENT_POLARIZATION"].set(0.0)
        p["REFERENCE_POLARIZATION"].set(0.0)
        p["FM_PERIOD_S"].set(0.0)
        p["SWEEP_STEP_DB"].set(0.0)
        p["SWEEP_DWELL_MS"].set(0.0)
        p["VLOW_V"].set(0.0)
        p["VHIGH_V"].set(0.0)
        p["GENERATE_LOAD_CMD"].set(0, process=False)
        if self.burn_mode == "fit":
            self._set_status("WAITING_FOR_FIT", "Waiting for calibrated fitter result")
        else:
            self._set_status("OPERATOR_MODE", "Reference waveform; fitter calibration not required")

    def connect(self):
        self._subscriber_thread = threading.Thread(
            target=self._subscriber_loop,
            name="ssrf-burn-recipe-fitter-subscriber",
            daemon=True,
        )
        self._subscriber_thread.start()
        return True

    def _set_status(self, state, reason):
        self.pvs["STATE"].set(str(state)[:39])
        self.pvs["REASON"].set(str(reason)[:255])

    def _emit(self, event_type, data=None, *, level="INFO"):
        session_id = str(self._active_session_id).strip()
        if not session_id:
            return None
        try:
            record = self._event_logger.append(
                event_type=event_type,
                source="burn_recipe_ioc",
                session_id=session_id,
                level=level,
                data=data or {},
            )
            self.pvs["ARCHIVE_STATUS"].set("OK")
            return record
        except Exception as exc:
            self.pvs["ARCHIVE_STATUS"].set("EVENT_LOG_ERROR")
            print(f"ssRF event logging failed: {exc}")
            return None

    def _recipe_event_data(self):
        return {
            "burn_mode": self.burn_mode,
            "objective": str(self.pvs["OBJECTIVE_RBV"].get()),
            "material": str(self.pvs["MATERIAL"].get()),
            "current_p_pct": float(self.pvs["CURRENT_POLARIZATION"].get()),
            "reference_p_pct": float(self.pvs["REFERENCE_POLARIZATION"].get()),
            "carrier_mhz": float(self.pvs["CARRIER_MHZ"].get()),
            "start_level_dbm": float(self.pvs["START_LEVEL_DBM"].get()),
            "target_level_dbm": float(self.pvs["TARGET_LEVEL_DBM"].get()),
            "fm_deviation_khz": float(self.pvs["FM_DEV_KHZ"].get()),
            "fm_repeats": int(self.pvs["FM_REPEATS"].get()),
            "fm_period_s": float(self.pvs["FM_PERIOD_S"].get()),
            "sweep_dwell_ms": float(self.pvs["SWEEP_DWELL_MS"].get()),
            "waveform_points": int(self.pvs["WAVEFORM_POINTS"].get()),
            "vlow_v": float(self.pvs["VLOW_V"].get()),
            "vhigh_v": float(self.pvs["VHIGH_V"].get()),
        }

    def _objective_command(self, new_value, _pv):
        value = str(new_value).strip().capitalize()
        if value not in ALLOWED_OBJECTIVES:
            self.pvs["OBJECTIVE_CMD"].set(
                self.pvs["OBJECTIVE_RBV"].get(), process=False
            )
            self._set_status("INVALID_RECIPE", "Objective must be Enhancement, Suppression, or Zero")
            return
        self.pvs["OBJECTIVE_RBV"].set(value)
        self.pvs["OBJECTIVE_CMD"].set(value, process=False)
        self.pvs["PLAN_VALID"].set(0)
        self._set_status("RECIPE_CHANGED", "Generate/load required")

    def _generate_load_command(self, new_value, _pv):
        self.pvs["GENERATE_LOAD_CMD"].set(0, process=False)
        if not bool(new_value):
            return
        if self._generate_requested or (
            self._generate_task is not None and not self._generate_task.done()
        ):
            self._set_status("BUSY", "Generate/load already active")
            return
        self._generate_requested = True
        self.pvs["PLAN_VALID"].set(0)
        self._set_status("REQUESTED", "Generate/load requested")

    def _subscriber_loop(self):
        while not self._stop.is_set():
            try:
                with socket.create_connection(
                    (self.publish_host, self.publish_port), timeout=2.0
                ) as sock:
                    sock.settimeout(1.0)
                    self.pvs["FITTER_CONNECTED"].set(1)
                    buffer = b""

                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(65536)
                        except socket.timeout:
                            continue

                        if not chunk:
                            raise ConnectionError("fitter publisher closed")

                        buffer += chunk
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            raw = raw.strip()
                            if not raw:
                                continue
                            msg = json.loads(raw.decode("utf-8", "replace"))
                            if msg.get("type") != "fit":
                                continue
                            with self._latest_lock:
                                self._latest_message = msg
                                self._latest_received = time.monotonic()
            except Exception:
                self.pvs["FITTER_CONNECTED"].set(0)
                self._stop.wait(1.0)

    def _latest_snapshot(self):
        with self._latest_lock:
            msg = None if self._latest_message is None else dict(self._latest_message)
            received = self._latest_received
        age = math.inf if not received else max(0.0, time.monotonic() - received)
        return msg, age

    @staticmethod
    async def _caput(pv, value, timeout=5.0):
        await aioca.caput(pv, value, wait=True, timeout=timeout)

    @staticmethod
    async def _caget(pv, timeout=3.0):
        return await aioca.caget(pv, timeout=timeout)

    async def _wait_for(self, pv, predicate, *, timeout_s, interval_s=0.05):
        deadline = time.monotonic() + float(timeout_s)
        last = None
        while time.monotonic() < deadline:
            last = await self._caget(pv)
            if predicate(last):
                return last
            await asyncio.sleep(interval_s)
        raise RuntimeError(f"timeout waiting for {pv}; last={last!r}")

    async def _generate_and_prepare(self):
        self._generate_requested = False
        try:
            msg, age = self._latest_snapshot()
            objective = str(self.pvs["OBJECTIVE_RBV"].get())
            if self.burn_mode == "fit":
                if msg is None:
                    raise RuntimeError("no fitter result available")
                if age > self.max_fit_age_s:
                    raise RuntimeError(f"latest fitter result is stale ({age:.1f} s)")
                if msg.get("constants_good") is not True:
                    raise RuntimeError("fitter constants are not calibrated")
                p_fit_pct = msg.get("P_fit")
                if p_fit_pct is None or not math.isfinite(float(p_fit_pct)):
                    raise RuntimeError("latest fit has no valid P_fit polarization")
                snapshot = FitSnapshot.from_message(msg)
                reference_p = float(deuts.ratio_method(float(snapshot.constants["r"])))
                current_p = float(p_fit_pct) / 100.0
                plan = build_waveform_plan(
                    snapshot, case=objective, polarization=current_p,
                    high_polarization=reference_p, v_edge_v=PHYSICAL_VHIGH_V,
                )
            else:
                plan = build_operator_waveform_plan(
                    material=self.operator_material, case=objective,
                    polarization=self.operator_polarization,
                    center_mhz=self.operator_center_mhz,
                )
                reference_p = current_p = self.operator_polarization
                p_fit_pct = 100.0 * current_p
                msg = {"material": plan.material, "fit_type": "OPERATOR_REFERENCE"}
            self._active_session_id = new_session_id(subsystem="ssrf")
            self.pvs["SESSION_ID"].set(self._active_session_id[:39])
            self.pvs["ARCHIVE_STATUS"].set("STARTED")
            self._emit(
                "recipe_requested",
                {
                    "burn_mode": self.burn_mode,
                    "objective": objective,
                    "fit_ts": str(msg.get("ts") or ""),
                    "fit_type": str(msg.get("fit_type") or ""),
                    "material": str(msg.get("material") or ""),
                    "current_p_pct": float(p_fit_pct),
                    "reference_p_pct": 100.0 * reference_p,
                },
            )
            self._set_status("PLANNING", "Building 8192-point waveform")
            carrier = float(self.pvs["CARRIER_MHZ"].get())
            start_level = float(self.pvs["START_LEVEL_DBM"].get())
            target_level = float(self.pvs["TARGET_LEVEL_DBM"].get())
            fm_dev = float(self.pvs["FM_DEV_KHZ"].get())
            repeats = int(self.pvs["FM_REPEATS"].get())
            if repeats < 1:
                raise RuntimeError("FM repeats must be >= 1")
            if target_level <= start_level:
                raise RuntimeError("target RF level must exceed start RF level")

            dwell_ms = repeats * float(plan.period_s) * 1000.0
            step_db = target_level - start_level

            self.pvs["MATERIAL"].set(str(plan.material)[:39])
            self.pvs["FIT_TYPE"].set(str(plan.fit_type or "")[:39])
            self.pvs["FIT_TS"].set(str(plan.fit_ts or "")[:39])
            self.pvs["CURRENT_POLARIZATION"].set(current_p * 100.0)
            self.pvs["REFERENCE_POLARIZATION"].set(reference_p * 100.0)
            self.pvs["FM_PERIOD_S"].set(float(plan.period_s))
            self.pvs["SWEEP_STEP_DB"].set(step_db)
            self.pvs["SWEEP_DWELL_MS"].set(dwell_ms)
            self.pvs["WAVEFORM_POINTS"].set(int(plan.waveform_points))
            self.pvs["VLOW_V"].set(PHYSICAL_VLOW_V)
            self.pvs["VHIGH_V"].set(PHYSICAL_VHIGH_V)

            fit_data_path = None
            waveform_data_path = None
            try:
                products = self._session_writer.write_products(
                    session_id=self._active_session_id,
                    message=msg,
                    plan=plan,
                )
                fit_data_path = products.fit_data_path
                waveform_data_path = products.waveform_data_path
                self.pvs["ARCHIVE_STATUS"].set("DATA_SAVED")
            except Exception as archive_exc:
                self.pvs["ARCHIVE_STATUS"].set("DATA_SAVE_ERROR")
                self._emit(
                    "session_data_warning",
                    {"error": str(archive_exc)},
                    level="WARNING",
                )

            self._emit(
                "waveform_planned",
                {
                    **self._recipe_event_data(),
                    "fit_ts": str(plan.fit_ts or ""),
                    "fit_type": str(plan.fit_type or ""),
                    "fit_data_path": fit_data_path,
                    "waveform_data_path": waveform_data_path,
                    "used_measured_heights": bool(plan.used_measured_heights),
                },
            )

            # Recipe/scalar writes modify sequencer IOC memory only.
            scalar_values = {
                "CARRIER_MHZ": carrier,
                "START_LEVEL_DBM": start_level,
                "TARGET_LEVEL_DBM": target_level,
                "FM_DEV_KHZ": fm_dev,
                "FM_PERIOD_S": float(plan.period_s),
                "VHIGH_V": PHYSICAL_VHIGH_V,
                "VLOW_V": PHYSICAL_VLOW_V,
                "FM_REPEATS": repeats,
            }
            for name, value in scalar_values.items():
                await self._caput(f"{SEQ}:{name}", value)

            baseline_stage_id = int(await self._caget(f"{SEQ}:WAVEFORM_STAGE_ID"))
            self._set_status("STAGING", "Submitting waveform to sequencer")
            stager = WaveformStager(write=self._caput)
            receipt = await stager.stage(plan)
            self._emit(
                "waveform_staged",
                {
                    "pv": receipt.pv,
                    "points": receipt.points,
                    "minimum": receipt.minimum,
                    "maximum": receipt.maximum,
                    "material": receipt.material,
                    "case": receipt.case,
                    "fit_ts": receipt.fit_ts,
                },
            )

            await self._wait_for(
                f"{SEQ}:WAVEFORM_STAGE_ID",
                lambda value: int(value) > baseline_stage_id,
                timeout_s=8.0,
            )
            points = int(await self._caget(f"{SEQ}:WAVEFORM_STAGE_POINTS"))
            if points != WAVEFORM_POINTS:
                raise RuntimeError(f"sequencer staged {points}, expected {WAVEFORM_POINTS}")

            self._set_status("PREPARING", "Configuring source and loading Keysight ARB")
            await self._caput(f"{SEQ}:PREPARE_CMD", 1)
            await self._wait_for(
                f"{SEQ}:PREPARED",
                lambda value: int(value) == 1,
                timeout_s=20.0,
                interval_s=0.10,
            )

            self.pvs["PLAN_VALID"].set(1)
            self._emit("source_prepared", self._recipe_event_data())
            self._set_status("READY_TO_ARM", "Waveform loaded; ARM then BURN ONCE")

        except Exception as exc:
            self.pvs["PLAN_VALID"].set(0)
            self._emit("recipe_failed", {"error": str(exc)}, level="ERROR")
            self._set_status("FAILED", str(exc))
        finally:
            self._generate_task = None

    async def do_reads(self):
        msg, age = self._latest_snapshot()
        self.pvs["FIT_AGE_S"].set(0.0 if not math.isfinite(age) else age)

        valid = False
        if msg is not None and self.burn_mode == "fit":
            self.pvs["MATERIAL"].set(str(msg.get("material") or "")[:39])
            self.pvs["FIT_TYPE"].set(str(msg.get("fit_type") or "")[:39])
            self.pvs["FIT_TS"].set(str(msg.get("ts") or "")[:39])

            p_fit = msg.get("P_fit")
            constants = msg.get("constants") or {}
            try:
                if p_fit is not None and math.isfinite(float(p_fit)):
                    self.pvs["CURRENT_POLARIZATION"].set(float(p_fit))
                r_ref = constants.get("r")
                if r_ref is not None and math.isfinite(float(r_ref)):
                    self.pvs["REFERENCE_POLARIZATION"].set(
                        100.0 * float(deuts.ratio_method(float(r_ref)))
                    )
                valid = (
                    msg.get("constants_good") is True
                    and p_fit is not None
                    and math.isfinite(float(p_fit))
                    and age <= self.max_fit_age_s
                )
            except Exception:
                valid = False

        self.pvs["LATEST_FIT_VALID"].set(int(valid))

        if self._active_session_id:
            try:
                exec_state = str(await self._caget(f"{SEQ}:EXECUTION_STATE", timeout=1.0))
                if exec_state != self._last_execution_state:
                    reason = str(await self._caget(f"{SEQ}:EXECUTION_REASON", timeout=1.0))
                    self._emit(
                        "sequencer_state",
                        {"state": exec_state, "reason": reason},
                        level=("ERROR" if exec_state in ("FAILED", "CLEANUP_FAILED") else "INFO"),
                    )
                    self._last_execution_state = exec_state
            except Exception:
                pass

        if self._generate_requested and self._generate_task is None:
            self._generate_task = asyncio.create_task(self._generate_and_prepare())

        return True
