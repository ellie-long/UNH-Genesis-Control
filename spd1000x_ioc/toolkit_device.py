"""Genesis Device adapter for a Siglent SPD1305X microwave power supply."""

from __future__ import annotations

import threading

from softioc import alarm, builder

from devices.base_device import BaseDevice

from .controller import SPD1000XController, SocketSCPITransport


class Device(BaseDevice):
    """Expose the SPD1305X through the Genesis EPICS PV contract."""

    def _create_pvs(self):
        self._io_lock = threading.RLock()

        max_v, max_i = SPD1000XController.effective_limits(
            self.settings.get("max_voltage", 30.0),
            self.settings.get("max_current", 5.0),
        )

        # Voltage
        self.pvs["VSET_CMD"] = builder.aOut(
            "VSET_CMD",
            DRVL=0,
            DRVH=max_v,
            EGU="V",
            PREC=3,
            on_update_name=self.do_sets,
            **self.sevr,
        )
        self.pvs["VSET_RBV"] = builder.aIn(
            "VSET_RBV",
            LOPR=0,
            HOPR=max_v,
            EGU="V",
            PREC=3,
            **self.sevr,
        )
        self.pvs["VMEAS"] = builder.aIn(
            "VMEAS",
            LOPR=0,
            HOPR=max_v,
            EGU="V",
            PREC=3,
            **self.sevr,
        )

        # Current
        self.pvs["ISET_CMD"] = builder.aOut(
            "ISET_CMD",
            DRVL=0,
            DRVH=max_i,
            EGU="A",
            PREC=3,
            on_update_name=self.do_sets,
            **self.sevr,
        )
        self.pvs["ISET_RBV"] = builder.aIn(
            "ISET_RBV",
            LOPR=0,
            HOPR=max_i,
            EGU="A",
            PREC=3,
            **self.sevr,
        )
        self.pvs["IMEAS"] = builder.aIn(
            "IMEAS",
            LOPR=0,
            HOPR=max_i,
            EGU="A",
            PREC=3,
            **self.sevr,
        )

        # Measured output power
        self.pvs["PMEAS"] = builder.aIn(
            "PMEAS",
            LOPR=0,
            HOPR=max_v * max_i,
            EGU="W",
            PREC=3,
            **self.sevr,
        )

        # Output control and actual output state
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

        # Constant-voltage / constant-current operating mode
        self.pvs["MODE_RBV"] = builder.boolIn(
            "MODE_RBV",
            ZNAM="CV",
            ONAM="CC",
        )

        # Protection settings
        self.pvs["OVP_CMD"] = builder.aOut(
            "OVP_CMD",
            DRVL=0,
            DRVH=SPD1000XController.DEFAULT_OVP,
            EGU="V",
            PREC=3,
            on_update_name=self.do_sets,
            **self.sevr,
        )
        self.pvs["OVP_RBV"] = builder.aIn(
            "OVP_RBV",
            LOPR=0,
            HOPR=SPD1000XController.DEFAULT_OVP,
            EGU="V",
            PREC=3,
            **self.sevr,
        )

        self.pvs["OCP_CMD"] = builder.aOut(
            "OCP_CMD",
            DRVL=0,
            DRVH=SPD1000XController.DEFAULT_OCP,
            EGU="A",
            PREC=3,
            on_update_name=self.do_sets,
            **self.sevr,
        )
        self.pvs["OCP_RBV"] = builder.aIn(
            "OCP_RBV",
            LOPR=0,
            HOPR=SPD1000XController.DEFAULT_OCP,
            EGU="A",
            PREC=3,
            **self.sevr,
        )

        # Standard Genesis communication health
        self.pvs["COMM"] = builder.boolIn(
            "COMM",
            ZNAM="Disconnected",
            ONAM="Connected",
        )
        self.pvs["ERROR"] = builder.stringIn("ERROR")

    def _create_connection(self):
        transport = SocketSCPITransport(
            self.settings["ip"],
            int(self.settings.get("port", 5025)),
            float(self.settings.get("timeout", 5.0)),
        )

        try:
            controller = SPD1000XController(
                transport,
                max_voltage=float(self.settings.get("max_voltage", 30.0)),
                max_current=float(self.settings.get("max_current", 5.0)),
                command_delay=float(self.settings.get("command_delay", 0.05)),
                write_enabled=bool(self.settings.get("write_enabled", False)),
            )

            identity = controller.identity()

            if "SPD1305X" not in identity.upper():
                raise OSError(
                    f"unexpected instrument identity: {identity}"
                )

            if (
                controller.write_enabled
                and bool(self.settings.get("initialize_on_connect", False))
            ):
                controller.initialize_safe()

            return controller

        except Exception:
            transport.close()
            raise

    def _disconnect(self):
        if self.t is not None:
            self.t.close()

        self.t = None

    def connect(self):
        with self._io_lock:
            self._disconnect()

            try:
                self.t = self._create_connection()
                self.read_outs()

            except Exception as exc:
                self._disconnect()
                self._set_comm(False, str(exc))

    def _set_comm(self, connected: bool, message: str = ""):
        self.pvs["COMM"].set(connected)
        self.pvs["ERROR"].set(message[:39])

        monitored = (
            "VSET_RBV",
            "VMEAS",
            "ISET_RBV",
            "IMEAS",
            "PMEAS",
            "OUTPUT_RBV",
            "MODE_RBV",
            "OVP_RBV",
            "OCP_RBV",
        )

        for name in monitored:
            if connected:
                self.pvs[name].set_alarm(
                    severity=alarm.NO_ALARM,
                    alarm=alarm.NO_ALARM,
                )
            else:
                self.pvs[name].set_alarm(
                    severity=alarm.MAJOR_ALARM,
                    alarm=alarm.COMM_ALARM,
                )

    def read_outs(self):
        try:
            self._apply_readings(self.t.read_all())
            self._set_comm(True)

        except Exception as exc:
            self._set_comm(False, str(exc))
            raise OSError(f"initial read: {exc}") from exc

    def _apply_readings(self, values):
        """Apply real hardware state to Genesis readback PVs."""

        # Actual measured output.
        self.pvs["VMEAS"].set(values["voltage"])
        self.pvs["IMEAS"].set(values["current"])
        self.pvs["PMEAS"].set(values["power"])

        # Hardware setpoint readbacks.
        self.pvs["VSET_RBV"].set(values["voltage_setpoint"])
        self.pvs["ISET_RBV"].set(values["current_setpoint"])

        # Keep command widgets synchronized with the actual hardware state.
        # process=False is critical: polling must never generate hardware writes.
        self.pvs["VSET_CMD"].set(
            values["voltage_setpoint"],
            process=False,
        )
        self.pvs["ISET_CMD"].set(
            values["current_setpoint"],
            process=False,
        )

        # Output and mode.
        self.pvs["OUTPUT_RBV"].set(values["output"])
        self.pvs["OUTPUT_CMD"].set(
            values["output"],
            process=False,
        )
        self.pvs["MODE_RBV"].set(
            values["constant_current"]
        )

        # Protection settings.
        self.pvs["OVP_RBV"].set(values["ovp"])
        self.pvs["OCP_RBV"].set(values["ocp"])

        self.pvs["OVP_CMD"].set(
            values["ovp"],
            process=False,
        )
        self.pvs["OCP_CMD"].set(
            values["ocp"],
            process=False,
        )

    def do_sets(self, new_value, pv):
        with self._io_lock:
            if not bool(self.settings.get("write_enabled", False)):
                self.pvs["ERROR"].set(
                    "Read-only mode; command not sent"
                )
                return

            if self.t is None:
                self._set_comm(
                    False,
                    "Disconnected; command not sent",
                )
                return

            name = pv.removeprefix(self.device_name + ":")

            operations = {
                "VSET_CMD": self.t.set_voltage,
                "ISET_CMD": self.t.set_current,
                "OUTPUT_CMD": self.t.set_output,
                "OVP_CMD": self.t.set_ovp,
                "OCP_CMD": self.t.set_ocp,
            }

            try:
                operation = operations.get(name)

                if operation is None:
                    raise ValueError(
                        f"unsupported command PV: {name}"
                    )

                self.t.write_and_settle(
                    operation,
                    new_value,
                )

                self._apply_readings(
                    self.t.read_all()
                )
                self._set_comm(True)

            except ValueError as exc:
                # A rejected command is not a lost connection.
                self.pvs["ERROR"].set(
                    str(exc)[:39]
                )

            except Exception as exc:
                self._disconnect()
                self._set_comm(
                    False,
                    str(exc),
                )

    async def do_reads(self):
        with self._io_lock:
            if self.t is None:
                self.connect()
                return self.t is not None

            try:
                self._apply_readings(
                    self.t.read_all()
                )
                self._set_comm(True)
                return True

            except Exception as exc:
                self._disconnect()
                self._set_comm(
                    False,
                    str(exc),
                )

                # Normal polling interval controls reconnect rate.
                return False

    def reconnect(self):
        self.connect()
