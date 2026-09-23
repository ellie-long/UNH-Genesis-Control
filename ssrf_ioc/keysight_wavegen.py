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

from __future__ import annotations

from genesis_common.visa_base import VisaConnection


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
