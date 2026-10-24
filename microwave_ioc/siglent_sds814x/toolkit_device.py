"""Read-only scalar CH1 measurement for SIGLENT SDS814X HD.

No waveform transfer, acquisition setup, trigger control, RF, or robot writes.
SIG_VI reports the oscilloscope's CH1 *mean voltage* measurement; this is
not the previous implementation's last waveform sample.

The scope must already have Simple measurement source C1 and item MEAN enabled
in its saved setup. This IOC never changes those user measurement settings.
"""
from __future__ import annotations

import asyncio
import logging
import math
import socket
import threading
import time

from softioc import alarm, builder
from genesis_common.upstream_base_device import BaseDevice

LOG = logging.getLogger(__name__)


class MeasurementUnavailable(ValueError):
    """SCPI is connected, but the selected measurement is not available."""


class SocketSCPI:
    """Bounded, single-owner text SCPI connection; no binary data support."""

    def __init__(self, host, port=5025, timeout=2.0):
        self.timeout = float(timeout)
        self.sock = socket.create_connection((host, int(port)), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buffer = bytearray()

    def close(self):
        self.sock.close()

    def write(self, command):
        if not command or '\n' in command or '\r' in command:
            raise ValueError('Invalid SCPI command')
        self.sock.settimeout(self.timeout)
        self.sock.sendall((command + '\n').encode('ascii'))

    def query(self, command):
        try:
            self.write(command)
            deadline = time.monotonic() + self.timeout
            while True:
                idx = self.buffer.find(b'\n')
                if idx >= 0:
                    reply = bytes(self.buffer[:idx]).decode('ascii').strip()
                    del self.buffer[:idx + 1]
                    if reply:
                        return reply
                    continue
                if len(self.buffer) > 4096:
                    raise ValueError('SCPI response too long')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('SCPI response deadline')
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError('Scope closed connection')
                self.buffer.extend(chunk)
        except Exception:
            self.close()  # An incomplete text response cannot be resynchronized.
            raise


class Controller:
    def __init__(self, transport):
        self.io = transport

    def initialize(self):
        # Header formatting only. Does not change acquisition/measurement setup.
        self.io.write('CHDR OFF')
        identity = self.io.query('*IDN?')
        parts = identity.upper().split(',')
        if (len(parts) < 2 or 'SIGLENT' not in parts[0] or
                parts[1].strip().replace(' ', '') not in ('SDS814XHD', 'SDS814X')):
            raise ValueError('Expected SIGLENT SDS814X HD: ' + identity)
        return identity

    def read_ch1_voltage(self):
        # Do not silently return another channel if the saved setup was changed.
        source = self.io.query(':MEAS:SIMP:SOUR?').upper()
        if source != 'C1':
            raise MeasurementUnavailable('Set Simple measurement source to C1')
        raw = self.io.query(':MEAS:SIMP:VAL? MEAN')
        try:
            value = float(raw)
        except ValueError as exc:
            raise MeasurementUnavailable('Enable CH1 Simple MEAN measurement') from exc
        if not math.isfinite(value) or abs(value) > 1e15:
            raise MeasurementUnavailable('CH1 mean measurement unavailable')
        return value

    def close(self):
        self.io.close()


class DeviceConnection(Controller):
    def __init__(self, host, port=5025, timeout=2.0):
        super().__init__(SocketSCPI(host, port, timeout))


class Device(BaseDevice):
    """Publish scalar CH1 mean voltage and connection/validity diagnostics."""

    def _pv(self, name):
        return self.pvs[f'{self.root}_{name}']

    def _add(self, name, factory, **fields):
        key = f'{self.root}_{name}'
        self.pvs[key] = factory(key, **fields)

    def _create_pvs(self):
        channels = self._skip_none_channels()
        if len(channels) != 1 or not channels[0] or any(c.isspace() for c in channels[0]):
            raise ValueError('SIGLENT requires one logical channel, e.g. [SIG]')
        self.root = channels[0]
        self.lock = threading.RLock()
        self.rate = float(self.settings.get('update_hz', 2.0))
        self.timeout = float(self.settings.get('timeout', 2.0))
        self.reconnect_delay = float(self.settings.get('reconnect_delay', 3.0))
        if not 0.2 <= self.rate <= 20.0:
            raise ValueError('update_hz must be 0.2..20')
        if not all(math.isfinite(v) and v > 0 for v in
                   (self.timeout, self.reconnect_delay)):
            raise ValueError('Invalid timing configuration')
        self.next_poll = self.retry_at = 0.0
        self.last_error = ''
        self._add('VI', builder.aIn, EGU='V', PREC=6)
        for name in ('Comm_Status', 'Data_Valid'):
            self._add(name, builder.boolIn, ZNAM='No', ONAM='Yes')
        self._add('Last_Error', builder.stringIn)
        self._add('Identity', builder.stringIn)
        self._pv('Comm_Status').set(False)
        self._invalidate()
        self._pv('Last_Error').set('')

    def _invalidate(self):
        self._pv('Data_Valid').set(False)
        self._pv('VI').set(float('nan'))
        self._pv('VI').set_alarm(severity=alarm.INVALID_ALARM,
                                 alarm=alarm.READ_ALARM)

    def _create_connection(self):
        return DeviceConnection(self.settings['ip'],
                                self.settings.get('port', 5025), self.timeout)

    def connect(self):
        # master_ioc calls this before iocInit: defer all network I/O to poll.
        self.retry_at = 0.0

    def _disconnect(self):
        if self.t is not None:
            try:
                self.t.close()
            except OSError:
                pass
        self.t = None
        self._pv('Comm_Status').set(False)

    def reconnect(self):
        with self.lock:
            self._disconnect()
            self.retry_at = 0.0

    def _error(self, exc, disconnect):
        message = str(exc)[:39]
        if message != self.last_error:
            LOG.warning('SDS814X HD: %s', exc)
            self.last_error = message
        self._pv('Last_Error').set(message)
        self._invalidate()
        if disconnect:
            self._disconnect()
            self.retry_at = time.monotonic() + self.reconnect_delay

    async def do_reads(self):
        return await asyncio.to_thread(self._poll)

    def _poll(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_poll:
                return False
            self.next_poll = now + 1.0 / self.rate
            try:
                if self.t is None:
                    if now < self.retry_at:
                        return False
                    self.t = self._create_connection()
                    self._pv('Identity').set(self.t.initialize()[:39])
                self._pv('Comm_Status').set(True)
                voltage = self.t.read_ch1_voltage()
                self._pv('VI').set(voltage)
                self._pv('VI').set_alarm(severity=alarm.NO_ALARM,
                                         alarm=alarm.NO_ALARM)
                self._pv('Data_Valid').set(True)
                self._pv('Last_Error').set('')
                self.last_error = ''
                return True
            except MeasurementUnavailable as exc:
                # The instrument remains connected: an unconfigured measurement
                # must not trigger a reconnect storm or fabricate a voltage.
                self._error(exc, disconnect=False)
            except Exception as exc:
                self._error(exc, disconnect=True)
            return False
