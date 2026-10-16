"""
devices/instruments/spd1000x.py
================================
Siglent SPD1000X-series (SPD1168X / SPD1305X) single-channel DC power supply
driver for the softioc_toolkit / epics-device-lib framework.

This is the EPICS port of ``siglent_coldboard/spd1000x_lan_gui.py``.

How the original script maps onto this file
-------------------------------------------
+---------------------------------+---------------------------------------------+
| Original (spd1000x_lan_gui.py)  | Here                                        |
+=================================+=============================================+
| class SocketSPD                 | class _SocketTransport                      |
| class VisaSPD                   | class _VisaTransport                        |
| class SPD (SCPI wrapper)        | class DeviceConnection                      |
| class Worker (thread + queues)  | Device.do_reads()  (periodic, run by IOC)   |
|                                 | Device.do_sets()   (called on caput)        |
| class App (Tk GUI)              | -- gone. EPICS PVs *are* the interface.     |
|                                 |    Any client (caget/caput, Phoebus, Python)|
|                                 |    can use them.                            |
| CSV logging in App              | -- gone. logic_devices/archiver.py does it. |
| ping()                          | -- gone. Alarms + reconnect() cover this.   |
| validate_2dp()                  | DRVH/DRVL/PREC record fields + _clamp()     |
| SAMPLE_INTERVAL = 1.0           | settings.yaml -> delay: 1                   |
| SOCKET_PORT = 5025              | settings.yaml -> port: 5025                 |
+---------------------------------+---------------------------------------------+

PV naming (one set per entry in ``channels:``, e.g. channel name "Coldboard"):

    <PREFIX>:Coldboard_VI    aIn      measured voltage      [V]
    <PREFIX>:Coldboard_CI    aIn      measured current      [A]
    <PREFIX>:Coldboard_PI    aIn      measured power        [W]
    <PREFIX>:Coldboard_VC    aOut     voltage setpoint      [V]   (caput to set)
    <PREFIX>:Coldboard_CC    aOut     current limit         [A]   (caput to set)
    <PREFIX>:Coldboard_Mode  boolOut  output OFF(0)/ON(1)         (caput to set)
    <PREFIX>:Coldboard_CVCC  boolIn   regulation mode CV(0)/CC(1)
    <PREFIX>:Coldboard_IDN   stringIn *IDN? response (set once at connect)

The suffix convention (_VI, _CI, _VC, _CC, _Mode) is the same one used by
dp832.py, cs4_magnet.py and cm4g_magnet.py so all lab supplies look alike.

settings.yaml entry
-------------------
    siglent_coldboard:
      module: 'target_ioc.instruments.spd1000x'
      autostart: True
      ip: '192.168.1.18'
      port: 5025            # raw SCPI socket port (used when transport: socket)
      transport: 'socket'   # 'socket' (std library only) or 'visa' (pyvisa, VXI-11)
      timeout: 5            # seconds
      settle: 0.1           # seconds to wait after a SET before reading back
      delay: 1              # seconds between do_reads() calls
      voltage_limit: 16.0   # software clamp on _VC (SPD1168X: 16 V, SPD1305X: 30 V)
      current_limit: 8.0    # software clamp on _CC (SPD1168X: 8 A,  SPD1305X: 5 A)
      channels:
        - Coldboard
      records: { ... }      # DESC/EGU/PREC/alarm limits per PV, see settings.yaml

Framework contract (see genesis_common/base_device.py)
-----------------------------------------------
BaseDevice.__init__ calls ``_create_pvs()`` immediately, master_ioc.py then
calls ``connect()`` (which calls ``_create_connection()`` and then
``read_outs()`` if it exists), and the IOC main loop awaits ``do_reads()``
every ``delay`` seconds.  Every method that talks to hardware must raise
``OSError`` on failure -- that is the signal BaseDevice uses to alarm and
reconnect.
"""

import logging
import socket
import time

from softioc import builder

from genesis_common.base_device import GenesisBaseDevice

log = logging.getLogger(__name__)

# Suffixes of the PVs created per channel. Used for alarm handling and
# for routing caputs in do_sets().
_INPUT_SUFFIXES = ("_VI", "_CI", "_PI", "_CVCC")
_OUTPUT_SUFFIXES = ("_VC", "_CC", "_Mode")


class Device(GenesisBaseDevice):
    """EPICS-facing side of the driver: owns the PVs, decides *when* to talk
    to the supply. All SCPI details live in DeviceConnection."""

    # ------------------------------------------------------------------ PVs
    def _create_pvs(self):
        """Called by BaseDevice.__init__. Build one PV set per channel.

        ``builder`` is pythonSoftIOC's record factory.  ``builder.aIn`` makes
        an EPICS *ai* record, ``aOut`` an *ao*, ``boolIn/boolOut`` a *bi/bo*,
        ``stringIn`` a *stringin*.  ``self.sevr`` (from BaseDevice) turns on
        MINOR/MAJOR alarm severities so HIHI/HIGH/LOW/LOLO limits from
        settings.yaml actually alarm.  ``on_update_name=self.do_sets`` makes
        the IOC call do_sets(value, full_pv_name) whenever a client writes to
        that PV -- this replaces the Worker thread's cmd_q in the old script.
        """
        for ch in self._skip_none_channels():
            # --- readbacks (was: Worker samples every SAMPLE_INTERVAL) ---
            self.pvs[ch + "_VI"] = builder.aIn(ch + "_VI", **self.sevr)
            self.pvs[ch + "_CI"] = builder.aIn(ch + "_CI", **self.sevr)
            self.pvs[ch + "_PI"] = builder.aIn(ch + "_PI", **self.sevr)
            self.pvs[ch + "_CVCC"] = builder.boolIn(ch + "_CVCC", ZNAM="CV", ONAM="CC")
            self.pvs[ch + "_IDN"] = builder.stringIn(ch + "_IDN")

            # --- controls (was: on_set_voltage / on_set_current / on_output) ---
            self.pvs[ch + "_VC"] = builder.aOut(
                ch + "_VC", on_update_name=self.do_sets, **self.sevr)
            self.pvs[ch + "_CC"] = builder.aOut(
                ch + "_CC", on_update_name=self.do_sets, **self.sevr)
            self.pvs[ch + "_Mode"] = builder.boolOut(
                ch + "_Mode", ZNAM="OFF", ONAM="ON", on_update_name=self.do_sets)

            # Is this IOC actually talking to the supply?  Every other reading
            # looks the same whether it is or not: an unreachable supply serves
            # 0.000 V, 0.000 A and accepts every write without complaint.
            #
            # The ScpiDevice drivers get this PV from their base class; this
            # one extends BaseDevice directly, so it has to build its own -
            # and until it did, tools/pv_map.py promised UNH:Coldboard_Conn to
            # the archiver, the screens and the alarm scan while nobody served
            # it.  That is what put a permanent INVALID on the alarm summary,
            # latched to a PV that had never existed.
            self.pvs[ch + "_Conn"] = builder.boolIn(
                ch + "_Conn", ZNAM="No", ONAM="Yes",
                **{"ZSV": "MAJOR", "OSV": "NO_ALARM"})

    # ------------------------------------------------------------ connection
    def _set_conn(self, connected):
        for ch in self._skip_none_channels():
            pv = self.pvs.get(ch + "_Conn")
            if pv is not None:
                pv.set(1 if connected else 0)

    def connect(self):
        super().connect()
        self._set_conn(self.t is not None)

    def _create_connection(self):
        """Called by BaseDevice.connect(). Everything comes from settings.yaml,
        with defaults so a minimal entry (ip + channels) still works."""
        s = self.settings
        return DeviceConnection(
            host=s["ip"],
            port=int(s.get("port", 5025)),
            timeout=float(s.get("timeout", 5.0)),
            transport=str(s.get("transport", "socket")).lower(),
            settle=float(s.get("settle", 0.1)),
        )

    def reconnect(self):
        """Close the old socket before BaseDevice re-opens a new one.
        (BaseDevice.reconnect only ``del``s the object; explicit close() is
        cleaner and avoids leaking file descriptors on repeated failures.)"""
        if self.t is not None:
            try:
                self.t.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
        super().reconnect()

    def read_outs(self):
        """Called once by BaseDevice._post_connect() right after connecting.

        Initialise the *output* PVs from the supply's actual state so the
        setpoint PVs are not left at 0 while the supply is really at 5 V.
        (The old GUI simply showed "0.00" in the entry boxes.)
        """
        if self.t is None:
            return
        for i, ch in enumerate(self.channels):
            if "None" in ch:
                continue
            n = i + 1
            try:
                self.pvs[ch + "_IDN"].set(self.t.idn()[:39])  # stringin max 40 chars
                v_sp, i_sp = self.t.read_sp(n)
                # process=False: these are OUT records whose on_update is
                # do_sets, so a plain .set() here would push the value we just
                # READ straight back to the instrument on every reconnect.
                self.store(ch + "_VC", v_sp)
                self.store(ch + "_CC", i_sp)
                self.store(ch + "_Mode", int(self.t.read_state(n)))
                log.info("%s: initial setpoints V=%.3f I=%.3f output=%s",
                         ch, v_sp, i_sp, "ON" if self.pvs[ch + "_Mode"].get() else "OFF")
            except OSError as e:
                log.error("%s: read_outs failed: %s", ch, e)
                self.reconnect()
                return

    # --------------------------------------------------------------- writes
    def do_sets(self, new_value, pv):
        """Called by the IOC when a client writes to an OUT PV.

        Replaces Worker._handle(): instead of pulling ("set_voltage", 5.0)
        off a queue we receive (5.0, "PREFIX:Coldboard_VC") directly.
        """
        gate_pv = pv.replace(self.device_name + ':', '')
        if not self.can_write(gate_pv):
            return

        pv_name = pv.replace(self.device_name + ":", "")   # strip "PREFIX:"
        ch, kind = pv_name.rsplit("_", 1)                 # "Coldboard", "VC"
        n = self.channels.index(ch) + 1                   # SCPI channel number
        if self.t is None:
            self._set_ignored(pv_name)
            return
        try:
            if kind == "VC":
                v = self._clamp(new_value, self.settings.get("voltage_limit"), pv_name)
                self.store(pv_name, self.t.set_voltage(n, v))       # read-back, no callback
            elif kind == "CC":
                a = self._clamp(new_value, self.settings.get("current_limit"), pv_name)
                self.store(pv_name, self.t.set_current(n, a))
            elif kind == "Mode":
                self.store(pv_name, int(self.t.set_state(n, bool(new_value))))
            else:
                log.error("do_sets: unknown control PV %s", pv_name)
                return
            log.info("%s <- %s", pv_name, new_value)
        except OSError as e:
            log.error("%s: set failed: %s", pv_name, e)
            self.reconnect()

    @staticmethod
    def _clamp(value, limit, pv_name):
        """Software interlock. The record's DRVH/DRVL (settings.yaml) already
        stop caputs above the limit; this is a second line of defence for
        writes that bypass the record (e.g. from Python inside the IOC)."""
        value = float(value)
        if value < 0:
            log.warning("%s: %.3f below 0, clamped", pv_name, value)
            return 0.0
        if limit is not None and value > float(limit):
            log.warning("%s: %.3f above limit %.3f, clamped", pv_name, value, float(limit))
            return float(limit)
        return value

    # ---------------------------------------------------------------- reads
    async def do_reads(self):
        """Called by master_ioc.py every ``delay`` seconds (Worker.run loop).

        Return True on success so master_ioc updates the MAN:<ioc>_time
        heartbeat PV; return False on failure after alarming + reconnecting.
        """
        if self.t is None:                 # connect() failed at startup
            self._set_conn(False)
            self.reconnect()
            return False
        try:
            for i, ch in enumerate(self.channels):
                if "None" in ch:
                    continue
                n = i + 1
                v, a, p = self.t.read(n)
                self.pvs[ch + "_VI"].set(v)
                self.pvs[ch + "_CI"].set(a)
                self.pvs[ch + "_PI"].set(p)
                on, cc = self.t.read_status(n)
                self.pvs[ch + "_CVCC"].set(int(cc))
                # Keep control PVs in sync if someone changes the front panel.
                #
                # This must NOT process the record.  These are OUT PVs whose
                # on_update is do_sets, so a plain .set() here turns a read
                # into a write: every poll takes the setpoint off the supply
                # and commands it straight back.  That is one SCPI write per
                # channel per second in the quiet case, and in the noisy case -
                # two values in flight - a loop that never settles, which is
                # what made an operator's 1 V and 2 V flip against each other
                # on 2026-09-15.  See BaseDevice.store().
                v_sp, i_sp = self.t.read_sp(n)
                self.store(ch + "_VC", v_sp)
                self.store(ch + "_CC", i_sp)
                self.store(ch + "_Mode", int(on))
            self._handle_read_success()
            self._set_conn(True)
            return True
        except OSError as e:
            log.warning("read failed: %s", e)
            self._set_conn(False)
            self._handle_read_error()
            return False

    # --------------------------------------------------------------- alarms
    # BaseDevice's versions look for a PV literally named after the channel
    # ("Coldboard"), but our PVs are "Coldboard_VI" etc., so override them to
    # alarm every PV belonging to the channel.
    def _handle_read_error(self, channels=None):
        for ch in self._skip_none_channels(channels):
            for suf in _INPUT_SUFFIXES + _OUTPUT_SUFFIXES:
                self.set_alarm(ch + suf)
        self.reconnect()

    def _handle_read_success(self, channels=None):
        for ch in self._skip_none_channels(channels):
            for suf in _INPUT_SUFFIXES + _OUTPUT_SUFFIXES:
                self.remove_alarm(ch + suf)


# =============================================================== transports
class _SocketTransport:
    """Raw TCP SCPI socket, port 5025.  Was ``SocketSPD`` in the GUI script.
    Only change: connection errors propagate (BaseDevice.connect handles them)
    and failures raise OSError."""

    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)

    def write(self, cmd):
        self.sock.sendall((cmd + "\n").encode("ascii"))

    def query(self, cmd):
        self.write(cmd)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("connection closed by supply")
            buf += chunk
        return buf.decode("ascii", errors="replace").strip()

    def close(self):
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class _VisaTransport:
    """VXI-11 via PyVISA ('TCPIP0::<ip>::INSTR').  Was ``VisaSPD``.
    Needs ``pip install pyvisa pyvisa-py``; the import is deferred so the
    socket transport works without it."""

    def __init__(self, host, timeout):
        import pyvisa  # noqa: WPS433 - optional dependency
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(f"TCPIP0::{host}::INSTR")
        self.inst.write_termination = "\n"
        self.inst.read_termination = "\n"
        self.inst.timeout = int(timeout * 1000)

    def write(self, cmd):
        self.inst.write(cmd)

    def query(self, cmd):
        return self.inst.query(cmd).strip()

    def close(self):
        try:
            self.inst.close()
        except Exception:  # noqa: BLE001
            pass


# ============================================================ SCPI wrapper
class DeviceConnection:
    """SCPI command layer.  Was ``class SPD`` in the GUI script, extended with
    the query commands the IOC needs (setpoint and status read-back).

    Rules followed by every public method:
      * takes the 1-based channel number (SPD1000X has only CH1, but keeping
        the argument makes the driver reusable for the 2/3-channel SPD3303X);
      * returns Python floats/bools, never raw strings;
      * raises OSError on any communication or parse problem, because that is
        what BaseDevice/do_reads catch.
    """

    def __init__(self, host, port, timeout, transport="socket", settle=0.1):
        self.host, self.port, self.timeout, self.settle = host, port, timeout, settle
        log.info("Connecting to SPD1000X at %s (%s)", host, transport)
        try:
            if transport == "visa":
                self.io = _VisaTransport(host, timeout)
            else:
                self.io = _SocketTransport(host, port, timeout)
        except Exception as e:
            raise OSError(f"SPD1000X connect to {host}: {e}") from e
        # Same as the original: ask for remote mode, ignore if unsupported.
        try:
            self.io.write("SYSTem:REMote")
            time.sleep(self.settle)
        except Exception:  # noqa: BLE001
            pass

    # ---- low level helpers -------------------------------------------------
    def _query(self, cmd):
        try:
            return self.io.query(cmd)
        except Exception as e:
            raise OSError(f"SPD1000X query '{cmd}' on {self.host}: {e}") from e

    def _write(self, cmd):
        try:
            self.io.write(cmd)
            time.sleep(self.settle)   # Siglent needs a moment before the next cmd
        except Exception as e:
            raise OSError(f"SPD1000X write '{cmd}' on {self.host}: {e}") from e

    def _query_float(self, cmd):
        data = self._query(cmd)
        try:
            return float(data)
        except ValueError as e:
            raise OSError(f"SPD1000X bad reply to '{cmd}': {data!r}") from e

    # ---- queries -----------------------------------------------------------
    def idn(self):
        return self._query("*IDN?")

    def read(self, ch):
        """Measured (voltage, current, power) for channel ``ch``."""
        v = self._query_float(f"MEASure:VOLTage? CH{ch}")
        i = self._query_float(f"MEASure:CURRent? CH{ch}")
        try:
            p = self._query_float(f"MEASure:POWEr? CH{ch}")
        except OSError:
            p = v * i          # older firmware lacks MEAS:POWE?; compute it
        return v, i, p

    def read_sp(self, ch):
        """Programmed (voltage, current) setpoints."""
        return (self._query_float(f"CH{ch}:VOLTage?"),
                self._query_float(f"CH{ch}:CURRent?"))

    def read_status(self, ch):
        """(output_on, in_cc_mode) decoded from SYSTem:STATus? (hex bitfield).
        SPD1000X: bit0 = CH1 CC(1)/CV(0), bit4 = CH1 output ON.
        SPD3303X uses the same two bits for CH1; verify against your manual
        if you adapt this for another model."""
        data = self._query("SYSTem:STATus?")
        try:
            bits = int(data.strip(), 16)
        except ValueError as e:
            raise OSError(f"SPD1000X bad status reply: {data!r}") from e
        return bool(bits & 0x10), bool(bits & 0x01)

    def read_state(self, ch):
        return self.read_status(ch)[0]

    # ---- commands (each returns the read-back so the PV shows the truth) ---
    def set_voltage(self, ch, volts):
        self._write(f"CH{ch}:VOLTage {float(volts):.3f}")
        return self.read_sp(ch)[0]

    def set_current(self, ch, amps):
        self._write(f"CH{ch}:CURRent {float(amps):.3f}")
        return self.read_sp(ch)[1]

    def set_state(self, ch, on):
        self._write(f"OUTPut CH{ch},{'ON' if on else 'OFF'}")
        return self.read_state(ch)

    def close(self):
        self.io.close()
