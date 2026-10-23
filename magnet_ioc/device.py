"""Genesis IOC for the magnet's Oxford IPS120-10 power supply (``UNH:GEN:MAG``).

Run by master_ioc.py like every Genesis device::

    python master_ioc.py -s configs/magnet_physical/ips120 -i ips120

Writes are disabled unless settings.yaml says ``write_enabled: true``
(GenesisBaseDevice's gate); with writes disabled the IOC only reads.
At start-up it sends nothing but reads (review issue 18: no command of any
kind on connect; command PVs start idle and are never restored).

PVs (``UNH:GEN:MAG:``)
-----------------------
Read-backs, every poll:  CURRENT (R0, demand), CURRENT_MEAS (R2), VOLTAGE (R1),
    PERSISTENT_CURRENT (R16), SETPOINT (R5), SWEEP_RATE (R6), HEATER_MA (R20),
    STATUS and its decoded SYSTEM / LIMITS / ACTIVITY / CONTROL / HEATER /
    MOTION, XMN / XMN_MEANING (V4), HEATER_ON, QUENCH, CLAMPED,
    CURRENT_DIFFERENCE (R0 - R16, V4).
Every 10th poll:  TRIP_CURRENT (R17), SAFE_NEG / SAFE_POS (R21 / R22),
    LEAD_RES (R23), INDUCTANCE (R24).
From the Genesis cryo IOCs (V4 typed these in; Ells: read them):
    HELIUM_LEVEL (the AMI 135), HELIUM_LOW_WARNING, FRINGE_FIELD_T /
    FRINGE_FIELD_G (the LS455).
V4 checks:  INDUCTANCE_CHECK (R24 within 0.3 H of 38 H, read at connect),
    IGNORE_INDUCTANCE (the operator's bypass, off at every start-up),
    HEATER_CURRENT_STATE and R20_MEANING (the heater read-back),
    SYSTEM_CHECK_STATE (one-line summary), HEATER_OFF_PROGRESS.
Derived:  MAGNET_CURRENT (R0, the supply current, while driven; R16 while
    persistent: review issue 9), FIELD (B = FIELD_SLOPE x I + FIELD_OFFSET,
    Ells's NMR-measured equation), FIELD_SOURCE, NMR_D / NMR_P (MHz),
    INDUCTANCE_MEAS (from V / dI/dt while ramping).
Operation:  STATE, OPERATION, BUSY, HEATER_PROGRESS, PERMIT, PERMIT_REASON.
Commands:  TARGET_CURRENT, TARGET_FIELD (sets TARGET_CURRENT by the field
    equation), RAMP, HOLD, HEATER_ON_CMD, HEATER_OFF_CMD, MATCH, OUTAGE (the
    red button), EXPERT (allows ramps down to 16" of magnet LHe).

Every operator request and what came of it goes to an event log (JSONL,
review issue 16); every poll's current, voltage and status go to a CSV log
(V4's columns, then the magnet current, field and heater); every PV is listed
in settings.yaml so the master archiver records it.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import logging
import math
import time
from pathlib import Path

from softioc import alarm, builder

from genesis_common.base_device import GenesisBaseDevice
from genesis_common.serial_tcp_base import SerialTcpConnection

from .interlocks import CryoPermit, PermitConfig
from .ips120_protocol import IPSClient, IPSError, decode_xmn, get_xmn_status
from .ips120_sequences import MagnetConfig, MagnetSequencer

log = logging.getLogger(__name__)

GAMMA_D_MHZ_PER_T = 6.53590365
GAMMA_P_MHZ_PER_T = 42.57747892
SLOW_EVERY = 10


class Device(GenesisBaseDevice):
    """IPS120-10 driver + the operations, on one serial-gateway port."""

    def __init__(self, device_name, settings):
        self.cfg = MagnetConfig.from_settings(settings)
        self.permit_cfg = PermitConfig.from_settings(settings.get("permit"))
        field_eq = settings.get("field_equation", {}) or {}
        self.field_slope = float(field_eq.get("slope_t_per_a", 0.1027405))
        self.field_offset = float(field_eq.get("offset_t", 0.00003497875))
        self._poll = 0
        self._expert = False
        self._event_dir = self._resolve_event_dir(settings.get("magnet_event_dir"))
        self._last_quench = False
        self.sequencer = None
        self.permit = CryoPermit(self.permit_cfg)
        super().__init__(device_name, settings)

    # ------------------------------------------------------------ setup
    @staticmethod
    def _resolve_event_dir(configured):
        if configured:
            return Path(configured)
        try:
            from archive.data_paths import archive_root
            return Path(archive_root()) / "events" / "magnet"
        except Exception:                        # noqa: BLE001
            return Path("archive") / "events" / "magnet"

    def _create_pvs(self):
        p, b = self.pvs, builder
        ai = lambda name, egu, prec=4: b.aIn(name, EGU=egu, PREC=prec)   # noqa: E731
        for name, egu, prec in (
                ("CURRENT", "A", 4), ("CURRENT_MEAS", "A", 4), ("VOLTAGE", "V", 4),
                ("PERSISTENT_CURRENT", "A", 4), ("SETPOINT", "A", 4), ("SWEEP_RATE", "A/min", 3),
                ("TRIP_CURRENT", "A", 4), ("HEATER_MA", "mA", 1), ("SAFE_NEG", "A", 3),
                ("SAFE_POS", "A", 3), ("LEAD_RES", "mohm", 2), ("INDUCTANCE", "H", 2),
                ("INDUCTANCE_MEAS", "H", 1), ("MAGNET_CURRENT", "A", 4), ("FIELD", "T", 5),
                ("FIELD_SLOPE", "T/A", 7), ("FIELD_OFFSET", "T", 8), ("NMR_D", "MHz", 4),
                ("NMR_P", "MHz", 3), ("HEATER_PROGRESS", "%", 0),
                ("HEATER_OFF_PROGRESS", "%", 0), ("CURRENT_DIFFERENCE", "A", 4),
                ("HELIUM_LEVEL", "in", 2), ("FRINGE_FIELD_T", "T", 8), ("FRINGE_FIELD_G", "G", 4)):
            p[name] = ai(name, egu, prec)
        for name in ("STATUS", "SYSTEM", "LIMITS", "ACTIVITY", "CONTROL", "HEATER", "MOTION",
                     "FIELD_SOURCE", "STATE", "OPERATION", "PERMIT_REASON", "VERSION",
                     "XMN", "XMN_MEANING", "INDUCTANCE_CHECK", "HEATER_CURRENT_STATE",
                     "R20_MEANING", "SYSTEM_CHECK_STATE"):
            p[name] = b.stringIn(name)
        p["HEATER_ON"] = b.boolIn("HEATER_ON", ZNAM="Off (switch closed)", ONAM="On (switch open)")
        p["QUENCH"] = b.boolIn("QUENCH", ZNAM="Normal", ONAM="QUENCHED", OSV="MAJOR")
        p["CLAMPED"] = b.boolIn("CLAMPED", ZNAM="Unclamped", ONAM="Clamped")
        p["BUSY"] = b.boolIn("BUSY", ZNAM="Idle", ONAM="Busy")
        p["PERMIT"] = b.boolIn("PERMIT", ZNAM="Blocked", ONAM="Permitted", ZSV="MINOR")
        p["HELIUM_LOW_WARNING"] = b.boolIn("HELIUM_LOW_WARNING", ZNAM="Helium OK",
                                           ONAM="LOW HELIUM", OSV="MAJOR")
        p["TARGET_CURRENT"] = b.aOut("TARGET_CURRENT", EGU="A", PREC=4, DRVL=0.0,
                                     DRVH=self.cfg.max_current - 0.001, initial_value=0.0)
        p["TARGET_FIELD"] = b.aOut("TARGET_FIELD", EGU="T", PREC=5, initial_value=0.0,
                                   on_update=self._set_target_field)
        p["EXPERT"] = b.boolOut("EXPERT", ZNAM="Normal", ONAM="Expert", initial_value=0,
                                on_update=self._set_expert)
        # V4: the operator's bypass of the R24 check; never on at start-up
        p["IGNORE_INDUCTANCE"] = b.boolOut("IGNORE_INDUCTANCE", ZNAM="Check", ONAM="BYPASSED",
                                           initial_value=0, on_update=self._set_ignore_inductance)
        for name, handler in (("RAMP", self._cmd_ramp), ("HOLD", self._cmd_hold),
                              ("HEATER_ON_CMD", self._cmd_heater_on),
                              ("HEATER_OFF_CMD", self._cmd_heater_off),
                              ("MATCH", self._cmd_match), ("OUTAGE", self._cmd_outage)):
            p[name] = b.Action(name, on_update=handler)
        p["FIELD_SLOPE"].set(self.field_slope)
        p["FIELD_OFFSET"].set(self.field_offset)
        p["STATE"].set("IDLE")
        p["PERMIT_REASON"].set("not checked yet")
        p["INDUCTANCE_CHECK"].set("NOT CHECKED")
        p["HEATER_CURRENT_STATE"].set("NOT VERIFIED")
        p["R20_MEANING"].set(self.cfg.r20_meaning)
        p["SYSTEM_CHECK_STATE"].set("STARTUP CHECKS")
        p["XMN"].set("X??")

    def _create_connection(self):
        s = self.settings
        transport = SerialTcpConnection(s["ip"], int(s["port"]), float(s.get("timeout", 3.0)),
                                        float(s.get("settle", 0.0)))
        transport.eol = b"\r"
        return IPSClient(transport)

    def _post_connect(self):
        """Reads only (issue 18)."""
        self.pvs["VERSION"].set(str(self.t.read_version())[:39])
        override = self.sequencer.inductance_override if self.sequencer else False
        self.sequencer = MagnetSequencer(
            self.t, self.cfg, permit=self._check_permit, event=self._event,
            state=lambda text: self.pvs["STATE"].set(str(text)[:39]),
            progress=lambda pct: self.pvs["HEATER_PROGRESS"].set(float(pct)),
            progress_off=lambda pct: self.pvs["HEATER_OFF_PROGRESS"].set(float(pct)),
            heater=lambda text: self.pvs["HEATER_CURRENT_STATE"].set(str(text)[:39]))
        self.sequencer.inductance_override = override
        # V4's start-up inductance check: R24, a read only
        self.sequencer.note_inductance(self.t.read_inductance())
        self.pvs["INDUCTANCE_CHECK"].set(self.sequencer.inductance_text()[:39])

    # ------------------------------------------------------------ recording
    def _event(self, kind: str, message: str, **data):
        """Review issue 16: every request and outcome, with the time and the
        magnet's state, in a JSONL event log; the latest on LAST_EVENT."""
        self.record_event(f"{kind}: {message}")
        now = dt.datetime.now(dt.timezone.utc)
        row = {"utc": now.isoformat(timespec="milliseconds"),
               "local": now.astimezone().isoformat(timespec="milliseconds"),
               "kind": kind, "message": message, "data": data,
               "current_a": self.pvs["CURRENT"].get(), "field_t": self.pvs["FIELD"].get(),
               "heater": self.pvs["HEATER"].get(), "state": self.pvs["STATE"].get()}
        try:
            self._event_dir.mkdir(parents=True, exist_ok=True)
            with open(self._event_dir / f"magnet_events_{now:%Y-%m-%d}.jsonl", "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError as exc:
            log.error("magnet event log: %s", exc)

    # ------------------------------------------------------------ permit
    async def _check_permit(self):
        ok, reason = await self.permit.check(self._expert)
        self.pvs["PERMIT"].set(1 if ok else 0)
        self.pvs["PERMIT_REASON"].set(str(reason)[:39])
        return ok, reason

    # ------------------------------------------------------------ commands
    def _gate(self, pv: str) -> bool:
        if self.sequencer is None or not self.can_write(pv):
            self._event("refused", f"{pv}: writes disabled or supply disconnected")
            return False
        return True

    async def _cmd_ramp(self, _value):
        if self._gate("RAMP"):
            target = float(self.pvs["TARGET_CURRENT"].get())
            self._event("request", f"RAMP to {target:.3f} A", target=target, expert=self._expert)
            self.sequencer.start("ramp", self.sequencer.ramp, target)

    async def _cmd_hold(self, _value):
        if self._gate("HOLD"):
            await self.sequencer.hold("operator")

    async def _cmd_heater_on(self, _value):
        if self._gate("HEATER_ON_CMD"):
            self._event("request", "heater ON")
            self.sequencer.start("heater on", self.sequencer.heater_on)

    async def _cmd_heater_off(self, _value):
        if self._gate("HEATER_OFF_CMD"):
            self._event("request", "heater OFF")
            self.sequencer.start("heater off", self.sequencer.heater_off)

    async def _cmd_match(self, _value):
        if self._gate("MATCH"):
            self._event("request", "match supply to magnet")
            self.sequencer.start("match", self.sequencer.match)

    async def _cmd_outage(self, _value):
        if self._gate("OUTAGE"):
            asyncio.ensure_future(self.sequencer.outage())

    def _set_target_field(self, field_t):
        amps = (float(field_t) - self.field_offset) / self.field_slope
        if math.isfinite(amps) and 0.0 <= amps < self.cfg.max_current:
            self.pvs["TARGET_CURRENT"].set(round(amps, 4))
        else:
            self.pvs["TARGET_FIELD"].set_alarm(severity=alarm.MAJOR_ALARM, alarm=alarm.WRITE_ALARM)

    def _set_ignore_inductance(self, value):
        on = bool(int(value))
        if self.sequencer is not None:
            self.sequencer.inductance_override = on
            self.pvs["INDUCTANCE_CHECK"].set(self.sequencer.inductance_text()[:39])
        self._event("inductance", "R24 check BYPASSED by the operator" if on
                    else "R24 check restored")

    def _set_expert(self, value):
        self._expert = bool(int(value))
        self._event("expert", "Expert mode " + ("ON (LHe floor 16 in)" if self._expert else "OFF"))

    # ------------------------------------------------------------ V4 read-backs
    def _publish_cryo(self):
        """HELIUM_LEVEL and FRINGE_FIELD from the Genesis cryo IOCs (V4 had
        them typed in by hand; Ells 2026-10-23: read them)."""
        level = self.permit.value(self.permit_cfg.lhe_pv)
        floor = self.permit_cfg.lhe_min_expert_in if self._expert else self.permit_cfg.lhe_min_in
        if math.isfinite(level):
            self.pvs["HELIUM_LEVEL"].set(level)
            self.pvs["HELIUM_LOW_WARNING"].set(1 if level < floor else 0)
        else:
            self.pvs["HELIUM_LEVEL"].set_alarm(severity=alarm.INVALID_ALARM, alarm=alarm.READ_ALARM)
            self.pvs["HELIUM_LOW_WARNING"].set(1)
        tesla = self.permit.value(self.permit_cfg.fringe_pv)
        if math.isfinite(tesla):
            self.pvs["FRINGE_FIELD_T"].set(tesla)
            self.pvs["FRINGE_FIELD_G"].set(tesla * 1.0e4)
        else:
            for name in ("FRINGE_FIELD_T", "FRINGE_FIELD_G"):
                self.pvs[name].set_alarm(severity=alarm.INVALID_ALARM, alarm=alarm.READ_ALARM)

    def _system_check(self, st) -> str:
        """V4's SYSTEM CHECKS line, most serious first."""
        seq = self.sequencer
        if seq is not None and seq.inductance_ok is False and not seq.inductance_override:
            return "CONTROLS BLOCKED - INDUCTANCE"
        text = st.text()
        if st.system != "0":
            return f"MAGNET FAULT - {text['system']}"
        if st.limits != "0":
            return f"WARNING - {text['limits']}"
        if self.pvs["HELIUM_LOW_WARNING"].get():
            return "WARNING - LOW HELIUM LEVEL"
        if not self.pvs["PERMIT"].get():
            return "BLOCKED - " + str(self.pvs["PERMIT_REASON"].get())
        if seq is not None and seq.inductance_override:
            return "INDUCTANCE BYPASS ACTIVE"
        return "SYSTEM READY"

    def _log_csv(self, values: dict, st, field_t: float):
        """Every poll: V4's columns (DateTime, Current_A, Voltage_V, Xmn,
        Xmn_Meaning), then the magnet current, field and heater.  One file a
        day; logging never stops the IOC."""
        now = dt.datetime.now()
        path = self._event_dir / f"magnet_log_{now:%Y-%m-%d}.csv"
        try:
            self._event_dir.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["DateTime", "Current_A", "Voltage_V", "Xmn", "Xmn_Meaning",
                                "MagnetCurrent_A", "Field_T", "Persistent_A", "Heater_mA", "Heater"])
                w.writerow([now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], f"{values['CURRENT']:.4f}",
                            f"{values['VOLTAGE']:.6f}", get_xmn_status(st.raw), decode_xmn(st.raw),
                            f"{self.pvs['MAGNET_CURRENT'].get():.4f}", f"{field_t:.6f}",
                            f"{values['PERSISTENT_CURRENT']:.4f}", f"{values['HEATER_MA']:.2f}",
                            st.text()["heater"]])
        except OSError as exc:
            log.error("magnet CSV log: %s", exc)

    # ------------------------------------------------------------ polling
    def field_from_current(self, amps: float) -> float:
        return self.field_slope * amps + self.field_offset

    def _read_once(self):
        """One blocking read cycle (runs in a worker thread)."""
        t = self.t
        st = t.read_status()
        out = {"st": st, "CURRENT": t.read_demand_current(), "VOLTAGE": t.read_voltage(),
               "CURRENT_MEAS": t.read_measured_current(), "SETPOINT": t.read_setpoint(),
               "SWEEP_RATE": t.read_sweep_rate(), "PERSISTENT_CURRENT": t.read_persistent_current(),
               "HEATER_MA": t.read_heater_current()}
        if self._poll % SLOW_EVERY == 0:
            out.update(TRIP_CURRENT=t.read_trip_current(), LEAD_RES=t.read_lead_resistance(),
                       INDUCTANCE=t.read_inductance())
            out["SAFE_NEG"], out["SAFE_POS"] = t.read_safe_limits()
        return out

    async def do_reads(self):
        if self.t is None:
            self._set_comm(False, "Supply disconnected")
            self.reconnect()
            return False
        try:
            values = await asyncio.to_thread(self._read_once)
        except (OSError, IPSError) as exc:
            log.warning("%s: read failed: %s", self.device_name, exc)
            self._set_comm(False, str(exc))
            for name in ("CURRENT", "FIELD", "MAGNET_CURRENT", "PERSISTENT_CURRENT"):
                self.pvs[name].set_alarm(severity=alarm.INVALID_ALARM, alarm=alarm.READ_ALARM)
            self.reconnect()
            return False
        self._poll += 1
        st = values.pop("st")
        for name, value in values.items():
            self.pvs[name].set(value)
        text = st.text()
        self.pvs["STATUS"].set(st.raw)
        self.pvs["XMN"].set(get_xmn_status(st.raw))
        self.pvs["XMN_MEANING"].set(decode_xmn(st.raw)[:39])
        self.pvs["CURRENT_DIFFERENCE"].set(values["CURRENT"] - values["PERSISTENT_CURRENT"])
        for key in ("system", "limits", "activity", "control", "heater", "motion"):
            self.pvs[key.upper()].set(text[key][:39])
        self.pvs["HEATER_ON"].set(1 if st.heater_on else 0)
        self.pvs["QUENCH"].set(1 if st.quenched else 0)
        self.pvs["CLAMPED"].set(1 if st.clamped else 0)
        # issue 9: the current in the magnet, right in both modes
        if st.heater_on:
            # R0, as the supply's own display and the student's code: on the
            # real supply R2 ("measured") reads ~1.03 A above R0 (2026-10-23:
            # 1.04 A at 0 A demand, 1.11 A at 0.10 A), so it is shown, not used
            magnet, source = values["CURRENT"], "supply (driven)"
        else:
            magnet, source = values["PERSISTENT_CURRENT"], "persistent"
        field = self.field_from_current(magnet)
        self.pvs["MAGNET_CURRENT"].set(magnet)
        self.pvs["FIELD_SOURCE"].set(source)
        self.pvs["FIELD"].set(field)
        self.pvs["NMR_D"].set(field * GAMMA_D_MHZ_PER_T)
        self.pvs["NMR_P"].set(field * GAMMA_P_MHZ_PER_T)
        seq = self.sequencer
        if seq is not None:
            self.pvs["BUSY"].set(1 if seq.busy else 0)
            self.pvs["OPERATION"].set(seq.operation[:39])
            if math.isfinite(seq.last_inductance_h):
                self.pvs["INDUCTANCE_MEAS"].set(seq.last_inductance_h)
            if "INDUCTANCE" in values:
                seq.note_inductance(values["INDUCTANCE"])
            self.pvs["INDUCTANCE_CHECK"].set(seq.inductance_text()[:39])
            if not seq.busy:                        # a running operation reports its own
                self.pvs["HEATER_CURRENT_STATE"].set(seq.heater_text(st, values["HEATER_MA"])[:39])
            self.pvs["R20_MEANING"].set(seq.r20_meaning or "auto: not decided")
        # issue 2: a quench reported by the supply stops whatever is running
        if st.fault and not self._last_quench:
            self._event("fault", f"supply status {text['system']}", trip_current=values.get(
                "TRIP_CURRENT"))
            if seq is not None and seq.busy and bool(self.settings.get("write_enabled", False)):
                await seq.hold("supply fault")
        self._last_quench = st.fault
        if not (seq is not None and seq.busy):
            await self._check_permit()
        self._publish_cryo()
        self.pvs["SYSTEM_CHECK_STATE"].set(self._system_check(st)[:39])
        self._log_csv(values, st, field)
        self.pvs["LAST_OK"].set(time.time())
        self._set_comm(True)
        return True
