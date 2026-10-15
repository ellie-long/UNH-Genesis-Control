"""Sim Control IOC (``UNH:GEN:SIM``): the operator's handle on a simulated cool-down.

Run by the unchanged ``master_ioc.py`` like every other Genesis IOC
(``module: cooldown_sim.controller``).  It owns no simulation state: operator
writes are relayed to the engine over the loopback control channel
(cooldown_sim/control.py), and every ``do_reads`` cycle publishes the
engine's status as read-back PVs.  Command PVs (``*_CMD``) are only ever
written by clients; read-backs (``*_RBV`` and status PVs) only by this IOC,
so no output record ever re-triggers itself.

The namespace ``UNH:GEN:SIM`` is used by no live Genesis profile.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math

from softioc import alarm, builder

from .timeparse import parse_lab_time
from .sim_pvs import AUTOPILOT_LEGS, FAULT_INSTRUMENTS, FAULT_MODES

log = logging.getLogger(__name__)

#: (PV, units, status-key) for the target-model truth read-backs
TRUTH = (("TRUE_P", "%", "P"), ("TRUE_Q", "%", "Q"), ("TRUE_T_LATTICE", "K", "T_lattice"),
         ("TRUE_T_SPIN", "K", "T_spin"), ("TRUE_B", "T", "B"),
         ("MW_DRIVE", "", "mw_drive"), ("MW_FREQ", "GHz", "mw_freq_GHz"),
         ("HEAT_CB", "W", "heat_cb_W"), ("QUENCH_LEVEL", "", "quench_level"),
         ("HEAT_TOTAL", "W", "heat_total_W"), ("BOILOFF_DELTA", "slm", "boiloff_delta_slm"),
         ("TRUE_T_BATH", "K", "T_bath"),
         ("TRUE_Q_BOLTZMANN", "%", "Q_boltzmann"),
         ("FIT_P", "%", "fit_P"), ("FIT_RESID_P", "%", "fit_resid_P"))

#: model inputs settable from Sim Control: (PV, model key, units, DRVL, DRVH)
MODEL_INPUTS = (("CB_HEAT_W_CMD", "coldboard_heat_W", "W", 0.0, 5.0),
                ("QUENCH_CMD", "quench", "", 0, 1),
                ("NMR_NOISE_X_CMD", "nmr_noise_x", "x", 0.0, 100.0),
                ("NMR_PHASE_DEG_CMD", "nmr_phase_offset_deg", "deg", -180.0, 180.0),
                ("TAKE_BASELINE_CMD", "take_baseline", "", 0, 1),
                ("FIELD_OFFSET_MHZ_CMD", "line_offset_MHz", "MHz", -5.0, 5.0),
                ("MW_ON_CMD", "mw_on", "", 0, 1),
                ("MW_FREQ_GHZ_CMD", "mw_freq_GHz", "GHz", 100.0, 200.0),
                ("MW_POWER_DB_CMD", "mw_power_dB", "dB", -60.0, 30.0),
                ("MW_UCA_V_CMD", "mw_uca_V", "V", 0.0, 5.0))


class Device:
    """master_ioc.py device contract: .pvs, connect(), async do_reads()."""

    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.port = int(settings["control_port"])
        self.pvs: dict = {}
        self._pending: list[dict] = []
        self._reader = self._writer = None
        self._create_pvs()

    # ------------------------------------------------------------------ PVs
    def _cmd(self, request: dict):
        self._pending.append(request)

    def _create_pvs(self):
        b, p = builder, self.pvs
        p["BANNER"] = b.stringIn("BANNER", initial_value="SIMULATION - NOT LIVE HARDWARE")
        p["ENGINE_OK"] = b.boolIn("ENGINE_OK", ZNAM="No engine", ONAM="Connected",
                                  ZSV="MAJOR", OSV="NO_ALARM")
        p["SIM_TIME"] = b.aIn("SIM_TIME", EGU="s", PREC=0)
        p["SIM_DATETIME"] = b.stringIn("SIM_DATETIME")
        p["RUNNING"] = b.boolIn("RUNNING", ZNAM="Paused", ONAM="Running")
        p["SPEED_RBV"] = b.aIn("SPEED_RBV", EGU="x", PREC=1)
        for name in ("MODE", "SOURCE", "SCENARIO", "RECORD_START", "RECORD_END"):
            p[name] = b.stringIn(name)
        p["WINDOW"] = b.longStringIn("WINDOW", length=128)
        p["LAST_EVENT"] = b.longStringIn("LAST_EVENT", length=256)
        p["RUN_DIR"] = b.longStringIn("RUN_DIR", length=512)
        p["NOTE_RBV"] = b.longStringIn("NOTE_RBV", length=256)

        # clock commands
        p["RUN_CMD"] = b.boolOut("RUN_CMD", ZNAM="Pause", ONAM="Run", always_update=True,
                                 on_update=lambda v: self._cmd({"cmd": "play" if v else "pause"}))
        p["SPEED_CMD"] = b.aOut("SPEED_CMD", EGU="x", PREC=1, DRVL=0.1, DRVH=600.0,
                                initial_value=60.0, always_update=True,
                                on_update=lambda v: self._cmd({"cmd": "speed", "value": float(v)}))
        p["JUMP_MIN_CMD"] = b.aOut("JUMP_MIN_CMD", EGU="min", PREC=0, DRVL=-10080, DRVH=10080,
                                   always_update=True,
                                   on_update=lambda v: v and self._cmd({"cmd": "jump", "minutes": float(v)}))
        p["SEEK_CMD"] = b.stringOut("SEEK_CMD", always_update=True, on_update=self._seek)

        # faults
        for inst in FAULT_INSTRUMENTS:
            key = inst.upper()
            p[f"FAULT_{key}_CMD"] = b.mbbOut(
                f"FAULT_{key}_CMD", *FAULT_MODES, always_update=True,
                on_update=lambda v, inst=inst: self._cmd(
                    {"cmd": "fault", "instrument": inst, "mode": FAULT_MODES[int(v)]}))
            p[f"FAULT_{key}_RBV"] = b.stringIn(f"FAULT_{key}_RBV")

        # autopilot
        for leg in AUTOPILOT_LEGS:
            key = leg.upper()
            p[f"AUTOPILOT_{key}_CMD"] = b.boolOut(
                f"AUTOPILOT_{key}_CMD", ZNAM="Human", ONAM="Autopilot", always_update=True,
                on_update=lambda v, leg=leg: self._cmd({"cmd": "autopilot", "leg": leg, "on": bool(v)}))
            p[f"AUTOPILOT_{key}_RBV"] = b.boolIn(f"AUTOPILOT_{key}_RBV", ZNAM="Human", ONAM="Autopilot")

        # session
        p["NOTE_CMD"] = b.longStringOut("NOTE_CMD", length=256, always_update=True,
                                        on_update=lambda v: v and self._cmd({"cmd": "note", "text": str(v)}))
        p["MARK_CMD"] = b.boolOut("MARK_CMD", ZNAM="", ONAM="Mark", always_update=True,
                                  on_update=lambda v: v and self._cmd({"cmd": "mark"}))
        p["END_SESSION_CMD"] = b.boolOut("END_SESSION_CMD", ZNAM="", ONAM="End", always_update=True,
                                         on_update=lambda v: v and self._cmd({"cmd": "stop"}))

        # target model truth and inputs
        for pv, egu, _key in TRUTH:
            p[pv] = b.aIn(pv, EGU=egu, PREC=3)
        p["MODEL_ACTIVE"] = b.boolIn("MODEL_ACTIVE", ZNAM="Replay only", ONAM="Closed loop")
        for pv, key, egu, lo, hi in MODEL_INPUTS:
            p[pv] = b.aOut(pv, EGU=egu, PREC=2, DRVL=lo, DRVH=hi, always_update=True,
                           on_update=lambda v, key=key: self._cmd({"cmd": "model", "key": key,
                                                                    "value": float(v)}))

        # trend history for Sim Control plots (sim minutes before now)
        n = int(self.settings.get("history_points", 240))
        p["HIST_MIN"] = b.WaveformIn("HIST_MIN", length=n, datatype=float)
        p["HIST_TRUE_P"] = b.WaveformIn("HIST_TRUE_P", length=n, datatype=float)
        p["HIST_FIT_P"] = b.WaveformIn("HIST_FIT_P", length=n, datatype=float)
        self._hist: list[tuple[float, float, float]] = []
        self._hist_n = n

    def _seek(self, value):
        try:
            self._cmd({"cmd": "seek", "value": parse_lab_time(str(value))})
        except ValueError as exc:
            self.pvs["LAST_EVENT"].set(str(exc))

    # ------------------------------------------------------------ lifecycle
    def connect(self):
        self.pvs["ENGINE_OK"].set(0)

    async def _ensure(self) -> bool:
        if self._writer is not None:
            return True
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.port), timeout=2.0)
            return True
        except (OSError, asyncio.TimeoutError):
            self._reader = self._writer = None
            return False

    async def _call(self, request: dict) -> dict:
        self._writer.write((json.dumps(request) + "\n").encode())
        await self._writer.drain()
        line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
        if not line:
            raise ConnectionError("engine closed the control channel")
        return json.loads(line)

    async def do_reads(self):
        if not await self._ensure():
            self.pvs["ENGINE_OK"].set(0)
            return False
        try:
            while self._pending:
                req = self._pending.pop(0)
                reply = await self._call(req)
                if not reply.get("ok"):
                    self.pvs["LAST_EVENT"].set(f"refused: {reply.get('error', '?')}"[:255])
                elif req.get("cmd") == "note":
                    self.pvs["NOTE_RBV"].set(req.get("text", "")[:255])
            reply = await self._call({"cmd": "status"})
        except (OSError, ConnectionError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            log.warning("engine link lost: %s", exc)
            if self._writer is not None:
                self._writer.close()
            self._reader = self._writer = None
            self.pvs["ENGINE_OK"].set(0)
            return False
        self._publish(reply.get("status", {}))
        self.pvs["ENGINE_OK"].set(1)
        return True

    def _publish(self, st: dict):
        p = self.pvs
        p["SIM_TIME"].set(float(st.get("sim_time", math.nan)))
        p["SIM_DATETIME"].set(str(st.get("sim_local", ""))[:39])
        p["RUNNING"].set(1 if st.get("running") else 0)
        p["SPEED_RBV"].set(float(st.get("speed", math.nan)))
        for key, pv in (("mode", "MODE"), ("source", "SOURCE"), ("scenario", "SCENARIO"),
                        ("record_start", "RECORD_START"), ("record_end", "RECORD_END")):
            p[pv].set(str(st.get(key, ""))[:39])
        p["WINDOW"].set(str(st.get("window", ""))[:127])
        p["LAST_EVENT"].set(str(st.get("last_event", ""))[:255])
        p["RUN_DIR"].set(str(st.get("run_dir", ""))[:511])
        for inst, mode in st.get("faults", {}).items():
            pv = p.get(f"FAULT_{inst.upper()}_RBV")
            if pv is not None:
                pv.set(mode)
                pv.set_alarm(severity=0 if mode == "none" else 1,
                             alarm=alarm.NO_ALARM if mode == "none" else alarm.STATE_ALARM)
        for leg, on in st.get("autopilot", {}).items():
            pv = p.get(f"AUTOPILOT_{leg.upper()}_RBV")
            if pv is not None:
                pv.set(1 if on else 0)
        truth = st.get("truth", {}) or {}
        p["MODEL_ACTIVE"].set(1 if truth else 0)
        for pv, _egu, key in TRUTH:
            p[pv].set(float(truth.get(key, math.nan)))
        self._history(st, truth)

    def _history(self, st: dict, truth: dict):
        t = float(st.get("sim_time", math.nan))
        if not truth or not math.isfinite(t):
            return
        if self._hist and t < self._hist[-1][0]:
            self._hist.clear()                      # seek backwards: restart the trend
        if not self._hist or t - self._hist[-1][0] >= 30.0:
            self._hist.append((t, float(truth.get("P", math.nan)), float(truth.get("fit_P", math.nan))))
            self._hist = self._hist[-self._hist_n:]
        minutes = [(h[0] - t) / 60.0 for h in self._hist]
        self.pvs["HIST_MIN"].set(minutes)
        self.pvs["HIST_TRUE_P"].set([h[1] for h in self._hist])
        self.pvs["HIST_FIT_P"].set([h[2] for h in self._hist])
