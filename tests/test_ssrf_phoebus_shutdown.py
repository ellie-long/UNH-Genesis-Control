"""Exercise the stop script's Python block with fake GUI and EPICS operations."""
from pathlib import Path
import types
import unittest
from unittest.mock import patch

from tests.test_microwave_phoebus_shutdown import FakeProcesses, java
from tools.phoebus_shutdown import Process, close_instance

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / 'configs/phoebus/ssrf_physical.ini'
RESOURCE = ROOT / 'gui/genesis_ssrf.bob'


class Ownership(unittest.IsolatedAsyncioTestCase):
    async def test_ssrf_only_and_repeated_missing_gui(self):
        runtime = FakeProcesses([java(1, SETTINGS, RESOURCE), java(2),
                                 Process(3, 'today', '/jdk/bin/java -jar unrelated.jar'),
                                 java(4, SETTINGS, ROOT / 'gui/genesis_ssrf_wavegen.bob'),
                                 java(5, ROOT / 'configs/phoebus/genesis_mac_settings.ini', RESOURCE)])
        await close_instance(SETTINGS, RESOURCE, runtime)
        await close_instance(SETTINGS, RESOURCE, runtime)
        self.assertEqual(runtime.signals, [1])
        self.assertEqual(set(runtime.processes), {2, 3, 4, 5})


class StopIntegration(unittest.TestCase):
    def execute_stop_block(self, gui_fail=False, safety_fail=False):
        events = []
        async def close(settings, resource):
            events.append(('gui', settings, resource))
            if gui_fail:
                raise RuntimeError('signal refused')
        async def safe(io, **kwargs):
            events.append(('safe-off', kwargs))
            if safety_fail:
                raise RuntimeError('RF/FM/WaveGen unconfirmed')
        class IO:
            def __init__(self, **kwargs):
                events.append(('io', kwargs))
            async def read_safety(self, pv):
                events.append(('read', pv))
                return 3 if ':RF_SW:' in pv else 0
        text = (ROOT / 'scripts/stop_ssrf_physical.sh').read_text()
        block = text.split("<<'PY_STOP'\n", 1)[1].split('\nPY_STOP', 1)[0]
        modules = {
            'logic_devices.ssrf_shutdown': types.SimpleNamespace(establish_safe_shutdown=safe),
            'logic_devices.ssrf_epics_io': types.SimpleNamespace(EpicsIO=IO),
        }
        with patch.dict('sys.modules', modules), \
                patch('tools.phoebus_shutdown.close_instance', close), \
                patch.dict('os.environ', {'GENESIS_SSRF_SEQ_PRESENT': '1'}), \
                patch('pathlib.Path.cwd', return_value=ROOT):
            try:
                exec(compile(block, 'fake_ssrf_stop', 'exec'), {})
            except SystemExit as exc:
                return events, str(exc)
        return events, None

    def test_gui_closes_before_safe_off(self):
        events, error = self.execute_stop_block()
        self.assertIsNone(error)
        self.assertEqual(events[0], ('gui', SETTINGS, RESOURCE))
        self.assertEqual(events[2][0], 'safe-off')
        self.assertEqual(len([e for e in events if e[0] == 'read']), 5)

    def test_gui_failure_still_attempts_and_verifies_hardware_safe_off(self):
        events, error = self.execute_stop_block(gui_fail=True)
        self.assertEqual(events[2][0], 'safe-off')
        self.assertEqual(len([e for e in events if e[0] == 'read']), 5)
        self.assertIn('signal refused', error)
        self.assertIn('safe baseline confirmed', error)
        self.assertIn('retaining physical owners', error)

    def test_gui_failure_and_safe_off_failure_reports_hardware_failure(self):
        events, error = self.execute_stop_block(gui_fail=True, safety_fail=True)
        self.assertEqual(events[2][0], 'safe-off')
        self.assertIn('RF/FM/WaveGen unconfirmed', error)
        self.assertNotIn('safe baseline confirmed', error)
