"""Independent GUI shutdown tests; never inspect or signal real processes."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tools.phoebus_shutdown import Process, ProcessRuntime, belongs_to, close_instance

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / 'configs/phoebus/genesis_mac_settings.ini'
RESOURCE = ROOT / 'gui/genesis_microwave.bob'


def java(pid, settings=SETTINGS, resource=RESOURCE, started='Thu Oct 1 12:00:00 2026'):
    return Process(pid, started, f'/jdk/bin/java -jar /Applications/CSS_Phoebus.app/product-1/product-1.jar -settings {settings} -resource {resource}')


class FakeProcesses:
    def __init__(self, processes=(), fail=False):
        self.processes = {p.pid: p for p in processes}
        self.signals = []
        self.fail = fail

    def snapshot(self):
        return dict(self.processes)

    def terminate(self, pid):
        self.signals.append(pid)
        if self.fail:
            raise PermissionError('signal refused')
        self.processes.pop(pid, None)


class PhoebusShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_only_microwave_java_instances_close(self):
        ssrf = java(2, ROOT / 'configs/phoebus/ssrf_physical.ini', ROOT / 'gui/genesis_ssrf.bob')
        unrelated = Process(3, 'today', '/jdk/bin/java -jar unrelated.jar')
        other_phoebus = java(4, SETTINGS, ROOT / 'gui/archive.bob')
        other_profile = java(5, ROOT / 'configs/phoebus/ssrf_physical.ini', RESOURCE)
        launcher = Process(6, 'today', f'/bin/sh main.command -settings {SETTINGS} -resource {RESOURCE}')
        runtime = FakeProcesses([java(1), ssrf, unrelated, other_phoebus, other_profile, launcher, java(7)])
        await close_instance(SETTINGS, RESOURCE, runtime)
        self.assertEqual(runtime.signals, [1, 7])
        self.assertEqual(set(runtime.processes), {2, 3, 4, 5, 6})

    async def test_missing_gui_and_repeated_close(self):
        runtime = FakeProcesses([java(1)])
        await close_instance(SETTINGS, RESOURCE, runtime)
        await close_instance(SETTINGS, RESOURCE, runtime)
        self.assertEqual(runtime.signals, [1])

    async def test_stale_pid_reused_by_unrelated_java_is_not_signaled(self):
        old = java(1)
        replacement = Process(1, 'later', '/jdk/bin/java -jar unrelated.jar')
        runtime = FakeProcesses()
        snapshots = iter([{1: old}, {1: replacement}, {1: replacement}])
        runtime.snapshot = lambda: next(snapshots)
        await close_instance(SETTINGS, RESOURCE, runtime)
        self.assertEqual(runtime.signals, [])

    async def test_missing_pid_is_not_signaled(self):
        runtime = FakeProcesses()
        snapshots = iter([{1: java(1)}, {}, {}])
        runtime.snapshot = lambda: next(snapshots)
        await close_instance(SETTINGS, RESOURCE, runtime)
        self.assertEqual(runtime.signals, [])

    async def test_survivor_fails_with_bounded_wait(self):
        runtime = FakeProcesses([java(1)], fail=True)
        with self.assertRaisesRegex(RuntimeError, 'surviving PIDs.*signal refused'):
            await close_instance(SETTINGS, RESOURCE, runtime, timeout=0)
        self.assertEqual(runtime.signals, [1])

    async def test_termination_race_process_already_gone(self):
        runtime = FakeProcesses([java(1)])
        def gone(pid):
            runtime.processes.pop(pid)
            raise ProcessLookupError()
        runtime.terminate = gone
        await close_instance(SETTINGS, RESOURCE, runtime)

    def test_exact_arguments_required(self):
        for command in [java(1).command + ' -resource other.bob',
                        java(1).command.replace(str(RESOURCE), str(RESOURCE) + '.backup'),
                        java(1).command.replace('product-1.jar', 'unrelated.jar'),
                        'echo ' + java(1).command]:
            self.assertFalse(belongs_to(Process(1, 'today', command), SETTINGS, RESOURCE))

    def test_process_parser_preserves_start_identity(self):
        row = f' 12 Thu Oct 1 12:00:00 2026 {java(12).command}\n'
        with patch('tools.phoebus_shutdown.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, row, '')):
            process = ProcessRuntime().snapshot()[12]
        self.assertEqual(process.started, 'Thu Oct 1 12:00:00 2026')
        self.assertTrue(belongs_to(process, SETTINGS, RESOURCE))

    def test_validated_independent_launcher_arguments(self):
        for script, settings, resource in [
                ('start_microwave_phoebus.sh', 'genesis_mac_settings.ini', 'genesis_microwave.bob'),
                ('start_ssrf_phoebus.sh', 'ssrf_physical.ini', 'genesis_ssrf.bob')]:
            text = (ROOT / 'scripts' / script).read_text()
            self.assertIn(settings, text)
            self.assertIn(resource, text)
            self.assertIn('start_phoebus_instance.sh', text)


if __name__ == '__main__':
    unittest.main()
