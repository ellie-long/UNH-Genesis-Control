"""Pure launcher decisions; physical lifecycle scripts are never executed."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from logic_devices.ssrf_stack_status import EXPECTED, classify_stack


class StackTests(unittest.TestCase):
    def snapshots(self):
        screens = '\n'.join(f'{100+i}.{name}\t(Detached)' for i,name in enumerate(EXPECTED))
        commands = [f'python -u master_ioc.py -s {signature} -i device'
                    if signature.startswith('configs/') else f'python -m {signature}'
                    for signature in EXPECTED.values()]
        return screens, commands

    def test_zero_uses_existing_startup(self):
        self.assertEqual(classify_stack('', [], lambda _: False)[0], 'ZERO')
        text = Path('scripts/start_ssrf_system.sh').read_text()
        self.assertIn('"$ROOT/scripts/genesisctl.sh" ssrf-start', text)

    def test_complete_reuse_does_not_require_rf_off(self):
        screens, commands = self.snapshots()
        self.assertEqual(classify_stack(screens, commands, lambda _: True)[0], 'COMPLETE')
        text = Path('scripts/start_ssrf_system.sh').read_text()
        complete = text.split('COMPLETE)')[1].split(';;')[0]
        for forbidden in ('ssrf-physical-status','caget','caput','ssh','ssrf-start'):
            self.assertNotIn(forbidden, complete)
        self.assertIn('start_ssrf_phoebus.sh', text)
        module = Path('logic_devices/ssrf_stack_status.py').read_text()
        self.assertNotIn('import aioca', module)
        self.assertNotIn('ssrf_ioc', module)

    def test_partial_stale_duplicate_missing_process_refused(self):
        screens, commands = self.snapshots()
        for s,c,alive in ((screens.splitlines()[0], commands, lambda _: True),
                          (screens,commands,lambda _: False),
                          (screens+'\n'+screens,commands,lambda _:True),
                          (screens,commands[:-1],lambda _:True),
                          (screens,commands+commands,lambda _:True),
                          ('',commands,lambda _:True)):
            self.assertEqual(classify_stack(s,c,alive)[0], 'PARTIAL')

    def test_absolute_config_paths_also_identify_existing_owners(self):
        screens, commands = self.snapshots()
        commands = [command.replace('configs/', '/other/checkout/configs/')
                    for command in commands]
        self.assertEqual(classify_stack(screens, commands, lambda _: True)[0], 'COMPLETE')
        self.assertEqual(classify_stack('', commands, lambda _: True)[0], 'PARTIAL')

    def test_launcher_runtime_decisions_with_stubbed_entry_points(self):
        # Execute the actual launcher in an isolated harness. No repository
        # physical lifecycle entry point or GUI is executed by this test.
        for state, expected in (('ZERO', ['ssrf-start', 'GUI']),
                                ('COMPLETE', ['GUI']), ('PARTIAL', [])):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                scripts = root / 'scripts'
                scripts.mkdir()
                launcher = scripts / 'start_ssrf_system.sh'
                launcher.write_text(Path('scripts/start_ssrf_system.sh').read_text())
                log = root / 'calls'
                stubs = {
                    root / 'python': '#!/bin/bash\nprintf "%s\\n" "$TEST_STACK_STATE"\n'
                                    'if [[ "$TEST_STACK_STATE" == PARTIAL ]]; then exit 1; fi\n',
                    scripts / 'genesisctl.sh': '#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_CALL_LOG"\n',
                    scripts / 'start_ssrf_phoebus.sh': '#!/bin/bash\necho GUI >> "$TEST_CALL_LOG"\n',
                }
                for path, text in stubs.items():
                    path.write_text(text)
                    path.chmod(0o755)
                env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                           TEST_STACK_STATE=state, TEST_CALL_LOG=str(log))
                result = subprocess.run(['bash', str(launcher)], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1 if state == 'PARTIAL' else 0)
                self.assertEqual(log.read_text().splitlines() if log.exists() else [], expected)
