"""
Genesis Keysight WaveGen controller.

Low-level device support for the Keysight oscilloscope WaveGen used as
the external-FM source for the Genesis ssRF system.

This module deliberately contains device control only.

It does NOT:
- fit NMR data
- generate adaptive FM waveforms
- calculate SMC dwell timing
- operate RF/NMR switches
- execute burns

Those responsibilities belong to higher Genesis layers.
"""

import logging
import math
import time

import numpy as np

from softioc import alarm, builder

from genesis_common.visa_base import VisaConnection, VisaDevice


log = logging.getLogger(__name__)

MAX_ARB_POINTS = 8192



class Device(VisaDevice):
    """EPICS-facing Genesis Keysight WaveGen device."""

    # Assigned after DeviceConnection is defined.
    connection_class = None

    READBACK_PVS = (
        "OUTPUT_RBV",
        "PERIOD_RBV",
        "VHIGH_RBV",
        "VLOW_RBV",
    )

    def _create_pvs(self):
        # --------------------------------------------------------------
        # Identity
        # --------------------------------------------------------------

        self.pvs["IDN"] = builder.longStringIn(
            "IDN",
            length=128,
        )

        # --------------------------------------------------------------
        # WaveGen output
        # --------------------------------------------------------------

        self.pvs["OUTPUT_CMD"] = builder.boolOut(
            "OUTPUT_CMD",
            ZNAM="OFF",
            ONAM="ON",
            on_update_name=self.do_sets,
        )

        self.pvs["OUTPUT_RBV"] = builder.boolIn(
            "OUTPUT_RBV",
            ZNAM="OFF",
            ONAM="ON",
        )

        # --------------------------------------------------------------
        # Waveform period
        # --------------------------------------------------------------

        self.pvs["PERIOD_CMD"] = builder.aOut(
            "PERIOD_CMD",
            EGU="s",
            PREC=6,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["PERIOD_RBV"] = builder.aIn(
            "PERIOD_RBV",
            EGU="s",
            PREC=6,
            **self.analog,
        )

        # --------------------------------------------------------------
        # Physical waveform voltage scale
        # --------------------------------------------------------------

        self.pvs["VHIGH_CMD"] = builder.aOut(
            "VHIGH_CMD",
            EGU="V",
            PREC=4,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["VHIGH_RBV"] = builder.aIn(
            "VHIGH_RBV",
            EGU="V",
            PREC=4,
            **self.analog,
        )

        self.pvs["VLOW_CMD"] = builder.aOut(
            "VLOW_CMD",
            EGU="V",
            PREC=4,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["VLOW_RBV"] = builder.aIn(
            "VLOW_RBV",
            EGU="V",
            PREC=4,
            **self.analog,
        )

        # --------------------------------------------------------------
        # Waveform staging
        #
        # WAVEFORM_CMD only stages data in IOC memory.
        # It never writes to the Keysight.
        #
        # LOAD_CMD is the explicit hardware commit operation.
        # --------------------------------------------------------------

        self._staged_waveform = np.array(
            [],
            dtype=np.float32,
        )

        self._waveform_id = 0

        self.pvs["WAVEFORM_CMD"] = builder.WaveformOut(
            "WAVEFORM_CMD",
            length=MAX_ARB_POINTS,
            datatype="float32",
            on_update_name=self.stage_waveform,
        )

        self.pvs["WAVEFORM_POINTS"] = builder.longIn(
            "WAVEFORM_POINTS",
            LOPR=0,
            HOPR=MAX_ARB_POINTS,
        )

        self.pvs["LOAD_CMD"] = builder.boolOut(
            "LOAD_CMD",
            ZNAM="Idle",
            ONAM="Load",
            on_update_name=self.do_sets,
        )

        self.pvs["ARB_POINTS_RBV"] = builder.longIn(
            "ARB_POINTS_RBV",
            LOPR=0,
            HOPR=MAX_ARB_POINTS,
        )

        self.pvs["WAVEFORM_READY"] = builder.boolIn(
            "WAVEFORM_READY",
            ZNAM="Not ready",
            ONAM="Ready",
        )

        self.pvs["WAVEFORM_ID"] = builder.longIn(
            "WAVEFORM_ID",
            LOPR=0,
        )

    def stage_waveform(self, new_value, pv):
        """Stage normalized waveform samples in IOC memory only."""

        try:
            values = np.asarray(
                new_value,
                dtype=np.float32,
            ).reshape(-1)

            if len(values) < 2:
                raise ValueError(
                    "waveform requires at least 2 points"
                )

            if len(values) > MAX_ARB_POINTS:
                raise ValueError(
                    "waveform exceeds "
                    f"{MAX_ARB_POINTS} points"
                )

            if not np.all(
                np.isfinite(values)
            ):
                raise ValueError(
                    "waveform contains non-finite values"
                )

            if np.any(values < -1.0) or np.any(
                values > 1.0
            ):
                raise ValueError(
                    "waveform samples must be "
                    "normalized to [-1, 1]"
                )

            self._staged_waveform = (
                values.copy()
            )

            self.pvs[
                "WAVEFORM_POINTS"
            ].set(
                len(values)
            )

            # A newly staged waveform has not yet been
            # committed to the instrument.
            self.pvs[
                "WAVEFORM_READY"
            ].set(0)

            self.record_event(
                f"Waveform staged: {len(values)} pts"
            )

            self.pvs[
                "WAVEFORM_CMD"
            ].set_alarm(
                severity=alarm.NO_ALARM,
                alarm=alarm.NO_ALARM,
            )

        except Exception as exc:
            self.pvs[
                "WAVEFORM_READY"
            ].set(0)

            self.record_event(
                "Waveform staging failed"
            )

            self.pvs[
                "WAVEFORM_CMD"
            ].set_alarm(
                severity=alarm.MAJOR_ALARM,
                alarm=alarm.WRITE_ALARM,
            )

            log.error(
                "%s: waveform staging failed: %s",
                self.device_name,
                exc,
            )

    def _post_connect(self):
        """Validate identity and synchronize startup readbacks.

        This method performs reads only. IOC connection must not modify
        physical WaveGen state.
        """

        if self.t is None:
            return

        identity = self.t.idn()

        identity_upper = identity.upper()

        if (
            "KEYSIGHT" not in identity_upper
            and "AGILENT" not in identity_upper
        ):
            raise OSError(
                "unexpected instrument identity: "
                f"{identity}"
            )

        self.pvs["IDN"].set(
            identity
        )

        state = self.t.read_state()

        self._apply_state(state)

        # Connection/reconnection alone does not prove that the staged
        # waveform matches the instrument's currently loaded ARB.
        self.pvs["WAVEFORM_READY"].set(0)
        self.pvs["ARB_POINTS_RBV"].set(0)

    def read_all(self, channel, index):
        """Perform one scalar polling cycle."""

        state = self.t.read_state()

        # Synchronize operator command widgets with confirmed hardware
        # state without causing another write callback.
        self._sync_commands(state)

        return {
            "OUTPUT_RBV": int(
                state["output_on"]
            ),
            "PERIOD_RBV": state[
                "period_s"
            ],
            "VHIGH_RBV": state[
                "vhigh_v"
            ],
            "VLOW_RBV": state[
                "vlow_v"
            ],
        }

    def _apply_state(self, state):
        """Apply startup state to command and readback PVs."""

        readbacks = {
            "OUTPUT_RBV": int(
                state["output_on"]
            ),
            "PERIOD_RBV": state[
                "period_s"
            ],
            "VHIGH_RBV": state[
                "vhigh_v"
            ],
            "VLOW_RBV": state[
                "vlow_v"
            ],
        }

        for name, value in readbacks.items():
            self.pvs[name].set(value)

        self._sync_commands(state)

    def _sync_commands(self, state):
        """Synchronize command PVs to confirmed hardware state."""

        values = {
            "OUTPUT_CMD": int(
                state["output_on"]
            ),
            "PERIOD_CMD": state[
                "period_s"
            ],
            "VHIGH_CMD": state[
                "vhigh_v"
            ],
            "VLOW_CMD": state[
                "vlow_v"
            ],
        }

        for name, value in values.items():
            self.store(
                name,
                value,
            )

    def _set_readback_alarm(self, fault):
        severity = (
            alarm.MAJOR_ALARM
            if fault
            else alarm.NO_ALARM
        )

        condition = (
            alarm.READ_ALARM
            if fault
            else alarm.NO_ALARM
        )

        for name in self.READBACK_PVS:
            self.pvs[name].set_alarm(
                severity=severity,
                alarm=condition,
            )

    async def do_reads(self):
        """Poll hardware while keeping the IOC alive on disconnect."""

        if self.t is None:
            if not self.reconnect():
                return False

        if self.t is None:
            return False

        try:
            readings = self.read_all(
                None,
                0,
            )

            for name, value in readings.items():
                self.pvs[name].set(
                    value
                )

            self._set_readback_alarm(
                False
            )

            self._set_comm(
                True
            )

            self.pvs["LAST_OK"].set(
                time.time()
            )

            return True

        except Exception as exc:
            message = str(exc)

            log.warning(
                "%s: WaveGen read failed: %s",
                self.device_name,
                message,
            )

            self._set_comm(
                False,
                message,
            )

            self._set_readback_alarm(
                True
            )

            # Next polling cycle enters Genesis reconnect/backoff.
            self._close_connection()

            return False

    def do_sets(self, new_value, pv):
        """Handle operator commands through the Genesis write gate."""

        pv_name = pv.removeprefix(
            self.device_name + ":"
        )

        if not self.can_write(
            pv_name
        ):
            if pv_name == "LOAD_CMD":
                self.store(
                    "LOAD_CMD",
                    0,
                )

            return

        try:
            if pv_name == "LOAD_CMD":
                # LOAD_CMD is momentary. A zero write means idle.
                if not bool(new_value):
                    self.store(
                        "LOAD_CMD",
                        0,
                    )
                    return

                if len(
                    self._staged_waveform
                ) < 2:
                    raise ValueError(
                        "no valid waveform staged"
                    )

                # Use confirmed hardware scalar settings rather than
                # potentially stale command-PV values.
                state = self.t.read_state()

                result = self.t.upload_arb(
                    values=self._staged_waveform,
                    period_s=state[
                        "period_s"
                    ],
                    vhigh_v=state[
                        "vhigh_v"
                    ],
                    vlow_v=state[
                        "vlow_v"
                    ],
                )

                confirmed_points = int(
                    result[
                        "arb_points"
                    ]
                )

                if confirmed_points != len(
                    self._staged_waveform
                ):
                    raise OSError(
                        "WaveGen ARB point-count "
                        "verification failed"
                    )

                self.pvs[
                    "ARB_POINTS_RBV"
                ].set(
                    confirmed_points
                )

                self.pvs[
                    "WAVEFORM_READY"
                ].set(1)

                self._waveform_id += 1

                self.pvs[
                    "WAVEFORM_ID"
                ].set(
                    self._waveform_id
                )

                # upload_arb() also confirms the scalar state.
                self._apply_state(
                    result
                )

                self.store(
                    "LOAD_CMD",
                    0,
                )

                self.record_event(
                    "Waveform loaded: "
                    f"{confirmed_points} pts"
                )

            elif pv_name == "OUTPUT_CMD":
                confirmed = (
                    self.t.set_output(
                        bool(new_value)
                    )
                )

                self.store(
                    "OUTPUT_CMD",
                    int(confirmed),
                )

                self.pvs[
                    "OUTPUT_RBV"
                ].set(
                    int(confirmed)
                )

                self.record_event(
                    "WaveGen output "
                    + (
                        "ON"
                        if confirmed
                        else "OFF"
                    )
                )

            elif pv_name == "PERIOD_CMD":
                value = float(
                    new_value
                )

                if (
                    not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError(
                        "period must be finite "
                        "and positive"
                    )

                confirmed = (
                    self.t.set_period_s(
                        value
                    )
                )

                self.store(
                    "PERIOD_CMD",
                    confirmed,
                )

                self.pvs[
                    "PERIOD_RBV"
                ].set(
                    confirmed
                )

                self.record_event(
                    "WaveGen period updated"
                )

            elif pv_name == "VHIGH_CMD":
                value = float(
                    new_value
                )

                if not math.isfinite(
                    value
                ):
                    raise ValueError(
                        "VHIGH must be finite"
                    )

                current = (
                    self.t.read_state()
                )

                if value <= current[
                    "vlow_v"
                ]:
                    raise ValueError(
                        "VHIGH must exceed VLOW"
                    )

                confirmed = (
                    self.t.set_vhigh_v(
                        value
                    )
                )

                self.store(
                    "VHIGH_CMD",
                    confirmed,
                )

                self.pvs[
                    "VHIGH_RBV"
                ].set(
                    confirmed
                )

                self.record_event(
                    "WaveGen VHIGH updated"
                )

            elif pv_name == "VLOW_CMD":
                value = float(
                    new_value
                )

                if not math.isfinite(
                    value
                ):
                    raise ValueError(
                        "VLOW must be finite"
                    )

                current = (
                    self.t.read_state()
                )

                if value >= current[
                    "vhigh_v"
                ]:
                    raise ValueError(
                        "VLOW must be below VHIGH"
                    )

                confirmed = (
                    self.t.set_vlow_v(
                        value
                    )
                )

                self.store(
                    "VLOW_CMD",
                    confirmed,
                )

                self.pvs[
                    "VLOW_RBV"
                ].set(
                    confirmed
                )

                self.record_event(
                    "WaveGen VLOW updated"
                )

            else:
                raise ValueError(
                    "unknown WaveGen command PV: "
                    f"{pv_name}"
                )

            self.pvs[
                pv_name
            ].set_alarm(
                severity=alarm.NO_ALARM,
                alarm=alarm.NO_ALARM,
            )

            self._set_comm(
                True
            )

        except Exception as exc:
            message = str(exc)

            if pv_name == "LOAD_CMD":
                self.store(
                    "LOAD_CMD",
                    0,
                )

                self.pvs[
                    "WAVEFORM_READY"
                ].set(0)

            log.error(
                "%s: command %s failed: %s",
                self.device_name,
                pv_name,
                message,
            )

            self._set_comm(
                False,
                message,
            )

            self.record_event(
                f"{pv_name} failed"
            )

            if pv_name in self.pvs:
                self.pvs[
                    pv_name
                ].set_alarm(
                    severity=alarm.MAJOR_ALARM,
                    alarm=alarm.WRITE_ALARM,
                )

            self._close_connection()


class DeviceConnection(VisaConnection):
    """Keysight WaveGen SCPI vocabulary."""

    # ------------------------------------------------------------------
    # Readback
    # ------------------------------------------------------------------

    def read_state(self):
        """Read the scalar WaveGen state."""

        return {
            "idn": self.idn(),
            "function": (
                self.query(":WGEN:FUNC?")
                .strip()
                .upper()
            ),
            "output_on": self.query_bool(
                ":WGEN:OUTP?"
            ),
            "period_s": self.query_float(
                ":WGEN:PER?"
            ),
            "vhigh_v": self.query_float(
                ":WGEN:VOLT:HIGH?"
            ),
            "vlow_v": self.query_float(
                ":WGEN:VOLT:LOW?"
            ),
        }

    # ------------------------------------------------------------------
    # Scalar setters
    #
    # Genesis convention:
    #     write command
    #     synchronize
    #     query instrument for confirmed value
    # ------------------------------------------------------------------

    def set_function_arb(self):
        """Select arbitrary-waveform mode."""

        self.write(":WGEN:FUNC ARB")
        self.opc()

        return (
            self.query(":WGEN:FUNC?")
            .strip()
            .upper()
        )

    def set_period_s(self, seconds):
        """Set one complete WaveGen waveform period."""

        seconds = float(seconds)

        if seconds <= 0:
            raise ValueError(
                "WaveGen period must be positive"
            )

        self.write(
            f":WGEN:PER {seconds:.12g}"
        )
        self.opc()

        return self.query_float(
            ":WGEN:PER?"
        )

    def set_vhigh_v(self, volts):
        """Set WaveGen high-level voltage."""

        volts = float(volts)

        self.write(
            f":WGEN:VOLT:HIGH {volts:.12g}"
        )
        self.opc()

        return self.query_float(
            ":WGEN:VOLT:HIGH?"
        )

    def set_vlow_v(self, volts):
        """Set WaveGen low-level voltage."""

        volts = float(volts)

        self.write(
            f":WGEN:VOLT:LOW {volts:.12g}"
        )
        self.opc()

        return self.query_float(
            ":WGEN:VOLT:LOW?"
        )

    def set_output(self, enabled):
        """Enable or disable the physical WaveGen output."""

        enabled = bool(enabled)

        self.write(
            ":WGEN:OUTP "
            + ("ON" if enabled else "OFF")
        )
        self.opc()

        return self.query_bool(
            ":WGEN:OUTP?"
        )

    # ------------------------------------------------------------------
    # Safe ARB-mode preparation
    # ------------------------------------------------------------------

    def prepare_arb_mode(self):
        """Prepare WaveGen for Genesis ARB operation.

        This mirrors Chhetra's validated setup philosophy but deliberately
        leaves the physical output OFF.

        Waveform data upload is added in the next milestone.
        """

        self.write(":WGEN:OUTP OFF")
        self.opc()

        self.write(":WGEN:FUNC ARB")
        self.opc()

        self.write(":FUNC:ARB:FILT NORM")
        self.opc()

        self.write(":WGEN:ARB:INTerpolate ON")

        self.write(":WGEN:OUTP:LOAD INF")
        self.write(":WGEN:OUTP:MODE NORM")

        # Preserve Chhetra's tested UNH command.
        self.write(":WGEN:OUTP:INV OFF")

        self.opc()

        errors = self.errors()

        if errors:
            raise OSError(
                "Keysight ARB preparation error: "
                + "; ".join(errors)
            )

        state = self.read_state()

        if state["output_on"]:
            raise OSError(
                "Keysight output unexpectedly ON "
                "after ARB preparation"
            )

        return state

    def read_arb_points(self):
        """Return the number of ARB points loaded on the instrument."""

        raw = self.query(
            ":WGEN:ARB:DATA:ATTRibute:POINts?"
        )

        try:
            return int(float(raw))

        except ValueError as exc:
            raise OSError(
                "bad Keysight ARB point-count "
                f"reply: {raw!r}"
            ) from exc

    @staticmethod
    def _validate_arb_values(values):
        """Validate normalized Genesis ARB samples."""

        values = [
            float(value)
            for value in values
        ]

        if len(values) < 2:
            raise ValueError(
                "ARB waveform requires at least 2 points"
            )

        if len(values) > MAX_ARB_POINTS:
            raise ValueError(
                "ARB waveform exceeds "
                f"{MAX_ARB_POINTS} points"
            )

        for value in values:
            if not math.isfinite(value):
                raise ValueError(
                    "ARB waveform contains "
                    "non-finite values"
                )

            if not -1.0 <= value <= 1.0:
                raise ValueError(
                    "ARB waveform samples must "
                    "be normalized to [-1, 1]"
                )

        return values

    def upload_arb(
        self,
        values,
        period_s,
        vhigh_v,
        vlow_v,
        binary_first=True,
    ):
        """Upload and configure one Genesis FM waveform.

        The waveform samples are normalized to [-1, +1].
        VHIGH/VLOW define the physical modulation-voltage scale.

        The physical WaveGen output is forced OFF before and after
        configuration. This method does not perform a burn.
        """

        values = self._validate_arb_values(
            values
        )

        period_s = float(period_s)
        vhigh_v = float(vhigh_v)
        vlow_v = float(vlow_v)

        if period_s <= 0:
            raise ValueError(
                "WaveGen period must be positive"
            )

        if not (
            math.isfinite(vhigh_v)
            and math.isfinite(vlow_v)
        ):
            raise ValueError(
                "WaveGen voltage limits "
                "must be finite"
            )

        if vlow_v >= vhigh_v:
            raise ValueError(
                "WaveGen VLOW must be less "
                "than VHIGH"
            )

        # ------------------------------------------------------
        # Safe configuration state
        # ------------------------------------------------------

        self.safe_output_off()

        self.write(":WGEN:FUNC ARB")
        self.opc()

        self.write(":FUNC:ARB:FILT NORM")
        self.opc()

        self.write(
            ":WGEN:ARB:DATA:CLEar"
        )
        self.opc()

        # ------------------------------------------------------
        # Waveform transfer
        #
        # Preserve Chhetra's tested behavior:
        # binary first, ASCII fallback.
        # ------------------------------------------------------

        transfer = "ascii"

        if binary_first:
            try:
                self.write_binary_values(
                    ":WGEN:ARB:DATA ",
                    values,
                    datatype="f",
                    is_big_endian=False,
                    close_on_error=False,
                )

                transfer = "binary"

            except OSError as exc:
                log.warning(
                    "Keysight binary ARB transfer "
                    "failed; falling back to ASCII: %s",
                    exc,
                )

                self.write_ascii_values(
                    ":WGEN:ARB:DATA ",
                    values,
                )

                transfer = "ascii"

        else:
            self.write_ascii_values(
                ":WGEN:ARB:DATA ",
                values,
            )

        self.opc()

        errors = self.errors()

        if errors:
            raise OSError(
                "Keysight ARB upload error: "
                + "; ".join(errors)
            )

        # ------------------------------------------------------
        # Verify the instrument accepted all points
        # ------------------------------------------------------

        loaded_points = (
            self.read_arb_points()
        )

        if loaded_points != len(values):
            raise OSError(
                "Keysight ARB point-count mismatch: "
                f"sent {len(values)}, "
                f"instrument reports {loaded_points}"
            )

        # ------------------------------------------------------
        # Finish WaveGen configuration
        # ------------------------------------------------------

        self.write(
            ":WGEN:ARB:INTerpolate ON"
        )

        self.set_vhigh_v(
            vhigh_v
        )

        self.set_vlow_v(
            vlow_v
        )

        self.set_period_s(
            period_s
        )

        self.write(
            ":WGEN:OUTP:LOAD INF"
        )

        self.write(
            ":WGEN:OUTP:MODE NORM"
        )

        # Preserve Chhetra's tested command.
        self.write(
            ":WGEN:OUTP:INV OFF"
        )

        self.safe_output_off()

        self.opc()

        errors = self.errors()

        if errors:
            raise OSError(
                "Keysight ARB configuration error: "
                + "; ".join(errors)
            )

        state = self.read_state()

        if state["output_on"]:
            raise OSError(
                "Keysight output unexpectedly ON "
                "after waveform upload"
            )

        return {
            **state,
            "arb_points": loaded_points,
            "transfer": transfer,
        }

    def safe_output_off(self):
        """Best-effort explicit WaveGen output OFF."""

        self.write(":WGEN:OUTP OFF")
        self.opc()

        confirmed = self.query_bool(
            ":WGEN:OUTP?"
        )

        if confirmed:
            raise OSError(
                "Keysight WaveGen output remained ON"
            )

        return False

# Bind the EPICS-facing device to the validated Keysight connection.
Device.connection_class = DeviceConnection
