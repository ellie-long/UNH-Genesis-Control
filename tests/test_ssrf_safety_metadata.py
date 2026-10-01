"""Production aioca read-format contract with real EPICS DBR decoding, no CA I/O."""
import ctypes
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import aioca
from epicscorelibs.ca import dbr
from logic_devices.ssrf_epics_io import EpicsIO
from logic_devices.ssrf_manual_rf import ManualRF
from logic_devices.ssrf_shutdown import establish_safe_shutdown
from tests.test_ssrf_rf_operation import IO


def decoded_value(value, format, severity=0, status=0):
    # Decode an actual DBR buffer with the installed CA library's conversion.
    # RAW has no alarm metadata; TIME carries real structure fields.
    channel = SimpleNamespace(name='TEST:RBV')
    with patch.object(dbr.cadef, 'ca_field_type', return_value=dbr.DBR_DOUBLE), \
         patch.object(dbr.cadef, 'ca_element_count', return_value=1):
        code, convert = dbr.type_to_dbr(channel, None, format)
    buffer = dbr.DbrCodeToType[code]()
    buffer.raw_value[0] = value
    if format != aioca.FORMAT_RAW:
        buffer.severity = severity
        buffer.status = status
    return convert(ctypes.byref(buffer), code, 1)


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_lifecycle_source_verification_uses_safety_reads(self):
        startup = Path('scripts/start_ssrf_physical.sh').read_text()
        shutdown = Path('scripts/stop_ssrf_physical.sh').read_text()
        for text in (startup, shutdown):
            for pv in ('GEN:RF_RBV', 'GEN:FM_RBV', 'WGEN:OUTPUT_RBV'):
                self.assertIn(f'io.read_safety("UNH:GEN:SSRF:{pv}")', text)
                self.assertNotIn(f'caget("UNH:GEN:SSRF:{pv}"', text)
        self.assertIn('io.read_safety("UNH:GEN:SSRF:SEQ:MANUAL_ARMED")', startup)
        self.assertIn('io.read_safety("UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV")', startup)
        self.assertIn('set -euo pipefail', shutdown)
        self.assertLess(shutdown.index('await establish_safe_shutdown'),
                        shutdown.index('screen -S "$s" -X quit'))

    async def test_production_time_format_and_raw_compatibility(self):
        formats = []
        async def caget(pv, format=aioca.FORMAT_RAW, **kwargs):
            formats.append(format)
            return decoded_value(0, format)
        with patch('logic_devices.ssrf_epics_io.aioca.caget', caget):
            io = EpicsIO()
            raw = await io.read('TEST')
            self.assertFalse(hasattr(raw, 'severity'))
            value = await io.read_safety('TEST')
            self.assertEqual(value.severity, 0)
            self.assertEqual(value.status, 0)
        self.assertEqual(formats, [aioca.FORMAT_RAW, aioca.FORMAT_TIME])

    async def test_missing_metadata_alarms_nonfinite_and_failed_reads(self):
        values = [decoded_value(0, aioca.FORMAT_RAW),
                  decoded_value(0, aioca.FORMAT_TIME, severity=2, status=1),
                  decoded_value(0, aioca.FORMAT_TIME, severity=0, status=1),
                  decoded_value(float('nan'), aioca.FORMAT_TIME),
                  decoded_value(float('inf'), aioca.FORMAT_TIME),
                  RuntimeError('read failed')]
        for value in values:
            with self.subTest(value=value):
                mock = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
                with patch('logic_devices.ssrf_epics_io.aioca.caget', mock):
                    with self.assertRaises(RuntimeError):
                        await EpicsIO().read_safety('TEST')
                    mock.assert_awaited_once_with('TEST', format=aioca.FORMAT_TIME, timeout=2.0)

    async def test_cleanup_alarm_off_cannot_move_switches(self):
        fake = IO()
        io = EpicsIO(execution_enabled=True)
        io.write, io.sleep = fake.write, fake.sleep
        async def caget(pv, format=aioca.FORMAT_RAW, **kwargs):
            value = await fake.read(pv)
            return decoded_value(value, format, severity=int(pv.endswith(':RF_RBV'))*2)
        with patch('logic_devices.ssrf_epics_io.aioca.caget', caget):
            manual = ManualRF(io, lambda: False)
            self.assertFalse(await manual.safe_off())
        self.assertEqual(len(fake.writes), 4)
        self.assertFalse(any('PORT_CMD' in pv for pv, _ in fake.writes))

    async def test_shutdown_alarm_or_missing_metadata_cannot_verify_safe(self):
        for broken in ('alarm', 'raw'):
            with tempfile.TemporaryDirectory() as tmp:
                fake = IO()
                io = EpicsIO(execution_enabled=True)
                io.write, io.sleep = fake.write, fake.sleep
                async def caget(pv, format=aioca.FORMAT_RAW, **kwargs):
                    value = await fake.read(pv)
                    if pv.endswith(':OUTPUT_RBV'):
                        if broken == 'raw': return decoded_value(value, aioca.FORMAT_RAW)
                        return decoded_value(value, format, severity=2, status=1)
                    return decoded_value(value, format)
                with patch('logic_devices.ssrf_epics_io.aioca.caget', caget):
                    with self.assertRaisesRegex(RuntimeError, 'OUTPUT_RBV'):
                        await establish_safe_shutdown(io, lock_directory=tmp)
                self.assertFalse(any('PORT_CMD' in pv for pv, _ in fake.writes))


if __name__ == '__main__': unittest.main()
