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

from genesis_common.visa_base import VisaConnection


log = logging.getLogger(__name__)

MAX_ARB_POINTS = 8192


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
