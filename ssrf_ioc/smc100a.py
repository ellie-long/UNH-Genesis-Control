"""
Genesis Phase-I IOC driver for the Rohde & Schwarz SMC100A.

Based on Muhammad Farooq's UNH ssRF EPICS implementation.

The SMC100A is the RF carrier / FM / power-sweep source used by the
UNH ssRF system.

Genesis architecture:

    SMC100A
        |
    raw TCP/SCPI
        |
    DeviceConnection
        |
    Device
        |
    master_ioc.py
        |
    EPICS PVs

Safety / architecture rules:
- IOC startup is read-only unless write_enabled=true.
- Polling never generates hardware writes.
- Command PVs and readback PVs are separate.
- Command PVs are synchronized to confirmed hardware state using
  process=False.
- The device IOC knows how to operate the SMC100A, but knows nothing
  about the ssRF burn interlock. Burn safety belongs to the higher-level
  Genesis logic IOC.
"""

from __future__ import annotations

import logging
import time

from softioc import builder

from genesis_common.scpi_base import ScpiConnection, ScpiDevice


log = logging.getLogger(__name__)


FM_SOURCES = ("INT", "EXT")


class Device(ScpiDevice):
    """EPICS-facing Genesis SMC100A device."""

    connection_class = None  # Assigned after DeviceConnection definition.

    def _create_pvs(self):
        freq_low, freq_high = self.settings.get(
            "freq_limit_mhz",
            [0.1, 3200.0],
        )

        level_low, level_high = self.settings.get(
            "level_limit_dbm",
            [-145.0, 10.0],
        )

        # --------------------------------------------------------------
        # Identity
        # --------------------------------------------------------------

        self.pvs["IDN"] = builder.stringIn("IDN")

        # --------------------------------------------------------------
        # Carrier frequency
        # --------------------------------------------------------------

        self.pvs["FREQ_CMD"] = builder.aOut(
            "FREQ_CMD",
            DRVL=float(freq_low),
            DRVH=float(freq_high),
            EGU="MHz",
            PREC=6,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["FREQ_RBV"] = builder.aIn(
            "FREQ_RBV",
            LOPR=float(freq_low),
            HOPR=float(freq_high),
            EGU="MHz",
            PREC=6,
            **self.analog,
        )

        # --------------------------------------------------------------
        # RF level
        # --------------------------------------------------------------

        self.pvs["LEVEL_CMD"] = builder.aOut(
            "LEVEL_CMD",
            DRVL=float(level_low),
            DRVH=float(level_high),
            EGU="dBm",
            PREC=2,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["LEVEL_RBV"] = builder.aIn(
            "LEVEL_RBV",
            LOPR=float(level_low),
            HOPR=float(level_high),
            EGU="dBm",
            PREC=2,
            **self.analog,
        )

        # --------------------------------------------------------------
        # RF output
        # --------------------------------------------------------------

        self.pvs["RF_CMD"] = builder.boolOut(
            "RF_CMD",
            ZNAM="OFF",
            ONAM="ON",
            on_update_name=self.do_sets,
        )

        self.pvs["RF_RBV"] = builder.boolIn(
            "RF_RBV",
            ZNAM="OFF",
            ONAM="ON",
        )

        # --------------------------------------------------------------
        # FM enable
        # --------------------------------------------------------------

        self.pvs["FM_CMD"] = builder.boolOut(
            "FM_CMD",
            ZNAM="OFF",
            ONAM="ON",
            on_update_name=self.do_sets,
        )

        self.pvs["FM_RBV"] = builder.boolIn(
            "FM_RBV",
            ZNAM="OFF",
            ONAM="ON",
        )

        # --------------------------------------------------------------
        # FM source
        #
        # 0 = internal SMC modulation source
        # 1 = external source, eventually the Keysight WaveGen IOC
        # --------------------------------------------------------------

        self.pvs["FM_SOURCE_CMD"] = builder.boolOut(
            "FM_SOURCE_CMD",
            ZNAM="INT",
            ONAM="EXT",
            on_update_name=self.do_sets,
        )

        self.pvs["FM_SOURCE_RBV"] = builder.boolIn(
            "FM_SOURCE_RBV",
            ZNAM="INT",
            ONAM="EXT",
        )

        # --------------------------------------------------------------
        # FM deviation
        # --------------------------------------------------------------

        self.pvs["FM_DEV_CMD"] = builder.aOut(
            "FM_DEV_CMD",
            EGU="kHz",
            PREC=3,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["FM_DEV_RBV"] = builder.aIn(
            "FM_DEV_RBV",
            EGU="kHz",
            PREC=3,
            **self.analog,
        )

        # --------------------------------------------------------------
        # Power sweep
        # --------------------------------------------------------------

        self.pvs["SWEEP_START_CMD"] = builder.aOut(
            "SWEEP_START_CMD",
            DRVL=float(level_low),
            DRVH=float(level_high),
            EGU="dBm",
            PREC=2,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["SWEEP_START_RBV"] = builder.aIn(
            "SWEEP_START_RBV",
            EGU="dBm",
            PREC=2,
            **self.analog,
        )

        self.pvs["SWEEP_STOP_CMD"] = builder.aOut(
            "SWEEP_STOP_CMD",
            DRVL=float(level_low),
            DRVH=float(level_high),
            EGU="dBm",
            PREC=2,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["SWEEP_STOP_RBV"] = builder.aIn(
            "SWEEP_STOP_RBV",
            EGU="dBm",
            PREC=2,
            **self.analog,
        )

        self.pvs["SWEEP_STEP_CMD"] = builder.aOut(
            "SWEEP_STEP_CMD",
            EGU="dB",
            PREC=2,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["SWEEP_STEP_RBV"] = builder.aIn(
            "SWEEP_STEP_RBV",
            EGU="dB",
            PREC=2,
            **self.analog,
        )

        self.pvs["SWEEP_DWELL_CMD"] = builder.aOut(
            "SWEEP_DWELL_CMD",
            EGU="ms",
            PREC=1,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["SWEEP_DWELL_RBV"] = builder.aIn(
            "SWEEP_DWELL_RBV",
            EGU="ms",
            PREC=1,
            **self.analog,
        )

        # Power sweep shape.
        # Genesis ssRF burns use TRI:
        #     start -> target -> start
        self.pvs["SWEEP_SHAPE_CMD"] = builder.boolOut(
            "SWEEP_SHAPE_CMD",
            ZNAM="SAW",
            ONAM="TRI",
            on_update_name=self.do_sets,
        )

        self.pvs["SWEEP_SHAPE_RBV"] = builder.boolIn(
            "SWEEP_SHAPE_RBV",
            ZNAM="SAW",
            ONAM="TRI",
        )

        self.pvs["SWEEP_ARM_CMD"] = builder.boolOut(
            "SWEEP_ARM_CMD",
            ZNAM="DISARMED",
            ONAM="ARMED",
            on_update_name=self.do_sets,
        )

        self.pvs["SWEEP_ARM_RBV"] = builder.boolIn(
            "SWEEP_ARM_RBV",
            ZNAM="DISARMED",
            ONAM="ARMED",
        )

        # This is deliberately called TRIGGER, not FIRE.
        #
        # The SMC IOC only knows how to trigger its sweep.
        # Whether triggering constitutes a safe ssRF burn is the job
        # of the future burn sequencer / interlock IOC.
        self.pvs["SWEEP_TRIGGER_CMD"] = builder.boolOut(
            "SWEEP_TRIGGER_CMD",
            ZNAM="IDLE",
            ONAM="TRIGGER",
            always_update=True,
            on_update_name=self.do_sets,
        )

    # ------------------------------------------------------------------
    # Alarm grouping
    # ------------------------------------------------------------------

    def _channel_pvs(self, channel):
        """Return SMC measurement/control PVs affected by device health."""

        health_pvs = {
            "COMM",
            "ERROR",
            "LAST_EVENT",
            "LAST_OK",
            "WRITE_ENABLED",
        }

        return [
            name
            for name in self.pvs
            if name not in health_pvs
        ]

    # ------------------------------------------------------------------
    # Startup / polling
    # ------------------------------------------------------------------

    def read_outs(self):
        """Populate the IOC from actual instrument state.

        This function performs queries only. It must never modify hardware.
        """

        if self.t is None:
            return

        identity = self.t.idn()

        if "SMC100A" not in identity.upper():
            raise OSError(
                f"unexpected instrument identity: {identity}"
            )

        self.pvs["IDN"].set(identity[:39])

        state = self.t.read_state()
        sweep = self.t.read_sweep()

        self._apply_state(state, sweep)

    def read_all(self, channel, index):
        """One polling cycle from the instrument."""

        state = self.t.read_state()
        sweep = self.t.read_sweep()

        # Keep operator command widgets synchronized with the hardware,
        # without causing write callbacks.
        self._sync_commands(state, sweep)

        return {
            "FREQ_RBV": state["freq_mhz"],
            "LEVEL_RBV": state["level_dbm"],
            "RF_RBV": int(state["rf_on"]),
            "FM_RBV": int(state["fm_on"]),
            "FM_SOURCE_RBV": int(state["fm_source"] == "EXT"),
            "FM_DEV_RBV": state["fm_dev_khz"],
            "SWEEP_START_RBV": sweep["start_dbm"],
            "SWEEP_STOP_RBV": sweep["stop_dbm"],
            "SWEEP_STEP_RBV": sweep["step_db"],
            "SWEEP_DWELL_RBV": sweep["dwell_ms"],
            "SWEEP_SHAPE_RBV": int(sweep["shape"].startswith("TRI")),
            "SWEEP_ARM_RBV": int(state["pow_mode"] == "SWE"),
        }

    def _apply_state(self, state, sweep):
        """Apply startup state to both command and readback PVs."""

        readbacks = {
            "FREQ_RBV": state["freq_mhz"],
            "LEVEL_RBV": state["level_dbm"],
            "RF_RBV": int(state["rf_on"]),
            "FM_RBV": int(state["fm_on"]),
            "FM_SOURCE_RBV": int(state["fm_source"] == "EXT"),
            "FM_DEV_RBV": state["fm_dev_khz"],
            "SWEEP_START_RBV": sweep["start_dbm"],
            "SWEEP_STOP_RBV": sweep["stop_dbm"],
            "SWEEP_STEP_RBV": sweep["step_db"],
            "SWEEP_DWELL_RBV": sweep["dwell_ms"],
            "SWEEP_SHAPE_RBV": int(sweep["shape"].startswith("TRI")),
            "SWEEP_ARM_RBV": int(state["pow_mode"] == "SWE"),
        }

        for name, value in readbacks.items():
            self.pvs[name].set(value)

        self._sync_commands(state, sweep)

    def _sync_commands(self, state, sweep):
        """Synchronize control widgets to confirmed hardware state."""

        values = {
            "FREQ_CMD": state["freq_mhz"],
            "LEVEL_CMD": state["level_dbm"],
            "RF_CMD": int(state["rf_on"]),
            "FM_CMD": int(state["fm_on"]),
            "FM_SOURCE_CMD": int(state["fm_source"] == "EXT"),
            "FM_DEV_CMD": state["fm_dev_khz"],
            "SWEEP_START_CMD": sweep["start_dbm"],
            "SWEEP_STOP_CMD": sweep["stop_dbm"],
            "SWEEP_STEP_CMD": sweep["step_db"],
            "SWEEP_DWELL_CMD": sweep["dwell_ms"],
            "SWEEP_SHAPE_CMD": int(sweep["shape"].startswith("TRI")),
            "SWEEP_ARM_CMD": int(state["pow_mode"] == "SWE"),
        }

        for name, value in values.items():
            self.store(name, value)

        # Momentary command is always idle unless actively triggered.
        self.store("SWEEP_TRIGGER_CMD", 0)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def do_sets(self, new_value, pv):
        pv_name = pv.removeprefix(self.device_name + ":")

        if not self.can_write(pv_name):
            if pv_name == "SWEEP_TRIGGER_CMD":
                self.store("SWEEP_TRIGGER_CMD", 0)
            return

        try:
            if pv_name == "FREQ_CMD":
                mhz = self._bounded(
                    "frequency",
                    new_value,
                    self.settings.get(
                        "freq_limit_mhz",
                        [0.1, 3200.0],
                    ),
                )

                confirmed = self.t.set_frequency_mhz(mhz)
                self.store("FREQ_CMD", confirmed)

            elif pv_name == "LEVEL_CMD":
                dbm = self._bounded(
                    "RF level",
                    new_value,
                    self.settings.get(
                        "level_limit_dbm",
                        [-145.0, 10.0],
                    ),
                )

                confirmed = self.t.set_level_dbm(dbm)
                self.store("LEVEL_CMD", confirmed)

            elif pv_name == "RF_CMD":
                confirmed = self.t.set_rf(bool(new_value))
                self.store("RF_CMD", int(confirmed))

            elif pv_name == "FM_CMD":
                confirmed = self.t.set_fm(bool(new_value))
                self.store("FM_CMD", int(confirmed))

            elif pv_name == "FM_SOURCE_CMD":
                source = "EXT" if bool(new_value) else "INT"
                confirmed = self.t.set_fm_source(source)

                self.store(
                    "FM_SOURCE_CMD",
                    int(confirmed == "EXT"),
                )

            elif pv_name == "FM_DEV_CMD":
                confirmed = self.t.set_fm_deviation_khz(
                    float(new_value)
                )
                self.store("FM_DEV_CMD", confirmed)

            elif pv_name == "SWEEP_START_CMD":
                value = self._bounded(
                    "sweep start",
                    new_value,
                    self.settings.get(
                        "level_limit_dbm",
                        [-145.0, 10.0],
                    ),
                )

                confirmed = self.t.set_sweep(
                    start_dbm=value
                )["start_dbm"]

                self.store("SWEEP_START_CMD", confirmed)

            elif pv_name == "SWEEP_STOP_CMD":
                value = self._bounded(
                    "sweep stop",
                    new_value,
                    self.settings.get(
                        "level_limit_dbm",
                        [-145.0, 10.0],
                    ),
                )

                confirmed = self.t.set_sweep(
                    stop_dbm=value
                )["stop_dbm"]

                self.store("SWEEP_STOP_CMD", confirmed)

            elif pv_name == "SWEEP_STEP_CMD":
                confirmed = self.t.set_sweep(
                    step_db=float(new_value)
                )["step_db"]

                self.store("SWEEP_STEP_CMD", confirmed)

            elif pv_name == "SWEEP_DWELL_CMD":
                value = float(new_value)

                if value <= 0:
                    raise ValueError(
                        "sweep dwell must be positive"
                    )

                confirmed = self.t.set_sweep(
                    dwell_ms=value
                )["dwell_ms"]

                self.store("SWEEP_DWELL_CMD", confirmed)

            elif pv_name == "SWEEP_SHAPE_CMD":
                shape = "TRI" if bool(new_value) else "SAW"

                confirmed = self.t.set_sweep(
                    shape=shape
                )["shape"]

                self.store(
                    "SWEEP_SHAPE_CMD",
                    int(confirmed.startswith("TRI")),
                )

            elif pv_name == "SWEEP_ARM_CMD":
                confirmed = self.t.arm_sweep(
                    bool(new_value)
                )

                self.store(
                    "SWEEP_ARM_CMD",
                    int(confirmed),
                )

            elif pv_name == "SWEEP_TRIGGER_CMD":
                if not bool(new_value):
                    return

                self.t.trigger_sweep()

                # Momentary command.
                self.store("SWEEP_TRIGGER_CMD", 0)

            else:
                raise ValueError(
                    f"unsupported SMC100A command PV: {pv_name}"
                )

            errors = self.t.errors()

            if errors:
                message = errors[0]
                self.pvs["ERROR"].set(message[:39])

                log.warning(
                    "%s: instrument error: %s",
                    pv_name,
                    "; ".join(errors),
                )

            else:
                self._set_comm(True)

            log.info(
                "%s <- %s",
                pv_name,
                new_value,
            )

        except ValueError as exc:
            self.pvs["ERROR"].set(str(exc)[:39])

            log.warning(
                "%s: rejected command: %s",
                pv_name,
                exc,
            )

            if pv_name == "SWEEP_TRIGGER_CMD":
                self.store("SWEEP_TRIGGER_CMD", 0)

        except OSError as exc:
            self._set_comm(False, str(exc))

            log.error(
                "%s: hardware command failed: %s",
                pv_name,
                exc,
            )

            if pv_name == "SWEEP_TRIGGER_CMD":
                self.store("SWEEP_TRIGGER_CMD", 0)

            self.reconnect()

    @staticmethod
    def _bounded(name, value, limits):
        low = float(limits[0])
        high = float(limits[1])
        value = float(value)

        if not low <= value <= high:
            raise ValueError(
                f"{name} must be in [{low}, {high}], got {value}"
            )

        return value


class DeviceConnection(ScpiConnection):
    """SMC100A-specific SCPI vocabulary."""

    def read_state(self):
        return {
            "freq_mhz": self.query_float("FREQ?") / 1e6,
            "level_dbm": self.query_float("POW?"),
            "fm_dev_khz": self.query_float("FM:DEV?") / 1e3,
            "rf_on": self.query_bool("OUTP?"),
            "fm_on": self.query_bool("FM:STAT?"),
            "pow_mode": (
                self.query("POW:MODE?")
                .strip()
                .upper()[:3]
            ),
            "fm_source": (
                self.query("FM:SOUR?")
                .strip()
                .upper()[:3]
            ),
        }

    def read_sweep(self):
        return {
            "start_dbm": self.query_float(
                "POW:STAR?"
            ),
            "stop_dbm": self.query_float(
                "POW:STOP?"
            ),
            "step_db": self.query_float(
                "SWE:POW:STEP?"
            ),
            "dwell_ms": (
                self.query_float("SWE:POW:DWEL?")
                * 1e3
            ),
            "shape": (
                self.query("SWE:POW:SHAP?")
                .strip()
                .upper()
            ),
        }

    # --------------------------------------------------------------
    # Setters always write, then query the instrument for confirmation.
    # --------------------------------------------------------------

    def set_frequency_mhz(self, mhz):
        # Preserve Farooq's important behavior:
        # fixed-carrier frequency changes must be made in CW frequency mode.
        self.write("FREQ:MODE CW")
        self.write(
            f"FREQ {float(mhz) * 1e6:.3f}"
        )

        return (
            self.query_float("FREQ?")
            / 1e6
        )

    def set_level_dbm(self, dbm):
        self.write(
            f"POW {float(dbm):.2f}"
        )

        return self.query_float("POW?")

    def set_rf(self, enabled):
        self.write(
            f"OUTP {'ON' if enabled else 'OFF'}"
        )

        return self.query_bool("OUTP?")

    def set_fm(self, enabled):
        self.write(
            f"FM:STAT {'ON' if enabled else 'OFF'}"
        )

        return self.query_bool("FM:STAT?")

    def set_fm_deviation_khz(self, khz):
        hz = float(khz) * 1e3

        self.write(
            f"FM:DEV {hz:.1f}"
        )

        # Preserve Farooq's external-FM support.
        try:
            self.write(
                f"FM:EXT:DEV {hz:.1f}"
            )
        except OSError:
            pass

        return (
            self.query_float("FM:DEV?")
            / 1e3
        )

    def set_fm_source(self, source="EXT"):
        source = str(source).strip().upper()

        if source not in FM_SOURCES:
            raise ValueError(
                f"FM source must be one of {FM_SOURCES}, got {source}"
            )

        self.write(
            f"FM:SOUR {source}"
        )

        confirmed = (
            self.query("FM:SOUR?")
            .strip()
            .upper()[:3]
        )

        if confirmed not in FM_SOURCES:
            raise OSError(
                f"unexpected FM source readback: {confirmed}"
            )

        return confirmed

    def set_sweep(
        self,
        start_dbm=None,
        stop_dbm=None,
        step_db=None,
        dwell_ms=None,
        shape=None,
    ):
        if start_dbm is not None:
            self.write(
                f"POW:STAR {float(start_dbm):.2f}"
            )

        if stop_dbm is not None:
            self.write(
                f"POW:STOP {float(stop_dbm):.2f}"
            )

        if step_db is not None:
            self.write(
                f"SWE:POW:STEP {float(step_db):.2f}"
            )

        if dwell_ms is not None:
            self.write(
                f"SWE:POW:DWEL "
                f"{float(dwell_ms) / 1e3:.4f}"
            )

        if shape is not None:
            self.write(
                f"SWE:POW:SHAP {str(shape).upper()}"
            )

        return self.read_sweep()

    def trigger_sweep(self):
        """Trigger one level sweep.

        Preserves Farooq's experimentally established sequence:
            SWE:RES
            POW:MODE SWE
            wait 50 ms
            *TRG
        """

        self.write("SWE:RES")
        self.write("POW:MODE SWE")

        # Instrument-specific settling requirement retained from
        # the previous BLAST implementation.
        time.sleep(0.05)

        self.write("*TRG")

    def arm_sweep(self, armed):
        if armed:
            self.write("SWE:RES")
            self.write("POW:MODE SWE")

        else:
            self.write("*CLS")
            self.write("SWE:RES")
            self.write("POW:MODE CW")

        return (
            self.query("POW:MODE?")
            .strip()
            .upper()
            .startswith("SWE")
        )


Device.connection_class = DeviceConnection