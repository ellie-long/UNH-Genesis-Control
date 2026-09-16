"""jdmax/softioc_toolkit Device adapter for a Siglent SPD1305X."""

from __future__ import annotations

import threading

from softioc import alarm, builder

from devices.base_device import BaseDevice

from .controller import SPD1000XController, SocketSCPITransport


class Device(BaseDevice):
    """Expose the deliberately small SPD1305X operational PV set."""

    def _create_pvs(self):
        self._io_lock = threading.RLock()
        max_v, max_i = SPD1000XController.effective_limits(
            self.settings.get("max_voltage", 30.0), self.settings.get("max_current", 5.0))

        self.pvs["CH1_VC"] = builder.aOut(
            "CH1_VC", DRVL=0, DRVH=max_v, EGU="V", PREC=3,
            on_update_name=self.do_sets, **self.sevr)
        self.pvs["CH1_VI"] = builder.aIn(
            "CH1_VI", LOPR=0, HOPR=max_v, EGU="V", PREC=3, **self.sevr)
        self.pvs["CH1_CC"] = builder.aOut(
            "CH1_CC", DRVL=0, DRVH=max_i, EGU="A", PREC=3,
            on_update_name=self.do_sets, **self.sevr)
        self.pvs["CH1_CI"] = builder.aIn(
            "CH1_CI", LOPR=0, HOPR=max_i, EGU="A", PREC=3, **self.sevr)
        self.pvs["CH1_PI"] = builder.aIn(
            "CH1_PI", LOPR=0, HOPR=max_v * max_i, EGU="W", PREC=3, **self.sevr)
        self.pvs["CH1_Output"] = builder.boolOut(
            "CH1_Output", ZNAM="OFF", ONAM="ON", on_update_name=self.do_sets)
        self.pvs["CH1_Mode"] = builder.boolIn(
            "CH1_Mode", ZNAM="CV", ONAM="CC")
        self.pvs["CH1_OVP"] = builder.aOut(
            "CH1_OVP", DRVL=0, DRVH=SPD1000XController.DEFAULT_OVP, EGU="V", PREC=3,
            on_update_name=self.do_sets, **self.sevr)
        self.pvs["CH1_OCP"] = builder.aOut(
            "CH1_OCP", DRVL=0, DRVH=SPD1000XController.DEFAULT_OCP, EGU="A", PREC=3,
            on_update_name=self.do_sets, **self.sevr)
        self.pvs["Comm_Status"] = builder.boolIn(
            "Comm_Status", ZNAM="Disconnected", ONAM="Connected")
        self.pvs["Last_Error"] = builder.stringIn("Last_Error")

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
            )
            identity = controller.identity()
            if "SPD1305X" not in identity.upper():
                raise OSError(f"unexpected instrument identity: {identity}")
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
        self.pvs["Comm_Status"].set(connected)
        self.pvs["Last_Error"].set(message[:39])
        monitored = ("CH1_VI", "CH1_CI", "CH1_PI", "CH1_Mode")
        for name in monitored:
            if connected:
                self.pvs[name].set_alarm(severity=alarm.NO_ALARM, alarm=alarm.NO_ALARM)
            else:
                self.pvs[name].set_alarm(severity=alarm.MAJOR_ALARM, alarm=alarm.COMM_ALARM)

    def read_outs(self):
        try:
            self._apply_readings(self.t.read_all())
            self._set_comm(True)
        except Exception as exc:
            self._set_comm(False, str(exc))
            raise OSError(f"initial read: {exc}") from exc

    def _apply_readings(self, values):
        # Output-record .set() defaults to process=True in pysoftioc. Readbacks
        # must never dispatch do_sets: that echoes/clamps protection settings
        # and can queue stale output ON/OFF commands across a reconnect.
        self.pvs["CH1_VI"].set(values["voltage"])
        self.pvs["CH1_CI"].set(values["current"])
        self.pvs["CH1_PI"].set(values["power"])
        self.pvs["CH1_VC"].set(values["voltage_setpoint"], process=False)
        self.pvs["CH1_CC"].set(values["current_setpoint"], process=False)
        self.pvs["CH1_Output"].set(values["output"], process=False)
        self.pvs["CH1_Mode"].set(values["constant_current"])
        self.pvs["CH1_OVP"].set(values["ovp"], process=False)
        self.pvs["CH1_OCP"].set(values["ocp"], process=False)

    def do_sets(self, new_value, pv):
        with self._io_lock:
            if self.t is None:
                self._set_comm(False, "Disconnected; command not sent")
                return
            name = pv.removeprefix(self.device_name + ":")
            operations = {
                "CH1_VC": self.t.set_voltage,
                "CH1_CC": self.t.set_current,
                "CH1_Output": self.t.set_output,
                "CH1_OVP": self.t.set_ovp,
                "CH1_OCP": self.t.set_ocp,
            }
            try:
                self.t.write_and_settle(operations[name], new_value)
                self._apply_readings(self.t.read_all())
                self._set_comm(True)
            except ValueError as exc:
                # A rejected command is not a lost connection.
                self.pvs["Last_Error"].set(str(exc)[:39])
            except Exception as exc:
                self._disconnect()
                self._set_comm(False, str(exc))

    async def do_reads(self):
        with self._io_lock:
            if self.t is None:
                self.connect()
                return self.t is not None
            try:
                self._apply_readings(self.t.read_all())
                self._set_comm(True)
                return True
            except Exception as exc:
                self._disconnect()
                self._set_comm(False, str(exc))
                # The normal poll interval paces subsequent reconnect attempts.
                return False

    def reconnect(self):
        self.connect()
