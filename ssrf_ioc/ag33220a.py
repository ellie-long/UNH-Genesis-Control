"""
Genesis Phase-I IOC driver for the Agilent / Keysight 33220A.

The 33220A provides the hardware-timed trigger/burst pulse used by the
Genesis ssRF burn chain.

Architecture:

    Agilent 33220A
        |
    raw TCP / SCPI
        |
    DeviceConnection
        |
    Device
        |
    master_ioc.py
        |
    EPICS PVs

Safety rules:
- IOC startup is read-only unless write_enabled=true.
- Startup/polling performs queries only.
- Command and readback PVs are separate.
- Polling synchronizes command widgets with confirmed hardware state
  using process=False.
- FIRE_CMD is momentary and must never be archived/restored.
- Higher-level burn sequencing/interlocking does not belong here.
"""

from __future__ import annotations

import logging

from softioc import builder

from genesis_common.scpi_base import ScpiConnection, ScpiDevice


log = logging.getLogger(__name__)


class Device(ScpiDevice):
    """EPICS-facing Genesis Agilent 33220A device."""

    connection_class = None

    def _create_pvs(self):
        freq_low, freq_high = self.settings.get(
            "pulse_freq_limit_hz",
            [0.001, 1000.0],
        )

        ampl_low, ampl_high = self.settings.get(
            "ampl_limit_vpp",
            [0.0, 5.0],
        )

        offset_low, offset_high = self.settings.get(
            "offset_limit_v",
            [-5.0, 5.0],
        )

        width_low, width_high = self.settings.get(
            "width_limit_s",
            [1e-6, 1.0],
        )

        # Identity / mode
        self.pvs["IDN"] = builder.stringIn("IDN")
        self.pvs["FUNCTION"] = builder.stringIn("FUNCTION")

        # Pulse frequency
        self.pvs["PULSE_FREQ_CMD"] = builder.aOut(
            "PULSE_FREQ_CMD",
            DRVL=float(freq_low),
            DRVH=float(freq_high),
            EGU="Hz",
            PREC=3,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["PULSE_FREQ_RBV"] = builder.aIn(
            "PULSE_FREQ_RBV",
            LOPR=float(freq_low),
            HOPR=float(freq_high),
            EGU="Hz",
            PREC=3,
            **self.analog,
        )

        # Amplitude
        self.pvs["AMPL_CMD"] = builder.aOut(
            "AMPL_CMD",
            DRVL=float(ampl_low),
            DRVH=float(ampl_high),
            EGU="Vpp",
            PREC=3,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["AMPL_RBV"] = builder.aIn(
            "AMPL_RBV",
            LOPR=float(ampl_low),
            HOPR=float(ampl_high),
            EGU="Vpp",
            PREC=3,
            **self.analog,
        )

        # Offset
        self.pvs["OFFSET_CMD"] = builder.aOut(
            "OFFSET_CMD",
            DRVL=float(offset_low),
            DRVH=float(offset_high),
            EGU="V",
            PREC=3,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["OFFSET_RBV"] = builder.aIn(
            "OFFSET_RBV",
            LOPR=float(offset_low),
            HOPR=float(offset_high),
            EGU="V",
            PREC=3,
            **self.analog,
        )

        # Pulse width
        self.pvs["WIDTH_CMD"] = builder.aOut(
            "WIDTH_CMD",
            DRVL=float(width_low),
            DRVH=float(width_high),
            EGU="s",
            PREC=6,
            on_update_name=self.do_sets,
            **self.analog,
        )

        self.pvs["WIDTH_RBV"] = builder.aIn(
            "WIDTH_RBV",
            LOPR=float(width_low),
            HOPR=float(width_high),
            EGU="s",
            PREC=6,
            **self.analog,
        )

        # Burst cycles
        self.pvs["NCYC_CMD"] = builder.longOut(
            "NCYC_CMD",
            on_update_name=self.do_sets,
        )

        self.pvs["NCYC_RBV"] = builder.longIn(
            "NCYC_RBV"
        )

        # Burst state
        self.pvs["BURST_CMD"] = builder.boolOut(
            "BURST_CMD",
            ZNAM="OFF",
            ONAM="ON",
            on_update_name=self.do_sets,
        )

        self.pvs["BURST_RBV"] = builder.boolIn(
            "BURST_RBV",
            ZNAM="OFF",
            ONAM="ON",
        )

        # Physical output state
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

        # Momentary trigger.
        self.pvs["FIRE_CMD"] = builder.boolOut(
            "FIRE_CMD",
            ZNAM="IDLE",
            ONAM="FIRE",
            always_update=True,
            on_update_name=self.do_sets,
        )

    # --------------------------------------------------------------
    # Alarm grouping
    # --------------------------------------------------------------

    def _channel_pvs(self, channel):
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

    # --------------------------------------------------------------
    # Startup / polling
    # --------------------------------------------------------------

    def read_outs(self):
        """Populate IOC state using queries only."""

        if self.t is None:
            return

        identity = self.t.idn()

        if "33220A" not in identity.upper():
            raise OSError(
                f"unexpected instrument identity: {identity}"
            )

        self.pvs["IDN"].set(identity[:39])

        state = self.t.read_state()
        self._apply_state(state)

    def read_all(self, channel, index):
        state = self.t.read_state()

        self._sync_commands(state)

        return {
            "PULSE_FREQ_RBV": state["freq_hz"],
            "AMPL_RBV": state["ampl_vpp"],
            "OFFSET_RBV": state["offset_v"],
            "WIDTH_RBV": state["width_s"],
            "NCYC_RBV": state["ncyc"],
            "BURST_RBV": int(state["burst_on"]),
            "OUTPUT_RBV": int(state["output_on"]),
            "FUNCTION": state["function"][:39],
        }

    def _apply_state(self, state):
        readbacks = {
            "PULSE_FREQ_RBV": state["freq_hz"],
            "AMPL_RBV": state["ampl_vpp"],
            "OFFSET_RBV": state["offset_v"],
            "WIDTH_RBV": state["width_s"],
            "NCYC_RBV": state["ncyc"],
            "BURST_RBV": int(state["burst_on"]),
            "OUTPUT_RBV": int(state["output_on"]),
            "FUNCTION": state["function"][:39],
        }

        for name, value in readbacks.items():
            self.pvs[name].set(value)

        self._sync_commands(state)

    def _sync_commands(self, state):
        values = {
            "PULSE_FREQ_CMD": state["freq_hz"],
            "AMPL_CMD": state["ampl_vpp"],
            "OFFSET_CMD": state["offset_v"],
            "WIDTH_CMD": state["width_s"],
            "NCYC_CMD": state["ncyc"],
            "BURST_CMD": int(state["burst_on"]),
            "OUTPUT_CMD": int(state["output_on"]),
        }

        for name, value in values.items():
            self.store(name, value)

        self.store("FIRE_CMD", 0)

    # --------------------------------------------------------------
    # Writes
    # --------------------------------------------------------------

    def do_sets(self, new_value, pv):
        pv_name = pv.removeprefix(
            self.device_name + ":"
        )

        if not self.can_write(pv_name):
            if pv_name == "FIRE_CMD":
                self.store("FIRE_CMD", 0)

            return

        operations = {
            "PULSE_FREQ_CMD": (
                self.t.set_pulse_freq,
                float,
            ),
            "AMPL_CMD": (
                self.t.set_amplitude,
                float,
            ),
            "OFFSET_CMD": (
                self.t.set_offset,
                float,
            ),
            "WIDTH_CMD": (
                self.t.set_width,
                float,
            ),
            "NCYC_CMD": (
                self.t.set_ncycles,
                int,
            ),
            "BURST_CMD": (
                self.t.set_burst,
                bool,
            ),
            "OUTPUT_CMD": (
                self.t.set_output,
                bool,
            ),
        }

        try:
            if pv_name == "FIRE_CMD":
                if not bool(new_value):
                    return

                self.t.fire()

                self.store(
                    "FIRE_CMD",
                    0,
                )

                self.record_event(
                    "Agilent burst fired"
                )

                return

            entry = operations.get(pv_name)

            if entry is None:
                raise ValueError(
                    f"unsupported command PV: {pv_name}"
                )

            operation, converter = entry

            confirmed = operation(
                converter(new_value)
            )

            if isinstance(confirmed, bool):
                confirmed = int(confirmed)

            self.store(
                pv_name,
                confirmed,
            )

            errors = self.t.errors()

            if errors:
                raise OSError(
                    errors[0]
                )

        except (OSError, ValueError) as exc:
            self._set_comm(
                False,
                str(exc),
            )

            self.record_event(
                f"Command failed: {pv_name}"
            )

            self._close_connection()

            log.error(
                "%s: command failed: %s",
                pv_name,
                exc,
            )


class DeviceConnection(ScpiConnection):
    """SCPI vocabulary used by the Agilent 33220A."""

    def read_state(self):
        return {
            "function": self.query(
                "FUNC?"
            ).strip().upper(),

            "freq_hz": self.query_float(
                "FREQ?"
            ),

            "ampl_vpp": self.query_float(
                "VOLT?"
            ),

            "offset_v": self.query_float(
                "VOLT:OFFS?"
            ),

            "width_s": self.query_float(
                "FUNC:PULS:WIDT?"
            ),

            "ncyc": int(
                float(
                    self.query(
                        "BURS:NCYC?"
                    )
                )
            ),

            "burst_on": self.query_bool(
                "BURS:STAT?"
            ),

            "output_on": self.query_bool(
                "OUTP?"
            ),
        }

    def set_pulse_freq(self, hz):
        self.write(
            f"FREQ {float(hz):.6f}"
        )

        return self.query_float(
            "FREQ?"
        )

    def set_amplitude(self, vpp):
        self.write(
            f"VOLT {float(vpp):.4f}"
        )

        return self.query_float(
            "VOLT?"
        )

    def set_offset(self, volts):
        self.write(
            f"VOLT:OFFS {float(volts):.4f}"
        )

        return self.query_float(
            "VOLT:OFFS?"
        )

    def set_width(self, seconds):
        self.write(
            f"FUNC:PULS:WIDT {float(seconds):.9f}"
        )

        return self.query_float(
            "FUNC:PULS:WIDT?"
        )

    def set_ncycles(self, cycles):
        self.write(
            f"BURS:NCYC {int(cycles)}"
        )

        return int(
            float(
                self.query(
                    "BURS:NCYC?"
                )
            )
        )

    def set_burst(self, on):
        if on:
            self.write(
                "FUNC PULS"
            )

            self.write(
                "BURS:MODE TRIG"
            )

            self.write(
                "TRIG:SOUR BUS"
            )

        self.write(
            f"BURS:STAT {'ON' if on else 'OFF'}"
        )

        return self.query_bool(
            "BURS:STAT?"
        )

    def set_output(self, on):
        self.write(
            f"OUTP {'ON' if on else 'OFF'}"
        )

        return self.query_bool(
            "OUTP?"
        )

    def fire(self):
        """Release one already-configured BUS-triggered burst."""

        self.write(
            "*TRG"
        )

        return True


Device.connection_class = DeviceConnection