"""Genesis Phase-I physical no-RF ssRF demonstration IOC.

Purpose
-------
Provide a GUI-only demonstration of the complete Phase-I software path:

offline NMR -> fitter -> adaptive WaveformPlan -> physical Keysight ARB load
-> physical NMR/ssRF switch sequence -> safe NMR restore.

Hard safety boundary
--------------------
This IOC has NO SMC write methods and never commands Keysight OUTPUT ON.
It only:
- reads the physical SMC and requires RF=OFF and FM=OFF,
- stages/loads an 8192-point waveform to the physical Keysight while output OFF,
- moves the two physical Mini-Circuits switches in safe order.

The production burn sequencer and its ARM semantics are unchanged.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import numpy as np
from aioca import caget, caput
from softioc import builder, softioc, asyncio_dispatcher

from ssrf_ioc.minicircuits_switch import MiniCircuitsSwitchConnection
from ssrf_ioc.smc100a import DeviceConnection as SmcConnection

ROOT = Path(__file__).resolve().parents[1]

PREFIX = "UNH:GEN:SSRF:SEQ"
FIT = "UNH:GEN:SSRF:FIT"
WGEN = "UNH:GEN:SSRF:WGEN"

NMR_IP = "192.168.1.8"
SSRF_IP = "192.168.1.11"
SMC_IP = "192.168.1.16"

REPLAY_FILE = ROOT / "test_data/2026-06-Cooldown/data_record_2026-06-17.csv"
MAX_POINTS = 8192


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _ca_get(pv: str, timeout: float = 5.0):
    async def run():
        return await caget(pv, timeout=timeout)
    return asyncio.run(run())


def _ca_put(pv: str, value, timeout: float = 10.0):
    async def run():
        await caput(pv, value, wait=True, timeout=timeout)
    asyncio.run(run())


class DemoIOC:
    def __init__(self):
        self.pvs = {}
        self.lock = threading.RLock()
        self.busy = False
        self.abort_event = threading.Event()
        self.fitter_proc = None
        self.fitter_log = None

        self.case = "Enhancement"
        self.polarization = 0.30
        self.high_polarization = 0.30

        self.waveform_loaded = False
        self.stage_id = 0
        self.no_rf_lock = False
        self.nmr_mask = 0
        self.ssrf_mask = 3
        self.wgen_output = 0
        self.wgen_points = 0
        self.wgen_ready = 0
        self.execution_armed = False

        self._create_pvs()
        self._set_state("IDLE", "Physical no-RF demo ready")
        self._set_stage("IDLE", "No fitter waveform loaded")

    # --------------------------------------------------------------
    # PVs
    # --------------------------------------------------------------

    def _create_pvs(self):
        p = self.pvs

        # Momentary workflow commands.
        p["START_FITTER_CMD"] = builder.boolOut(
            "START_FITTER_CMD", ZNAM="IDLE", ONAM="START FITTER",
            always_update=True, on_update_name=self._cmd_start_fitter)
        p["REPLAY_EQ_CMD"] = builder.boolOut(
            "REPLAY_EQ_CMD", ZNAM="IDLE", ONAM="REPLAY EQ",
            always_update=True, on_update_name=self._cmd_replay_eq)
        p["UPDATE_CONSTANTS_CMD"] = builder.boolOut(
            "UPDATE_CONSTANTS_CMD", ZNAM="IDLE", ONAM="UPDATE CONSTANTS",
            always_update=True, on_update_name=self._cmd_update_constants)
        p["SET_SSRF_MODE_CMD"] = builder.boolOut(
            "SET_SSRF_MODE_CMD", ZNAM="IDLE", ONAM="ssRF MODE",
            always_update=True, on_update_name=self._cmd_ssrf_mode)
        p["PREPARE_CMD"] = builder.boolOut(
            "PREPARE_CMD", ZNAM="IDLE", ONAM="PREPARE / LOAD",
            always_update=True, on_update_name=self._cmd_prepare)
        p["ARM_CMD"] = builder.boolOut(
            "ARM_CMD", ZNAM="IDLE", ONAM="ARM",
            always_update=True, on_update_name=self._cmd_arm)
        p["DISARM_CMD"] = builder.boolOut(
            "DISARM_CMD", ZNAM="IDLE", ONAM="DISARM",
            always_update=True, on_update_name=self._cmd_disarm)
        p["START_CMD"] = builder.boolOut(
            "START_CMD", ZNAM="IDLE", ONAM="BLAST / BURN",
            always_update=True, on_update_name=self._cmd_blast)
        p["DEMO_RUN_CMD"] = builder.boolOut(
            "DEMO_RUN_CMD", ZNAM="IDLE", ONAM="RUN DEMO",
            always_update=True, on_update_name=self._cmd_run_demo)
        p["ABORT_CMD"] = builder.boolOut(
            "ABORT_CMD", ZNAM="IDLE", ONAM="ABORT",
            always_update=True, on_update_name=self._cmd_abort)
        p["STOP_FITTER_CMD"] = builder.boolOut(
            "STOP_FITTER_CMD", ZNAM="IDLE", ONAM="STOP FITTER",
            always_update=True, on_update_name=self._cmd_stop_fitter)

        # Operator parameters used by the existing adaptive planner runtime.
        p["CASE_CMD"] = builder.stringOut(
            "CASE_CMD", initial_value=self.case,
            on_update_name=self._set_case)
        p["POLARIZATION"] = builder.aOut(
            "POLARIZATION", initial_value=self.polarization,
            DRVL=-1.0, DRVH=1.0, PREC=4,
            on_update_name=self._set_pol)
        p["HIGH_POLARIZATION"] = builder.aOut(
            "HIGH_POLARIZATION", initial_value=self.high_polarization,
            DRVL=0.0, DRVH=1.0, PREC=4,
            on_update_name=self._set_high_pol)

        # Existing fitter-runtime target. This is intentionally the same
        # sequencer staging boundary already used in the repository.
        p["WAVEFORM_STAGE_REQ"] = builder.WaveformOut(
            "WAVEFORM_STAGE_REQ", length=MAX_POINTS, datatype="float32",
            on_update_name=self._stage_waveform)

        p["WAVEFORM_STAGE_STATE"] = builder.stringIn("WAVEFORM_STAGE_STATE")
        p["WAVEFORM_STAGE_REASON"] = builder.longStringIn(
            "WAVEFORM_STAGE_REASON", length=256)
        p["WAVEFORM_STAGE_ID"] = builder.longIn("WAVEFORM_STAGE_ID", LOPR=0)

        p["DEMO_STATE"] = builder.stringIn("DEMO_STATE")
        p["DEMO_REASON"] = builder.longStringIn("DEMO_REASON", length=256)
        p["DEMO_BUSY"] = builder.boolIn("DEMO_BUSY", ZNAM="IDLE", ONAM="BUSY")
        p["DEMO_READY"] = builder.boolIn("DEMO_READY", ZNAM="BLOCKED", ONAM="READY")
        p["EXECUTION_ARMED"] = builder.boolIn(
            "EXECUTION_ARMED", ZNAM="DISARMED", ONAM="ARMED")
        p["EXECUTION_STATE"] = builder.stringIn("EXECUTION_STATE")
        p["EXECUTION_REASON"] = builder.longStringIn(
            "EXECUTION_REASON", length=256)
        p["EXECUTION_MODE"] = builder.stringIn("EXECUTION_MODE")

        p["FITTER_UP"] = builder.boolIn("FITTER_UP", ZNAM="DOWN", ONAM="RUNNING")
        p["FITTER_CALIBRATED"] = builder.boolIn(
            "FITTER_CALIBRATED",
            ZNAM="NOT CALIBRATED",
            ONAM="CALIBRATED",
        )
        p["FIT_MODE"] = builder.stringIn("FIT_MODE")
        p["NO_RF_LOCK"] = builder.boolIn("NO_RF_LOCK", ZNAM="BLOCKED", ONAM="RF/FM OFF")
        p["SMC_RF"] = builder.boolIn("SMC_RF", ZNAM="OFF", ONAM="ON")
        p["SMC_FM"] = builder.boolIn("SMC_FM", ZNAM="OFF", ONAM="ON")
        p["NMR_MASK"] = builder.longIn("NMR_MASK", LOPR=0, HOPR=3)
        p["SSRF_MASK"] = builder.longIn("SSRF_MASK", LOPR=0, HOPR=3)

        p["WGEN_OUTPUT"] = builder.boolIn("WGEN_OUTPUT", ZNAM="OFF", ONAM="ON")
        p["WGEN_POINTS"] = builder.longIn("WGEN_POINTS", LOPR=0, HOPR=MAX_POINTS)
        p["WGEN_READY"] = builder.boolIn("WGEN_READY", ZNAM="NOT READY", ONAM="READY")
        p["WAVEFORM_LOADED"] = builder.boolIn(
            "WAVEFORM_LOADED", ZNAM="NOT LOADED", ONAM="LOADED")

        p["DEMO_BUSY"].set(0)
        p["DEMO_READY"].set(0)
        p["EXECUTION_ARMED"].set(0)
        p["EXECUTION_STATE"].set("IDLE")
        p["EXECUTION_REASON"].set("Physical no-RF commissioning idle")
        p["EXECUTION_MODE"].set("PHYSICAL_NO_RF")
        p["FITTER_UP"].set(0)
        p["FITTER_CALIBRATED"].set(0)
        p["FIT_MODE"].set("STOPPED")
        p["NO_RF_LOCK"].set(0)
        p["SMC_RF"].set(0)
        p["SMC_FM"].set(0)
        p["NMR_MASK"].set(0)
        p["SSRF_MASK"].set(3)
        p["WGEN_OUTPUT"].set(0)
        p["WGEN_POINTS"].set(0)
        p["WGEN_READY"].set(0)
        p["WAVEFORM_LOADED"].set(0)
        p["WAVEFORM_STAGE_ID"].set(0)

    # --------------------------------------------------------------
    # Common helpers
    # --------------------------------------------------------------

    def _pv_name(self, pv) -> str:
        return str(pv).rsplit(":", 1)[-1]

    def _reset_cmd(self, pv):
        name = self._pv_name(pv)
        if name in self.pvs:
            self.pvs[name].set(0, process=False)

    def _set_state(self, state: str, reason: str):
        self.pvs["DEMO_STATE"].set(str(state)[:39])
        self.pvs["DEMO_REASON"].set(str(reason)[:255])

    def _set_stage(self, state: str, reason: str):
        self.pvs["WAVEFORM_STAGE_STATE"].set(str(state)[:39])
        self.pvs["WAVEFORM_STAGE_REASON"].set(str(reason)[:255])

    def _launch(self, label: str, fn):
        with self.lock:
            if self.busy:
                self._set_state("BUSY", f"Cannot start {label}; another demo task is active")
                return
            self.busy = True
            self.pvs["DEMO_BUSY"].set(1)

        def runner():
            try:
                fn()
            except Exception as exc:
                self._set_state("ERROR", f"{label}: {exc}")
            finally:
                with self.lock:
                    self.busy = False
                    self.pvs["DEMO_BUSY"].set(0)

        threading.Thread(target=runner, daemon=True, name=f"demo-{label}").start()

    # --------------------------------------------------------------
    # Physical safety/readback
    # --------------------------------------------------------------

    def _read_smc(self):
        smc = SmcConnection(SMC_IP, port=5025, timeout=2.0, settle=0.0)
        try:
            return smc.read_state()
        finally:
            smc.close()

    def _assert_no_rf(self):
        state = self._read_smc()
        rf = bool(state["rf_on"])
        fm = bool(state["fm_on"])
        self.no_rf_lock = (not rf) and (not fm)
        self.pvs["SMC_RF"].set(int(rf))
        self.pvs["SMC_FM"].set(int(fm))
        self.pvs["NO_RF_LOCK"].set(int(self.no_rf_lock))
        if rf or fm:
            raise RuntimeError(f"physical SMC is not safe: RF={rf}, FM={fm}")
        return state

    def _read_switches(self):
        nmr = MiniCircuitsSwitchConnection(NMR_IP, 80, 2.0)
        ssrf = MiniCircuitsSwitchConnection(SSRF_IP, 80, 2.0)
        try:
            nmr_mask = int(nmr.read_port_mask())
            ssrf_mask = int(ssrf.read_port_mask())
            return nmr_mask, ssrf_mask
        finally:
            for sw in (nmr, ssrf):
                close = getattr(sw, "close", None)
                if callable(close):
                    close()

    def _read_wgen(self):
        output = int(float(_ca_get(f"{WGEN}:OUTPUT_RBV", 4.0)))
        points = int(float(_ca_get(f"{WGEN}:ARB_POINTS_RBV", 4.0)))
        ready = int(float(_ca_get(f"{WGEN}:WAVEFORM_READY", 4.0)))
        self.wgen_output = output
        self.wgen_points = points
        self.wgen_ready = ready
        self.pvs["WGEN_OUTPUT"].set(output)
        self.pvs["WGEN_POINTS"].set(points)
        self.pvs["WGEN_READY"].set(ready)
        return output, points, ready

    def poll_once(self):
        try:
            self._assert_no_rf()
        except Exception:
            self.pvs["NO_RF_LOCK"].set(0)

        try:
            nmr, ssrf = self._read_switches()
            self.nmr_mask = nmr
            self.ssrf_mask = ssrf
            self.pvs["NMR_MASK"].set(nmr)
            self.pvs["SSRF_MASK"].set(ssrf)
        except Exception:
            pass

        try:
            output, points, ready = self._read_wgen()
        except Exception:
            output, points, ready = 1, 0, 0

        fitter_up = int(_port_open(8777) and _port_open(8778))
        self.pvs["FITTER_UP"].set(fitter_up)

        if fitter_up:
            try:
                calibrated = int(
                    float(
                        _ca_get(
                            f"{FIT}:CALIBRATED",
                            3.0,
                        )
                    )
                )

                mode = str(
                    _ca_get(
                        f"{FIT}:MODE_RBV",
                        3.0,
                    )
                )

                self.pvs[
                    "FITTER_CALIBRATED"
                ].set(calibrated)

                self.pvs[
                    "FIT_MODE"
                ].set(mode[:39])

            except Exception:
                self.pvs[
                    "FITTER_CALIBRATED"
                ].set(0)

                self.pvs[
                    "FIT_MODE"
                ].set("UNKNOWN")

        else:
            self.pvs[
                "FITTER_CALIBRATED"
            ].set(0)

            self.pvs[
                "FIT_MODE"
            ].set("STOPPED")

        demo_ready = (
            fitter_up == 1
            and self.waveform_loaded
            and self.no_rf_lock
            and output == 0
            and points == MAX_POINTS
            and ready == 1
            and self.nmr_mask == 0
            and self.ssrf_mask == 3
        )
        self.pvs["DEMO_READY"].set(int(demo_ready))

    # --------------------------------------------------------------
    # Parameter callbacks
    # --------------------------------------------------------------

    def _set_case(self, value, pv):
        value = str(value).strip()
        if value not in ("Enhancement", "Suppression", "Zero"):
            self.pvs["CASE_CMD"].set(self.case, process=False)
            self._set_state("ERROR", "Case must be Enhancement, Suppression, or Zero")
            return
        self.case = value

    def _set_pol(self, value, pv):
        self.polarization = float(value)

    def _set_high_pol(self, value, pv):
        self.high_polarization = float(value)

    # --------------------------------------------------------------
    # Workflow command callbacks
    # --------------------------------------------------------------

    def _cmd_start_fitter(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("start-fitter", self.start_fitter)

    def _cmd_replay_eq(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("replay-eq", self.replay_equilibrium)

    def _cmd_update_constants(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("update-constants", self.update_constants)

    def _cmd_ssrf_mode(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("ssrf-mode", self.set_ssrf_mode)

    def _cmd_prepare(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("prepare", self.prepare_waveform)

    def _cmd_arm(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("arm", self.arm_demo)

    def _cmd_disarm(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)
            self._set_state("DISARMED", "Demo execution disarmed")

    def _cmd_blast(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("blast", self.blast_workflow)

    def _cmd_run_demo(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("run-demo", self.run_switch_demo)

    def _cmd_abort(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)
            self.abort_event.set()
            self._set_state(
                "ABORT REQUESTED",
                "Execution disarmed; active switch demo will restore safe routing",
            )

    def _cmd_stop_fitter(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("stop-fitter", self.stop_fitter)

    # --------------------------------------------------------------
    # Fitter workflow
    # --------------------------------------------------------------

    def start_fitter(self):
        if _port_open(8777) or _port_open(8778):
            self.pvs["FITTER_UP"].set(1)
            self._set_state("FITTER READY", "Fitter already running")
            return

        self._set_state("START FITTER", "Starting authoritative EPICS fitter")

        self.fitter_log = open("/tmp/genesis_ssrf_demo_fitter.log", "a", buffering=1)
        env = os.environ.copy()
        env["EPICS_CAS_SERVER_PORT"] = "5064"
        env["EPICS_PVA_SERVER_PORT"] = "5076"

        self.fitter_proc = subprocess.Popen(
            [
                sys.executable, "-u", "-m", "online_fitter.epics_run",
                "--prefix", FIT,
                "--material", "d-butanol",
                "--mode", "equilibrium",
                "--eq-buffer", "10",
                "--replace",
            ],
            cwd=ROOT,
            env=env,
            stdout=self.fitter_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        deadline = time.time() + 15.0
        while time.time() < deadline:
            if _port_open(8777) and _port_open(8778):
                self.pvs["FITTER_UP"].set(1)
                self._set_state("FITTER READY", "Fitter running in equilibrium mode")
                return
            time.sleep(0.2)

        raise RuntimeError("fitter failed to start; see /tmp/genesis_ssrf_demo_fitter.log")

    def stop_fitter(self):
        if self.fitter_proc is not None and self.fitter_proc.poll() is None:
            self.fitter_proc.terminate()
            try:
                self.fitter_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.fitter_proc.kill()
        self.fitter_proc = None
        self.pvs["FITTER_UP"].set(0)
        self.pvs["FITTER_CALIBRATED"].set(0)
        self.pvs["FIT_MODE"].set("STOPPED")
        self._set_state("IDLE", "Fitter stopped")

    def _replay(self, *, limit: int, polarization_only: bool):
        if not _port_open(8777):
            raise RuntimeError("fitter input 8777 is not running")

        cmd = [
            sys.executable, "-u", "-m", "online_fitter.mock_labview_client",
            "--path", str(REPLAY_FILE),
            "--rate", "20",
            "--limit", str(int(limit)),
        ]
        if polarization_only:
            cmd.append("--polarization-only")

        subprocess.run(cmd, cwd=ROOT, check=True, timeout=60)

    def replay_equilibrium(self):
        self._set_state("REPLAY EQ", "Replaying 60 offline NMR sweeps")
        self._replay(limit=60, polarization_only=False)
        self._set_state("EQ READY", "Offline NMR replay complete; update constants next")

    def update_constants(self):
        if not _port_open(8777):
            raise RuntimeError("fitter is not running")

        self._set_state("CALIBRATING", "Running fitter Update Constants")
        _ca_put(f"{FIT}:UPDATE_CONSTANTS_CMD", 1, 5.0)

        deadline = time.time() + 35.0
        while time.time() < deadline:
            calibrated = int(float(_ca_get(f"{FIT}:CALIBRATED", 5.0)))
            if calibrated == 1:
                source = str(_ca_get(f"{FIT}:CONSTANTS_SOURCE", 5.0))
                self._set_state("CALIBRATED", f"Fitter constants calibrated; source={source}")
                return
            time.sleep(0.5)

        warning = str(_ca_get(f"{FIT}:WARNING", 5.0))
        raise RuntimeError(f"CALIBRATED did not become 1; fitter warning={warning}")

    def set_ssrf_mode(self):
        if not _port_open(8777):
            raise RuntimeError("fitter is not running")

        _ca_put(f"{FIT}:MODE_CMD", "ssRF", 5.0)
        time.sleep(0.5)
        mode = str(_ca_get(f"{FIT}:MODE_RBV", 5.0))
        if mode.lower() != "ssrf":
            raise RuntimeError(f"fitter mode readback is {mode!r}, not ssRF")
        self._set_state("ssRF MODE", "Fitter mode = ssRF")

    # --------------------------------------------------------------
    # Existing fitter runtime -> demo staging boundary -> physical WGEN
    # --------------------------------------------------------------

    def _stage_waveform(self, value, pv):
        values = np.asarray(value, dtype=np.float32).reshape(-1)

        def work():
            try:
                if values.size != MAX_POINTS:
                    raise RuntimeError(f"expected 8192 points, got {values.size}")
                if not np.all(np.isfinite(values)):
                    raise RuntimeError("waveform contains non-finite samples")
                if np.any(values < -1.0) or np.any(values > 1.0):
                    raise RuntimeError("waveform outside normalized [-1,1]")

                self._set_stage("STAGING", "Forwarding fitter waveform to physical Keysight")
                self._assert_no_rf()

                output = int(float(_ca_get(f"{WGEN}:OUTPUT_RBV", 5.0)))
                writable = int(float(_ca_get(f"{WGEN}:WRITE_ENABLED", 5.0)))
                if output != 0:
                    raise RuntimeError("physical WaveGen output is ON")
                if writable != 1:
                    raise RuntimeError("physical upload-only WaveGen IOC is not writable")

                before = int(float(_ca_get(f"{WGEN}:WAVEFORM_STAGE_ID", 5.0)))
                _ca_put(f"{WGEN}:WAVEFORM_CMD", values, 25.0)

                deadline = time.time() + 8.0
                while time.time() < deadline:
                    sid = int(float(_ca_get(f"{WGEN}:WAVEFORM_STAGE_ID", 5.0)))
                    pts = int(float(_ca_get(f"{WGEN}:WAVEFORM_POINTS", 5.0)))
                    if sid > before and pts == MAX_POINTS:
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("physical WaveGen did not acknowledge 8192-point stage")

                # Explicit hardware commit. Upload-only profile still blocks OUTPUT_CMD.
                _ca_put(f"{WGEN}:LOAD_CMD", 1, 60.0)

                deadline = time.time() + 20.0
                while time.time() < deadline:
                    arb = int(float(_ca_get(f"{WGEN}:ARB_POINTS_RBV", 5.0)))
                    ready = int(float(_ca_get(f"{WGEN}:WAVEFORM_READY", 5.0)))
                    output = int(float(_ca_get(f"{WGEN}:OUTPUT_RBV", 5.0)))
                    if arb == MAX_POINTS and ready == 1 and output == 0:
                        self.waveform_loaded = True
                        self.stage_id += 1
                        self.pvs["WAVEFORM_STAGE_ID"].set(self.stage_id)
                        self.pvs["WAVEFORM_LOADED"].set(1)
                        self.pvs["WGEN_POINTS"].set(arb)
                        self.pvs["WGEN_READY"].set(ready)
                        self.pvs["WGEN_OUTPUT"].set(output)
                        self._set_stage(
                            "LOADED",
                            "8192-point fitter waveform loaded to physical Keysight; output OFF",
                        )
                        return
                    time.sleep(0.2)

                raise RuntimeError("physical WaveGen LOAD did not reach 8192/READY/OFF")

            except Exception as exc:
                self.waveform_loaded = False
                self.pvs["WAVEFORM_LOADED"].set(0)
                self._set_stage("FAILED", str(exc))
                self._set_state("ERROR", f"Waveform stage/load failed: {exc}")

        threading.Thread(target=work, daemon=True, name="demo-wgen-stage").start()

    def prepare_waveform(self, preserve_arm=False):
        if not preserve_arm:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)

        if not _port_open(8778):
            raise RuntimeError("fitter publisher 8778 is not running")

        calibrated = int(float(_ca_get(f"{FIT}:CALIBRATED", 5.0)))
        if calibrated != 1:
            raise RuntimeError("fitter is not calibrated")

        self.set_ssrf_mode()
        self.waveform_loaded = False
        self.pvs["WAVEFORM_LOADED"].set(0)
        self._set_stage("WAITING FIT", f"case={self.case}")
        self._set_state("2/7 WAITING FIT", "Waiting for fresh calibrated ssRF fitter result")
        self._set_state("PREPARE", "Starting existing fitter -> WaveformPlan runtime")

        log = open("/tmp/genesis_ssrf_demo_prepare.log", "w", buffering=1)
        proc = subprocess.Popen(
            [
                sys.executable, "-u", "-m", "ssrf_analysis.fitter_stage_runtime",
                "--case", self.case,
                "--polarization", str(self.polarization),
                "--high-polarization", str(self.high_polarization),
                "--stage",
            ],
            cwd=ROOT,
            env={
                **os.environ,

                # fitter_stage_runtime --stage writes ONLY to the
                # sequencer staging boundary hosted by this IOC.
                "EPICS_CA_AUTO_ADDR_LIST": "NO",
                "EPICS_CA_ADDR_LIST": "127.0.0.1:5074",
                "EPICS_CA_MAX_ARRAY_BYTES": "262144",
            },
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        # Ensure subscriber is listening before producing a fresh ssRF fit.
        time.sleep(1.0)
        self._replay(limit=12, polarization_only=True)

        rc = proc.wait(timeout=50)
        log.close()
        if rc != 0:
            raise RuntimeError("fitter_stage_runtime failed; see /tmp/genesis_ssrf_demo_prepare.log")

        deadline = time.time() + 25.0
        while time.time() < deadline:
            if self.waveform_loaded:
                self._set_state(
                    "PREPARED",
                    "3/7 WAVEFORM LOADED: fit -> WaveformPlan -> physical Keysight complete",
                )
                return
            time.sleep(0.2)

        raise RuntimeError("planner completed but physical Keysight load was not confirmed")

    # --------------------------------------------------------------
    # One-shot ARM + physical switch demo, no RF authority
    # --------------------------------------------------------------

    def arm_demo(self):
        # One-shot workflow arm. No switch motion and no RF occurs here.
        if not _port_open(8777) or not _port_open(8778):
            raise RuntimeError("fitter is not running")

        calibrated = int(float(_ca_get(f"{FIT}:CALIBRATED", 5.0)))
        if calibrated != 1:
            raise RuntimeError("fitter is not calibrated")

        self._assert_no_rf()
        output, points, ready = self._read_wgen()
        nmr_mask, ssrf_mask = self._read_switches()

        if output != 0:
            raise RuntimeError(f"physical WaveGen output is ON: {output}")

        if nmr_mask != 0 or ssrf_mask != 3:
            raise RuntimeError(
                f"routing is not NMR_LIVE: NMR={nmr_mask}, ssRF={ssrf_mask}"
            )

        self.execution_armed = True
        self.pvs["EXECUTION_ARMED"].set(1)
        self._set_state(
            "ARMED",
            "One-shot workflow armed; BLAST will fit, plan, load, route, and restore",
        )

    def blast_workflow(self):
        # One-button transaction after ARM.
        if not self.execution_armed:
            raise RuntimeError("BLAST blocked: execution is not armed")

        try:
            self._set_state(
                "1/7 FIT + PLAN",
                "Fresh ssRF fit -> adaptive WaveformPlan",
            )
            self.prepare_waveform(preserve_arm=True)

            if not self.execution_armed:
                raise RuntimeError("workflow arm was revoked during preparation")

            self._set_state(
                "4/7 KEYSIGHT READY",
                "8192-point adaptive waveform loaded; output remains OFF",
            )

            # Existing switch routine consumes the one-shot arm.
            self.run_switch_demo()

        except Exception:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)
            raise

    def run_switch_demo(self):
        self.abort_event.clear()

        if not self.execution_armed:
            raise RuntimeError("RUN DEMO blocked: execution is not armed")

        # Consume the one-shot arm before any physical switch motion.
        self.execution_armed = False
        self.pvs["EXECUTION_ARMED"].set(0)

        if not self.waveform_loaded:
            raise RuntimeError("PREPARE has not loaded an 8192-point fitter waveform")

        self._assert_no_rf()
        output, points, ready = self._read_wgen()
        if output != 0 or points != MAX_POINTS or ready != 1:
            raise RuntimeError(
                f"physical WaveGen not safe/ready: output={output}, points={points}, ready={ready}"
            )

        nmr = MiniCircuitsSwitchConnection(NMR_IP, 80, 2.0)
        ssrf = MiniCircuitsSwitchConnection(SSRF_IP, 80, 2.0)

        try:
            n0 = int(nmr.read_port_mask())
            s0 = int(ssrf.read_port_mask())
            self.nmr_mask = n0
            self.ssrf_mask = s0
            self.pvs["NMR_MASK"].set(n0)
            self.pvs["SSRF_MASK"].set(s0)

            if n0 != 0 or s0 != 3:
                raise RuntimeError(f"initial routing not NMR_LIVE: NMR={n0}, ssRF={s0}")

            self._set_state("5/7 NMR DISCONNECT", "Physical NMR switch 0 -> 3")
            self._assert_no_rf()
            n = int(nmr.set_port_mask(3))
            if n != 3:
                raise RuntimeError(f"NMR switch returned {n}, expected 3")
            self.nmr_mask = 3
            self.pvs["NMR_MASK"].set(3)
            time.sleep(0.7)

            if self.abort_event.is_set():
                raise RuntimeError("demo aborted")

            self._set_state("6/7 ssRF CONNECT", "Physical ssRF switch 3 -> 0")
            self._assert_no_rf()
            s = int(ssrf.set_port_mask(0))
            if s != 0:
                raise RuntimeError(f"ssRF switch returned {s}, expected 0")
            self.ssrf_mask = 0
            self.pvs["SSRF_MASK"].set(0)

            self._set_state(
                "6/7 NO-RF BURN PATH",
                "ssRF route connected; SMC RF/FM OFF; WaveGen output OFF",
            )
            deadline = time.time() + 2.5
            while time.time() < deadline:
                self._assert_no_rf()
                if self.abort_event.is_set():
                    break
                time.sleep(0.25)

        finally:
            # Always remove ssRF route first.
            try:
                self._set_state("3/4 ssRF DISCONNECT", "ssRF switch -> mask 3")
                s = int(ssrf.set_port_mask(3))
                self.ssrf_mask = s
                self.pvs["SSRF_MASK"].set(s)
            except Exception as exc:
                self._set_state("ERROR", f"ssRF disconnect failed: {exc}")
                raise

            # NMR may be restored only after positive RF/FM-off confirmation.
            self._assert_no_rf()
            self._set_state("4/4 NMR RESTORE", "NMR switch -> mask 0")
            n = int(nmr.set_port_mask(0))
            self.nmr_mask = n
            self.pvs["NMR_MASK"].set(n)

            for sw in (nmr, ssrf):
                close = getattr(sw, "close", None)
                if callable(close):
                    close()

        if self.abort_event.is_set():
            self._set_state("ABORTED SAFE", "ssRF disconnected; NMR restored; RF/FM remain OFF")
        else:
            self._set_state(
                "COMPLETE",
                "Full no-RF workflow demo complete; NMR_LIVE restored",
            )


builder.SetDeviceName(PREFIX)
demo = DemoIOC()
dispatcher = asyncio_dispatcher.AsyncioDispatcher()
builder.LoadDatabase()
softioc.iocInit(dispatcher)


async def poll_loop():
    while True:
        await asyncio.to_thread(demo.poll_once)
        await asyncio.sleep(1.0)


dispatcher(poll_loop)

# This IOC is intentionally headless.
#
# AsyncioDispatcher owns its worker event loop/thread.  The main thread
# only needs to remain alive after iocInit(); using interactive_ioc()
# makes a detached/background launch terminate on stdin EOF.
print("[demo-ioc] HEADLESS IOC READY", flush=True)

try:
    while True:
        time.sleep(3600.0)
except KeyboardInterrupt:
    pass
