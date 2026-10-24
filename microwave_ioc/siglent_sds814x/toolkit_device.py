"""Bounded CH1 snapshots; SIGLENT SDS800X HD Programming Guide EN11G.

Uses the standard launcher prefix and configured channel record names.
The configured logical channel (for example SIG) monitors physical scope CH1.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import math
import socket
import socketserver
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from softioc import builder, alarm
from genesis_common.upstream_base_device import BaseDevice

HISTORY_SECONDS = 300
MAX_WAVE_POINTS = 1000
TRIGGER_STATUSES = frozenset(('STOP', 'ARM', 'READY', 'AUTO', "TRIG'D", 'ROLL'))

class SCPIError(ValueError):
    pass

class SocketSCPI:
    """One owner serializes complete transactions. Timeout closes the stream.

    Exact-length binary reads preserve LF/CR bytes inside the payload. LF, CRLF and Siglent double-LF terminators are accepted. Responses and total query time are bounded.
    """
    def __init__(self, host, port=5025, timeout=2.0):
        self.timeout = float(timeout)
        self.sock = socket.create_connection((host, int(port)), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buffer = bytearray()
        self.deadline = 0

    def close(self):
        self.sock.close()

    def write(self, command):
        if '\n' in command or '\r' in command:
            raise ValueError('Embedded SCPI newline')
        self.sock.settimeout(self.timeout)
        self.sock.sendall((command + '\n').encode('ascii'))

    def _read(self, n):
        while len(self.buffer) < n:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('SCPI response deadline')
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('Scope closed connection')
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:n])
        del self.buffer[:n]
        return result

    def query(self, command, binary=False):
        try:
            self.write(command)
            self.deadline = time.monotonic() + self.timeout
            if binary:
                first = self._read(1)
                # Siglent terminates binary blocks with LF LF. Consume any
                # leftover terminator on the next transaction, even if split TCP.
                for _ in range(4):
                    if first not in (b'\r', b'\n'):
                        break
                    first = self._read(1)
                if first != b'#':
                    raise ValueError('Missing SCPI block header')
                digits = self._read(1)
                if digits not in b'123456789' or len(digits) != 1:
                    raise ValueError('Invalid block length digit')
                raw_length = self._read(int(digits))
                if not raw_length.isdigit():
                    raise ValueError('Invalid block length')
                size = int(raw_length)
                if not 1 <= size <= 2000:
                    raise ValueError('Expected at most 1000 WORD points')
                data = self._read(size)
                end = self._read(1)
                if end == b'\r':
                    end = self._read(1)
                if end != b'\n':
                    raise ValueError('Missing block terminator')
                return data
            line = bytearray()
            while len(line) < 4096:
                char = self._read(1)
                if char == b'\n' and not line:
                    continue
                if char == b'\n':
                    return line.decode('ascii').strip()
                line.extend(char)
            raise ValueError('SCPI line too long')
        except Exception:
            self.close()
            raise

    def query_many(self, commands):
        """One SCPI program message; firmware returns semicolon-separated values.

        Keep metadata/status queries together to reduce network round trips.
        Binary blocks retain their separate, exact-length transactions.
        """
        if not commands or any(not c.endswith('?') or ';' in c for c in commands):
            raise ValueError('Expected simple SCPI queries')
        response = self.query(';'.join(commands))
        values = [value.strip() for value in response.split(';')]
        if len(values) != len(commands) or any(not v for v in values):
            self.close()
            raise ValueError('Incomplete grouped SCPI response: ' + repr(response))
        return values


def parse_preamble(preamble):
    """Decode the 346-byte little-endian WAVEDESC (guide pp. 754-755)."""
    if len(preamble) != 346 or preamble[:8] != b'WAVEDESC':
        raise ValueError('Invalid SIGLENT WAVEDESC')
    fields = {'width': (32, 'h'), 'order': (34, 'h'), 'length': (36, 'i'),
              'points': (116, 'i'), 'first': (132, 'i'), 'stride': (136, 'i'),
              'gain': (156, 'f'), 'offset': (160, 'f'),
              'codes': (164, 'f'), 'adc': (172, 'h'), 'interval': (176, 'f'),
              'delay': (180, 'd'), 'probe': (328, 'f'), 'source': (344, 'h')}
    p = {name: struct.unpack_from('<' + kind, preamble, offset)[0]
         for name, (offset, kind) in fields.items()}
    checks = {'width': p['width'] == 1, 'order': p['order'] == 0,
              'length': p['length'] == 346, 'source': p['source'] == 0,
              'points': p['points'] >= 1, 'first': p['first'] >= 0,
              'stride': p['stride'] >= 1, 'adc': 8 <= p['adc'] <= 16}
    checks.update({k: p[k] > 0 for k in ('gain', 'codes', 'interval', 'probe')})
    invalid = [f'{k}={v!r}' for k, v in p.items()
               if not np.isfinite(v) or not checks.get(k, True)]
    if invalid:
        raise ValueError('Invalid CH1 WORD parameters: ' + ', '.join(invalid))
    # Offset 144 is documented as sequence frame count, but firmware 4.8.12
    # reports transfer point count there outside sequence mode. It is not a
    # conversion parameter. Sequence mode is checked explicitly with ACQ:SEQ?.
    return p


def decode_waveform(preamble, payload, timebase):
    p = parse_preamble(preamble)
    if not 2 <= len(payload) <= 2000 or len(payload) % 2:
        raise ValueError('Invalid WORD payload size')
    if not np.isfinite(timebase) or timebase <= 0:
        raise ValueError('Invalid timebase')
    codes = np.frombuffer(payload, dtype='<i2').astype(float)
    # WORD values are signed and left aligned; code_per_div already matches
    # their scale. Apply the configured probe to both gain and offset.
    volts = (codes * p['gain'] / p['codes'] - p['offset']) * p['probe']
    indices = p['first'] + np.arange(len(codes)) * p['stride']
    seconds = p['delay'] - 5 * timebase + indices * p['interval']
    return seconds, volts


@dataclass(frozen=True)
class AcquisitionState:
    status: str
    mode: str
    enabled: bool
    units: str

    @property
    def live(self):
        return (self.enabled and self.units == 'V' and self.status != 'STOP'
                and (self.status not in ('ARM', 'READY') or self.mode == 'AUTO'))


class Controller:
    def __init__(self, transport):
        self.io = transport

    def initialize(self):
        self.io.write('CHDR OFF')
        identity = self.io.query('*IDN?')
        parts = identity.upper().split(',')
        if (len(parts) < 2 or 'SIGLENT' not in parts[0] or
                parts[1].strip().replace(' ', '') not in ('SDS814XHD', 'SDS814X')):
            raise ValueError('Expected SIGLENT SDS814X HD: ' + identity)
        # Communication/transfer settings only; preserve saved acquisition setup.
        self.configure_transfer()
        return identity

    def configure_transfer(self):
        self.io.write(':WAV:SOUR C1;:WAV:WIDT WORD;:WAV:BYT LSB')

    def acquisition_state(self):
        status, mode, enabled, units = self.io.query_many(
            (':TRIG:STAT?', ':TRIG:MODE?', ':CHAN1:SWIT?', ':CHAN1:UNIT?'))
        status, mode, enabled, units = (s.upper() for s in (status, mode, enabled, units))
        if status not in TRIGGER_STATUSES:
            raise ValueError('Unknown acquisition status ' + status)
        if enabled not in ('ON', 'OFF'):
            raise ValueError('Unknown CH1 switch ' + enabled)
        return AcquisitionState(status, mode, enabled == 'ON', units)

    def snapshot(self):
        sequence, points, scale = self.io.query_many(
            (':ACQ:SEQ?', ':ACQ:POIN?', ':TIM:SCAL?'))
        if sequence.upper() != 'OFF':
            raise SCPIError('Disable sequence acquisition on scope')
        total = float(points)
        if not np.isfinite(total) or not 1 <= total <= 100_000_000 or total != int(total):
            raise ValueError('Invalid acquisition point count')
        total = int(total)
        # Read the final contiguous window. SDS814X HD firmware 4.8.12
        # truncates decimated transfers, which can omit the requested endpoint.
        # Stride 1 preserves the final-sample voltage without extra acquisition
        # writes or a second waveform from a potentially different trigger.
        count = min(total, MAX_WAVE_POINTS)
        first, stride = total - count, 1
        # One program message reasserts transfer settings and range each poll.
        # Keep checks around the payload; do not cache voltage conversion.
        self.io.write(':WAV:SOUR C1;:WAV:WIDT WORD;:WAV:BYT LSB;'
                      f':WAV:STAR 0;:WAV:INT 1;:WAV:POIN {count};:WAV:STAR {first}')
        timebase = float(scale)
        before = self.io.query(':WAV:PRE?', binary=True)
        parameters = parse_preamble(before)
        if parameters['first'] != first or parameters['stride'] != stride:
            raise SCPIError('Scope did not apply waveform transfer range')
        payload = self.io.query(':WAV:DATA?', binary=True)
        after = self.io.query(':WAV:PRE?', binary=True)
        # Compare conversion/range fields, not changing trigger timestamps.
        end_scale, end_points, end_sequence = self.io.query_many(
            (':TIM:SCAL?', ':ACQ:POIN?', ':ACQ:SEQ?'))
        if (parameters != parse_preamble(after) or
                timebase != float(end_scale) or total != float(end_points) or
                end_sequence.upper() != 'OFF'):
            raise SCPIError('Scale changed during transfer; retry')
        if len(payload) != count * 2:
            raise SCPIError('Unexpected waveform point count')
        return decode_waveform(before, payload, timebase)

    def close(self):
        self.io.close()


class DeviceConnection(Controller):
    """Raw TCP/SCPI connection used by the standard BaseDevice hooks."""

    def __init__(self, host, port=5025, timeout=2.0):
        super().__init__(SocketSCPI(host, port, timeout))


class History:
    """Five minutes of received CH1 readings, with gaps for invalid polls."""
    def __init__(self):
        self.rows = deque(maxlen=6002)

    def add(self, monotonic, epoch, reading=np.nan):
        self.rows.append((monotonic, epoch, float(reading)))
        self.prune(monotonic)

    def prune(self, now):
        while self.rows and self.rows[0][0] < now - HISTORY_SECONDS:
            self.rows.popleft()

    def arrays(self, now):
        self.prune(now)
        data = np.array(self.rows, dtype=float).reshape(-1, 3)
        return data[:, 0] - now, data[:, 1], data[:, 2]


# BaseDevice integration: socket I/O stays off the asyncio dispatcher.
LOG = logging.getLogger(__name__)

class Device(BaseDevice):
    """Standard toolkit Device; channels names the logical CH1 monitor."""

    def _pv(self, key):
        return self.pvs[f'{self.root}_{key}']

    def _add_pv(self, key, factory, **fields):
        name = f'{self.root}_{key}'
        self.pvs[name] = factory(name, **fields)

    def _create_pvs(self):
        if (len(self.channels) != 1 or not isinstance(self.channels[0], str) or
                not self.channels[0] or any(c.isspace() for c in self.channels[0])):
            raise ValueError('Scope channel must be one nonempty record name')
        channels = self._skip_none_channels()
        if len(channels) != 1:
            raise ValueError('SDS814X HD requires one logical channel, for example channels: [SIG]')
        self.root = channels[0]
        self.lock = threading.RLock()
        self.history = History()
        self.rate = float(self.settings.get('update_hz', 5))
        self.timeout = float(self.settings.get('timeout', 2))
        self.reconnect_delay = float(self.settings.get('reconnect_delay', 3))
        if not .2 <= self.rate <= 20:
            raise ValueError('update_hz must be 0.2..20')
        if not all(np.isfinite(x) and x > 0 for x in
                   (self.timeout, self.reconnect_delay)):
            raise ValueError('Timeout and polling periods must be finite and positive')
        self.next_poll = self.retry_at = 0
        self.last_frame_mono = None
        self.sequence = 0
        self.stopped_frame = False
        self.last_status = None
        self._add_pv('VI', builder.aIn, EGU='V', PREC=6)
        for name in ('Comm_Status', 'Data_Valid'):
            self._add_pv(name, builder.boolIn, ZNAM='No', ONAM='Yes')
        for name in ('Last_Error', 'Acq_Status', 'Units', 'Identity'):
            self._add_pv(name, builder.stringIn)
        for name, units in (('Frame_Epoch', 's'), ('Data_Age', 's'), ('Actual_Hz', 'Hz'), ('Read_Duration', 's')):
            self._add_pv(name, builder.aIn, EGU=units, PREC=3)
        self._add_pv('Frame_Seq', builder.longIn)
        self._add_pv('Wave_N', builder.longIn)
        self._add_pv('History_N', builder.longIn)
        for name in ('Wave_Time', 'Wave_Volts'):
            self._add_pv(name, builder.WaveformIn, length=1000, datatype=float)
        # One packed PV is the atomic logging interface: version, sequence,
        # receive epoch, count, live eligibility, all X values, all Y values.
        self._add_pv('Frame', builder.WaveformIn, length=2005, datatype=float)
        for name in ('History_Time', 'History_Epoch', 'History_Reading'):
            self._add_pv(name, builder.WaveformIn, length=6004, datatype=float)
        self._pv('Data_Age').set(-1)
        self._invalidate()

    def _create_connection(self):
        return DeviceConnection(
            self.settings['ip'], self.settings.get('port', 5025), self.timeout)

    def connect(self):
        # master_ioc calls connect before iocInit; defer network I/O until polling.
        self.retry_at = 0

    def reconnect(self):
        with self.lock:
            self._disconnect()
            self.retry_at = 0

    def _disconnect(self):
        if self.t is not None:
            self.t.close()
        self.t = None
        self._pv('Comm_Status').set(False)

    def _invalidate(self):
        self._pv('Data_Valid').set(False)
        self._pv('Actual_Hz').set(0)
        self._pv('VI').set(float('nan'))
        for name in ('VI', 'Wave_Time', 'Wave_Volts', 'Frame'):
            self._pv(name).set_alarm(severity=alarm.INVALID_ALARM, alarm=alarm.READ_ALARM)

    def _error(self, exc, disconnect=True):
        LOG.warning('SDS814X HD: %s', exc)
        self._pv('Last_Error').set(str(exc)[:39])
        self._invalidate()
        if disconnect:
            self._disconnect()
            self._pv('Acq_Status').set('Disconnected')
            self.retry_at = time.monotonic() + self.reconnect_delay

    def _publish_history(self, now):
        times, epochs, values = self.history.arrays(now)
        self._pv('History_N').set(len(times))
        self._pv('History_Time').set(times)
        self._pv('History_Epoch').set(epochs)
        self._pv('History_Reading').set(values)

    async def do_reads(self):
        return await asyncio.to_thread(self._poll)

    def _poll(self):
        with self.lock:
            start = time.monotonic()
            if start < self.next_poll:
                return False
            self.next_poll = start + 1 / self.rate
            success = False
            live = False
            try:
                if self.t is None and start >= self.retry_at:
                    self.t = self._create_connection()
                    self._pv('Identity').set(self.t.initialize()[:39])
                    self.stopped_frame = False
                    self.last_status = None
                if self.t is not None:
                    state = self.t.acquisition_state()
                    status, units = state.status, state.units
                    self._pv('Acq_Status').set(status)
                    self._pv('Units').set(units)
                    if status != self.last_status:
                        self.stopped_frame = False
                        self.last_status = status
                    live = state.live
                    take_frame = live or (state.enabled and units == 'V' and
                                          status == 'STOP' and not self.stopped_frame)
                    if take_frame:
                        xs, ys = self.t.snapshot()
                        end_state = self.t.acquisition_state()
                        if end_state.units != units or end_state.enabled != state.enabled:
                            raise SCPIError('CH1 state changed during transfer; retry')
                        live = live and end_state.live
                        now, epoch = time.monotonic(), time.time()
                        self.sequence = self.sequence % 2147483646 + 1
                        self._pv('Wave_Time').set(xs)
                        self._pv('Wave_Volts').set(ys)
                        self._pv('Wave_N').set(len(xs))
                        self._pv('Frame_Epoch').set(epoch)
                        self._pv('Frame').set(np.r_[1., self.sequence, epoch, len(xs), float(live), xs, ys])
                        self._pv('Frame_Seq').set(self.sequence)
                        self._pv('Actual_Hz').set(0 if self.last_frame_mono is None else 1 / (now - self.last_frame_mono))
                        self.last_frame_mono = now
                        self.stopped_frame = status == 'STOP'
                        if live:
                            reading = float(ys[-1])
                            self._pv('VI').set(reading)
                            self.history.add(now, epoch, reading)
                        for name in ('VI', 'Wave_Time', 'Wave_Volts', 'Frame'):
                            self._pv(name).set_alarm(severity=alarm.NO_ALARM, alarm=alarm.NO_ALARM)
                    if not live:
                        self._invalidate()
                    self._pv('Data_Valid').set(live)
                    self._pv('Comm_Status').set(True)
                    self._pv('Last_Error').set('' if units == 'V' else 'Set CH1 units to V on scope')
                    success = True
            except SCPIError as exc:
                self._error(exc, disconnect=False)
                live = False
            except Exception as exc:
                self._error(exc)
                live = False
            finally:
                now = time.monotonic()
                if not live:
                    self.history.add(now, time.time())
                self._publish_history(now)
                self._pv('Data_Age').set(-1 if self.last_frame_mono is None else now - self.last_frame_mono)
                self._pv('Read_Duration').set(now - start)
                # Start-to-start rate limit. A slow read already consumed its
                # interval; do not add another full interval after it finishes.
                # Schedule from this poll only, so missed periods never queue up.
                self.next_poll = max(self.next_poll, now)
            return success


"""Local commissioning simulator, not a hardware conformance test.

python -m devices.instruments.siglent_sds814x --port 5025
"""
def simulator_preamble(count=1000, first=0, stride=1):
    """Synthetic protocol fixture; not a hardware capture."""
    p = bytearray(346)
    p[:8] = b'WAVEDESC'
    for offset, kind, value in ((32,'h',1),(34,'h',0),(36,'i',346),
            (60,'i',20000),(116,'i',10000),(132,'i',first),(136,'i',stride),
            (144,'i',count),
            (156,'f',1),(160,'f',0),(164,'f',6400),(172,'h',12),
            (176,'f',.000001),(180,'d',0),(328,'f',1),(344,'h',0)):
        struct.pack_into('<'+kind,p,offset,value)
    return bytes(p)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        config = {':CHAN1:SWIT':'ON', ':CHAN1:UNIT':'V', ':ACQ:SEQ':'OFF',
                  ':ACQ:POIN':'10000', ':TIM:SCAL':'.001',
                  ':WAV:STAR':'0', ':WAV:INT':'1', ':WAV:POIN':'1000', ':TRIG:MODE':'AUTO'}
        while line := self.rfile.readline():
            commands = line.decode().strip().split(';')
            grouped = len(commands) > 1 and all(c.endswith('?') for c in commands)
            replies = []
            for cmd in commands:
                if cmd == '*IDN?':
                    reply = 'SIGLENT,SDS814X HD,SIMULATOR,1.0'
                elif cmd == ':TRIG:STAT?':
                    reply = 'Auto'
                elif cmd in (':WAV:PRE?', ':WAV:DATA?'):
                    count = int(config[':WAV:POIN'])
                    first, stride = int(config[':WAV:STAR']), int(config[':WAV:INT'])
                    if cmd == ':WAV:PRE?':
                        data = simulator_preamble(count, first, stride)
                    else:
                        offset = 1000 * math.sin(time.monotonic()/5)
                        data = np.array([int(offset+3000*math.sin((first+i*stride)*.006))
                                         for i in range(count)],dtype='<i2').tobytes()
                    try:
                        self.wfile.write(f'#{len(str(len(data)))}{len(data)}'.encode()+data+b'\n\n')
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    continue
                elif cmd.endswith('?'):
                    reply = config[cmd[:-1]]
                else:
                    if ' ' in cmd:
                        name, value = cmd.split(' ',1)
                        if name in config:
                            config[name] = value
                    continue
                if grouped:
                    replies.append(reply)
                    if len(replies) < len(commands):
                        continue
                    reply = ';'.join(replies)
                try:
                    self.wfile.write((reply+'\n').encode())
                except (BrokenPipeError, ConnectionResetError):
                    return

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

def simulator_main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=5025)
    args=p.parse_args()
    with Server(('127.0.0.1',args.port),Handler) as server:
        print(f'SDS814X HD simulator on 127.0.0.1:{server.server_address[1]}',flush=True)
        server.serve_forever()


if __name__ == '__main__':
    simulator_main()
