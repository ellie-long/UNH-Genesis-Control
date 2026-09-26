"""Laptop-side EPICS driver for the Vaunix TCP shim."""
import logging
import socket
import subprocess
import threading
import time

from softioc import builder
from devices.base_device import BaseDevice

LOG = logging.getLogger(__name__)


class ShimRejected(RuntimeError):
    """The TCP connection works, but the shim rejected the operation."""


class ShimConnection:
    """One persistent, synchronized request/reply connection to the Pi."""

    def __init__(self, host, port, timeout):
        self.host, self.port = host, int(port)
        self.timeout = float(timeout)
        self.socket = self.reader = None
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        self.close()
        self.socket = socket.create_connection((self.host, self.port), self.timeout)
        self.socket.settimeout(self.timeout)
        self.reader = self.socket.makefile("rb")

    def close(self):
        if self.reader is not None:
            try: self.reader.close()
            except OSError: pass
        if self.socket is not None:
            try: self.socket.close()
            except OSError: pass
        self.reader = self.socket = None

    def command(self, request):
        with self.lock:
            if self.socket is None: raise OSError("shim connection is closed")
            try:
                self.socket.sendall((request + "\n").encode("ascii"))
                raw = self.reader.readline(4097)
            except (OSError, ValueError) as exc:
                self.close()
                raise OSError(f"shim communication failed: {exc}") from exc
            if not raw or len(raw) > 4096 or not raw.endswith(b"\n"):
                self.close()
                raise OSError("shim returned an incomplete or oversized response")
            reply = raw.decode("ascii", "replace").strip()
            if reply.startswith("ERR "): raise ShimRejected(reply[4:])
            return reply

    def identity(self):
        reply = self.command("*IDN?")
        fields = reply.split(",")
        if len(fields) != 5 or fields[0] != "VAUNIX" or fields[4] not in ("TEST", "HARDWARE"):
            raise OSError("invalid *IDN? response")
        return reply, int(fields[4] == "TEST")

    def limits(self):
        values = self.command("LIMITS?").split()
        if len(values) != 4: raise OSError("invalid LIMITS? response")
        fmin, fmax, pmin, pmax = map(float, values)
        return fmin / 1e9, fmax / 1e9, pmin, pmax

    def read(self):
        values = self.command("READ?").split()
        if len(values) != 5: raise OSError("invalid READ? response")
        frequency, power = map(float, values[:2])
        try: flags = tuple(int(value) for value in values[2:])
        except ValueError: raise OSError("invalid flags in READ? response") from None
        if any(value not in (0, 1) for value in flags):
            raise OSError("invalid flags in READ? response")
        return frequency / 1e9, power, *flags

    def _set(self, name, value):
        reply = self.command(f"{name} {value}").split()
        if len(reply) != 2 or reply[0] != "OK":
            raise OSError(f"invalid {name} response")
        try: return float(reply[1])
        except ValueError: raise OSError(f"invalid {name} value") from None

    def set_frequency(self, ghz):
        return self._set("FREQ", f"{float(ghz) * 1e9:.0f}") / 1e9

    def set_power(self, dbm): return self._set("POW", f"{float(dbm):.2f}")
    def set_rf(self, enabled): return int(self._set("RF", 1 if enabled else 0))
    def set_reference(self, internal):
        return int(self._set("REF", 1 if internal else 0))

    def sweep(self):
        values = self.command("SWEEP?").split()
        if len(values) != 9: raise OSError("invalid SWEEP? response")
        start, end, step = map(float, values[:3])
        duration = int(values[3])
        flags = tuple(int(value) for value in values[4:])
        if any(value not in (0, 1) for value in flags):
            raise OSError("invalid flags in SWEEP? response")
        return start / 1e9, end / 1e9, step / 1e9, duration, *flags

    def set_test_mode(self, enabled): return int(self._set("TEST_MODE", 1 if enabled else 0))
    def set_sweep_start(self, ghz): return self._set("SWEEP_START", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_end(self, ghz): return self._set("SWEEP_END", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_step(self, ghz): return self._set("SWEEP_STEP", f"{float(ghz) * 1e9:.0f}") / 1e9
    def set_sweep_time(self, milliseconds): return int(self._set("SWEEP_TIME", int(round(milliseconds))))
    def set_sweep_flag(self, name, enabled):
        return int(self._set(f"SWEEP_{name}", 1 if enabled else 0))


class Device(BaseDevice):
    """One Vaunix channel with command and readback EPICS records."""

    def _create_pvs(self):
        channels = self._skip_none_channels()
        if len(channels) != 1:
            raise ValueError("Vaunix LMS requires exactly one channel name")
        self.channel = channels[0]
        self.multiplier = float(self.settings.get("multiplier", 1))
        channel = self.channel
        self.pvs[channel + "_FI"] = builder.aIn(channel + "_FI", **self.sevr)
        self.pvs[channel + "_FC"] = builder.aOut(
            channel + "_FC", on_update_name=self.do_sets, **self.sevr)
        self.pvs[channel + "_OutFI"] = builder.aIn(channel + "_OutFI", **self.sevr)
        self.pvs[channel + "_PWRI"] = builder.aIn(channel + "_PWRI", **self.sevr)
        self.pvs[channel + "_PWRC"] = builder.aOut(
            channel + "_PWRC", on_update_name=self.do_sets, **self.sevr)
        self.pvs[channel + "_RF"] = builder.boolOut(
            channel + "_RF", on_update_name=self.do_sets)
        self.pvs[channel + "_Ref"] = builder.boolOut(
            channel + "_Ref", on_update_name=self.do_sets)
        self.pvs[channel + "_Lock"] = builder.boolIn(channel + "_Lock")
        self.pvs[channel + "_Connected"] = builder.boolIn(
            channel + "_Connected", ZNAM="Disconnected", ONAM="Connected")
        self.pvs[channel + "_TestMode"] = builder.boolOut(
            channel + "_TestMode", ZNAM="Hardware", ONAM="Test",
            on_update_name=self.do_sets)
        for suffix in ("SweepStart", "SweepEnd", "SweepStep"):
            self.pvs[channel + f"_{suffix}I"] = builder.aIn(
                channel + f"_{suffix}I", EGU="GHz", PREC=6, **self.sevr)
            self.pvs[channel + f"_{suffix}C"] = builder.aOut(
                channel + f"_{suffix}C", EGU="GHz", PREC=6,
                on_update_name=self.do_sets, **self.sevr)
        self.pvs[channel + "_SweepTimeI"] = builder.longIn(
            channel + "_SweepTimeI", EGU="ms")
        self.pvs[channel + "_SweepTimeC"] = builder.longOut(
            channel + "_SweepTimeC", EGU="ms", DRVL=1,
            on_update_name=self.do_sets)
        sweep_flags = {
            "SweepDirection": ("Down", "Up"),
            "SweepRepeat": ("One Shot", "Repeat"),
            "SweepBidirectional": ("Sawtooth", "Triangle"),
            "SweepExternalTrigger": ("Internal", "External"),
            "SweepRun": ("Stopped", "Running"),
        }
        for suffix, (zero, one) in sweep_flags.items():
            self.pvs[channel + "_" + suffix] = builder.boolOut(
                channel + "_" + suffix, ZNAM=zero, ONAM=one,
                on_update_name=self.do_sets)
        self.pvs[channel + "_SweepActive"] = builder.boolIn(
            channel + "_SweepActive", ZNAM="Idle", ONAM="Sweeping")

    def _create_connection(self):
        return ShimConnection(self.settings["ip"], self.settings["port"],
                              self.settings["timeout"])

    def _start_remote_service(self):
        """Idempotently start the Pi service before opening its TCP socket."""
        shim = self.settings.get("shim", {})
        if not shim.get("start_with_ioc", True):
            return
        user, host = shim.get("ssh_user"), shim.get("ssh_host")
        if not user or not host:
            raise OSError("shim.ssh_user and shim.ssh_host are required")
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                   f"{user}@{host}",
                   "systemctl --user start vaunix-shim.service"]
        try:
            subprocess.run(command, check=True, timeout=12,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            raise OSError(f"could not start Pi shim service: {detail.strip()}") from exc
        deadline = time.monotonic() + float(shim.get("startup_timeout", 15))
        last_error = None
        while time.monotonic() < deadline:
            try:
                probe = socket.create_connection(
                    (self.settings["ip"], int(self.settings["port"])), timeout=1)
                probe.close()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        raise OSError(f"Pi shim service did not open its TCP port: {last_error}")

    def connect(self):
        try:
            self._start_remote_service()
            self.t = self._create_connection()
            identity, test_mode = self.t.identity()
            self.pvs[self.channel + "_Connected"].set(True)
            self.pvs[self.channel + "_TestMode"].set(test_mode, process=False)
            LOG.info("connected to %s; limits GHz/dBm: %s", identity, self.t.limits())
            self.read_outs()
        except (OSError, ShimRejected) as exc:
            LOG.error("Vaunix connection failed: %s", exc)
            self.t = None
            self.pvs[self.channel + "_Connected"].set(False)

    def reconnect(self):
        if self.t is not None: self.t.close()
        self.t = None
        self.connect()

    def _apply(self, values, include_setpoints=False):
        frequency, power, rf, reference, lock = values
        channel = self.channel
        self.pvs[channel + "_FI"].set(frequency)
        self.pvs[channel + "_OutFI"].set(frequency * self.multiplier)
        self.pvs[channel + "_PWRI"].set(power)
        self.pvs[channel + "_RF"].set(rf)
        self.pvs[channel + "_Ref"].set(reference)
        self.pvs[channel + "_Lock"].set(lock)
        if include_setpoints:
            self.pvs[channel + "_FC"].set(frequency)
            self.pvs[channel + "_PWRC"].set(power)

    def _apply_sweep(self, values, include_setpoints=False):
        start, end, step, duration, direction, repeat, bidirectional, trigger, active = values
        channel = self.channel
        for suffix, value in (("SweepStartI", start), ("SweepEndI", end),
                              ("SweepStepI", step), ("SweepTimeI", duration),
                              ("SweepActive", active)):
            self.pvs[channel + "_" + suffix].set(value)
        for suffix, value in (("SweepDirection", direction), ("SweepRepeat", repeat),
                              ("SweepBidirectional", bidirectional),
                              ("SweepExternalTrigger", trigger), ("SweepRun", active)):
            self.pvs[channel + "_" + suffix].set(value, process=False)
        if include_setpoints:
            for suffix, value in (("SweepStartC", start), ("SweepEndC", end),
                                  ("SweepStepC", step), ("SweepTimeC", duration)):
                self.pvs[channel + "_" + suffix].set(value, process=False)

    def read_outs(self):
        if self.t is not None:
            try:
                self._apply(self.t.read(), include_setpoints=True)
                self._apply_sweep(self.t.sweep(), include_setpoints=True)
            except (OSError, ShimRejected) as exc:
                LOG.error("Vaunix startup read failed: %s", exc)

    def do_sets(self, new_value, pv):
        if self.t is None:
            LOG.warning("ignoring %s while the Vaunix shim is disconnected", pv)
            return
        name = pv.rsplit(":", 1)[-1]
        try:
            if name.endswith("_FC"):
                achieved = self.t.set_frequency(new_value)
                self.pvs[self.channel + "_FI"].set(achieved)
                self.pvs[self.channel + "_OutFI"].set(achieved * self.multiplier)
            elif name.endswith("_PWRC"):
                self.pvs[self.channel + "_PWRI"].set(self.t.set_power(new_value))
            elif name.endswith("_RF"):
                self.pvs[self.channel + "_RF"].set(self.t.set_rf(new_value))
            elif name.endswith("_Ref"):
                self.pvs[self.channel + "_Ref"].set(self.t.set_reference(new_value))
            elif name.endswith("_TestMode"):
                mode = self.t.set_test_mode(new_value)
                self.pvs[self.channel + "_TestMode"].set(mode, process=False)
                self.read_outs()
            elif name.endswith("_SweepStartC"):
                self.pvs[self.channel + "_SweepStartI"].set(self.t.set_sweep_start(new_value))
            elif name.endswith("_SweepEndC"):
                self.pvs[self.channel + "_SweepEndI"].set(self.t.set_sweep_end(new_value))
            elif name.endswith("_SweepStepC"):
                self.pvs[self.channel + "_SweepStepI"].set(self.t.set_sweep_step(new_value))
            elif name.endswith("_SweepTimeC"):
                self.pvs[self.channel + "_SweepTimeI"].set(self.t.set_sweep_time(new_value))
            elif "_Sweep" in name:
                suffix = name.split("_Sweep", 1)[1]
                mapping = {"Direction": "DIRECTION", "Repeat": "REPEAT",
                           "Bidirectional": "BIDIRECTIONAL",
                           "ExternalTrigger": "EXTERNAL_TRIGGER", "Run": "RUN"}
                achieved = self.t.set_sweep_flag(mapping[suffix], new_value)
                self.pvs[self.channel + "_Sweep" + suffix].set(achieved, process=False)
        except ShimRejected as exc:
            LOG.warning("Vaunix rejected %s=%s: %s", name, new_value, exc)
            self.read_outs()
        except OSError as exc:
            LOG.error("Vaunix write failed: %s", exc)
            self.reconnect()

    async def do_reads(self):
        if self.t is None:
            self.connect()
            if self.t is None:
                self.set_alarm(self.channel)
                return False
        try:
            self._apply(self.t.read())
            self._apply_sweep(self.t.sweep())
            self.pvs[self.channel + "_Connected"].set(True)
            self.remove_alarm(self.channel)
            return True
        except ShimRejected as exc:
            LOG.warning("Vaunix read rejected: %s", exc)
            self.set_alarm(self.channel)
            self.pvs[self.channel + "_Connected"].set(False)
            return False
        except OSError as exc:
            LOG.error("Vaunix read failed: %s", exc)
            self.set_alarm(self.channel)
            self.pvs[self.channel + "_Connected"].set(False)
            self.reconnect()
            return False

    def set_alarm(self, _channel):
        for suffix in ("_FI", "_OutFI", "_PWRI", "_Lock", "_SweepStartI",
                       "_SweepEndI", "_SweepStepI", "_SweepTimeI", "_SweepActive"):
            super().set_alarm(self.channel + suffix)

    def remove_alarm(self, _channel):
        for suffix in ("_FI", "_OutFI", "_PWRI", "_Lock", "_SweepStartI",
                       "_SweepEndI", "_SweepStepI", "_SweepTimeI", "_SweepActive"):
            super().remove_alarm(self.channel + suffix)
