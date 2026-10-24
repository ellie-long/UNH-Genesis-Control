"""Lifecycle regression: all process and EPICS operations are fakes."""
import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tools import microwave_shutdown as shutdown

ROOT = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self, names=shutdown.ORDER, shared=(), survivor=None, unknown=()):
        self.names = {name: f'{100 + i}.{name}' for i, name in enumerate(names)}
        self.shared = set(shared)
        self.unknown = set(unknown)
        self.survivor = survivor
        self.events = []

    async def close_gui(self):
        pass

    def snapshot(self):
        sessions = {n: [sid] for n, sid in self.names.items()}
        parents, owned, shared = {}, {}, {}
        for name, sid in self.names.items():
            pid = int(sid.split('.')[0])
            parents[pid] = 1
            parents[pid + 1000] = pid
            if name in self.shared:
                shared[pid + 1000] = name
            elif name in shutdown.ORDER and name not in self.unknown:
                owned[pid + 1000] = name
        return sessions, parents, owned, shared

    def quit(self, sid):
        name = sid.split('.', 1)[1]
        self.events.append(('quit', name))
        if name != self.survivor:
            del self.names[name]


class FakeIO:
    def __init__(self, events, overrides=None):
        self.events = events
        self.overrides = overrides or {}

    async def caput(self, pv, value, **kwargs):
        self.events.append(('put', pv, value))

    async def caget(self, pv, **kwargs):
        self.events.append(('get', pv))
        if pv in self.overrides:
            value = self.overrides[pv]
            if isinstance(value, Exception):
                raise value
            return value
        if pv.endswith(':MW_RF_ERROR'):
            return ''
        if pv.endswith((':MW_RF_TIME', ':MW_RF_DONE_TIME')):
            return 1
        if pv.endswith((':MW_RF_VALID', ':MW_RF_READY', ':MW_RF_STATE', ':RFValid', ':RFState')):
            return 1
        if pv.endswith('_time'):
            return 2
        if pv.endswith((':VMEAS', ':VSET_RBV')):
            return 5
        if pv.endswith((':COMM', ':COMM_STATUS', ':MW_Connected', ':OUTPUT_RBV')):
            return 1
        return 0


async def no_sleep(_):
    pass


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def execute(self, runtime, overrides=None):
        io = FakeIO(runtime.events, overrides)

        async def safe(names, io):
            await shutdown.establish_safe(names, io, timeout=0.1, sleep=no_sleep, now=lambda: 1)

        await shutdown.run(runtime, io, safety=safe, sleep=no_sleep, cleanup_timeout=0)

    async def test_success_commands_precede_deterministic_cleanup(self):
        r = FakeRuntime()
        await self.execute(r)
        quits = [e[1] for e in r.events if e[0] == 'quit']
        self.assertEqual(quits, list(shutdown.ORDER))
        first_quit = next(i for i, e in enumerate(r.events) if e[0] == 'quit')
        safe_puts = [i for i, e in enumerate(r.events) if e[0] == 'put' and 'Archive' not in e[1]]
        self.assertEqual(len(safe_puts), 4)
        self.assertTrue(all(i < first_quit for i in safe_puts))
        archive_put = r.events.index(('put', 'UNH:GEN:Archive_Enable', 0))
        self.assertLess(archive_put, r.events.index(('quit', 'archiver')))
        self.assertEqual(r.names, {})

    async def test_failed_verification_retains_all_owners(self):
        r = FakeRuntime()
        async def failure(names, io):
            for pv in ('UNH:GEN:MW:SUP:RFEnable', 'UNH:GEN:MW:SRC:MW_RF'):
                await io.caput(pv, 0)
            raise RuntimeError('safety failed')
        with self.assertRaisesRegex(RuntimeError, 'safety failed'):
            await shutdown.run(r, FakeIO(r.events), safety=failure)
        self.assertFalse(any(e[0] == 'quit' for e in r.events))
        self.assertEqual(set(r.names), set(shutdown.ORDER))

    async def test_actual_rf_on_stale_poll_disconnected_or_test_mode_fail(self):
        for pv, value in [('UNH:GEN:MW:SRC:MW_RF_RBV', 1),
                          ('UNH:GEN:MW:SRC:MW_RF_VALID', 0),
                          ('UNH:GEN:MW:SRC:MW_RF_STATE', 0),
                          ('UNH:GEN:MW:SRC:MW_RF_TIME', 0),
                          ('UNH:GEN:MW:SRC:MW_RF_DONE_TIME', 0),
                          ('UNH:GEN:MW:SRC:MW_RF_READY', 0),
                          ('UNH:GEN:MW:SRC:MW_RF_ERROR', 'OFF failed'),
                          ('UNH:GEN:MW:SRC:MW_SweepActive', 1),
                          ('UNH:GEN:MW:SUP:RFValid', 0),
                          ('UNH:GEN:MW:SUP:RFState', 0),
                          ('UNH:GEN:MW:SUP:SweepState', 1),
                          ('UNH:GEN:MW:SRC:MW_RF_RBV', RuntimeError('unavailable')),
                          ('UNH:GEN:MW:SRC:MAN:vaunix_lms_time', 0),
                          ('UNH:GEN:MW:SRC:MW_Connected', 0),
                          ('UNH:GEN:MW:SRC:MW_TestMode', 1),
                          ('UNH:GEN:MW:PS:VMEAS', 4),
                          ('UNH:GEN:MW:ROBOT:X_BUSY', 1)]:
            r = FakeRuntime()
            ticks = iter([1] * 8 + [20] * 8)
            async def safe(names, io):
                await shutdown.establish_safe(names, io, sleep=no_sleep, now=lambda: next(ticks, 20))
            with self.assertRaisesRegex(RuntimeError, 'Safety verification failed'):
                await shutdown.run(r, FakeIO(r.events, {pv: value}), safety=safe)
            self.assertFalse(any(e[0] == 'quit' for e in r.events))

    async def test_legacy_and_request_state_are_not_rf_evidence(self):
        r = FakeRuntime()
        await self.execute(r, {'UNH:GEN:MW:SRC:MW_RF': 1,
                               'UNH:GEN:MW:SUP:RFEnable': 1})
        self.assertEqual(r.names, {})
        self.assertIn(('put', 'UNH:GEN:MW:SRC:MW_RF_CMD', 0), r.events)
        self.assertNotIn(('get', 'UNH:GEN:MW:SRC:MW_RF'), r.events)
        self.assertNotIn(('get', 'UNH:GEN:MW:SUP:RFEnable'), r.events)

    async def test_survivor_fails_and_keeps_dependencies(self):
        r = FakeRuntime(survivor='mw_supervisor')
        with self.assertRaisesRegex(RuntimeError, 'Shutdown survivor: mw_supervisor'):
            await self.execute(r)
        self.assertIn('mw_vaunix_lms', r.names)
        self.assertIn('ioc-manager', r.names)

    async def test_repeated_stop_issues_no_more_commands(self):
        r = FakeRuntime()
        await self.execute(r)
        count = len(r.events)
        await self.execute(r)
        self.assertEqual(len(r.events), count)

    async def test_partial_startup_uca_and_manager(self):
        r = FakeRuntime(('mw_spd1305x', 'ioc-manager'))
        await self.execute(r)
        puts = [e for e in r.events if e[0] == 'put']
        self.assertEqual(puts, [('put', 'UNH:GEN:MW:PS:VSET_CMD', 5.0)])
        self.assertEqual([e[1] for e in r.events if e[0] == 'quit'],
                         ['mw_spd1305x', 'ioc-manager'])

    async def test_shared_and_ssrf_sessions_untouched(self):
        extra = ('genesis-ssrf-smc', 'genesis-ssrf-archiver', 'genesis-ssrf-sequencer',
                 'genesis_ssrf_wgen', 'unrelated', 'mw_vaunix_lms-extra')
        r = FakeRuntime(shutdown.ORDER + extra, shared=('archiver', 'ioc-manager'))
        await self.execute(r)
        self.assertEqual(set(r.names), set(extra) | {'archiver', 'ioc-manager'})
        self.assertNotIn(('put', 'UNH:GEN:Archive_Enable', 0), r.events)

    async def test_unknown_physical_owner_aborts_before_writes(self):
        r = FakeRuntime(unknown=('mw_vaunix_lms',))
        with self.assertRaisesRegex(RuntimeError, 'Ownership not established'):
            await self.execute(r)
        self.assertEqual(r.events, [])

    async def test_archive_failure_retains_archive_and_manager(self):
        r = FakeRuntime(('archiver', 'ioc-manager'))
        async def failure(io):
            raise RuntimeError('files not closed')
        with self.assertRaisesRegex(RuntimeError, 'files not closed'):
            await shutdown.run(r, FakeIO(r.events), archive=failure)
        self.assertEqual(r.events, [])

    async def test_failed_command_still_attempts_remaining_safe_commands(self):
        events = []
        io = FakeIO(events)
        original = io.caput
        async def put(pv, value, **kwargs):
            await original(pv, value, **kwargs)
            if pv.endswith(':RFEnable'):
                raise RuntimeError('put failed')
        io.caput = put
        ticks = iter([1] * 8 + [20] * 8)
        with self.assertRaisesRegex(RuntimeError, 'command failures'):
            await shutdown.establish_safe(shutdown.ORDER, io, sleep=no_sleep, now=lambda: next(ticks, 20))
        self.assertEqual(len([e for e in events if e[0] == 'put']), 4)

    async def test_supervisor_without_source_owner_fails_before_commands(self):
        r = FakeRuntime(('mw_supervisor', 'ioc-manager'))
        with self.assertRaisesRegex(RuntimeError, 'source ownership'):
            await self.execute(r)
        self.assertEqual(r.events, [])

    async def test_gui_close_failure_still_commands_and_verifies_safety(self):
        r = FakeRuntime()
        async def fail():
            r.events.append(('gui', 'failed'))
            raise RuntimeError('GUI survived')
        r.close_gui = fail
        with self.assertRaisesRegex(RuntimeError, 'GUI survived.*safe state verified'):
            await self.execute(r)
        self.assertEqual(r.events[0], ('gui', 'failed'))
        self.assertEqual(len([e for e in r.events if e[0] == 'put']), 4)
        self.assertTrue(any(e[0] == 'get' for e in r.events))
        self.assertFalse(any(e[0] == 'quit' for e in r.events))

    async def test_gui_closed_before_safety_and_cleanup(self):
        r = FakeRuntime()
        async def close():
            r.events.append(('gui', 'closed'))
        r.close_gui = close
        await self.execute(r)
        self.assertEqual(r.events[0], ('gui', 'closed'))

    async def test_gui_failure_without_iocs_is_not_success(self):
        r = FakeRuntime(())
        async def fail():
            raise RuntimeError('GUI survived')
        r.close_gui = fail
        with self.assertRaisesRegex(RuntimeError, 'GUI survived'):
            await self.execute(r)

    async def test_dedicated_manager_and_archiver_are_terminated(self):
        for name in ('ioc-manager', 'archiver'):
            with self.subTest(name=name):
                r = FakeRuntime((name,))
                await self.execute(r)
                self.assertEqual([e for e in r.events if e[0] == 'quit'], [('quit', name)])
                self.assertEqual(r.names, {})

    async def test_only_known_shared_manager_or_archiver_succeeds(self):
        for name in ('ioc-manager', 'archiver'):
            with self.subTest(name=name):
                r = FakeRuntime((name,), shared=(name,))
                await self.execute(r)
                self.assertIn(name, r.names)
                self.assertEqual(r.events, [])

    async def test_unknown_manager_or_archiver_never_produces_success(self):
        for name in ('ioc-manager', 'archiver'):
            for devices in ((), ('mw_spd1305x',)):
                with self.subTest(name=name, devices=devices):
                    r = FakeRuntime(devices + (name,), unknown=(name,))
                    with self.assertRaisesRegex(RuntimeError, 'Ownership not established.*' + name):
                        await self.execute(r)
                    self.assertIn(name, r.names)
                    self.assertEqual(r.events, [])


class Detection(unittest.TestCase):
    def test_lsof_race_skips_only_exited_process(self):
        """No hidden owner accepted when ps PID dies before lsof."""
        outputs = [
            subprocess.CompletedProcess([], 1,
                'No Sockets found in /run/screen/S-test.\n', ''),
            subprocess.CompletedProcess([], 0,
                '424242 1 python master_ioc.py -s '
                'configs/commissioning/microwave_physical/pcl602 -i pcl602\n', ''),
            subprocess.CompletedProcess([], 1, '', ''),
        ]
        with patch.object(shutdown.subprocess, 'run', side_effect=outputs), \
                patch.object(shutdown.os, 'kill', side_effect=ProcessLookupError):
            sessions, parents, owned, shared = shutdown.LocalRuntime(ROOT).snapshot()
        self.assertEqual(sessions, {})
        self.assertEqual(owned, {})
        self.assertEqual(shared, {})
        self.assertIn(424242, parents)

    def test_lsof_failure_for_live_process_fails_closed(self):
        outputs = [
            subprocess.CompletedProcess([], 1,
                'No Sockets found in /run/screen/S-test.\n', ''),
            subprocess.CompletedProcess([], 0,
                '424242 1 python master_ioc.py -s '
                'configs/commissioning/microwave_physical/pcl602 -i pcl602\n', ''),
            subprocess.CompletedProcess([], 1, '', 'access denied'),
        ]
        with patch.object(shutdown.subprocess, 'run', side_effect=outputs), \
                patch.object(shutdown.os, 'kill', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Cannot establish cwd'):
                shutdown.LocalRuntime(ROOT).snapshot()

    def test_exact_names(self):
        sessions = shutdown.screen_sessions(' 12.mw_vaunix_lms-extra (Detached)\n 13.ioc-manager (Detached)\n')
        self.assertNotIn('mw_vaunix_lms', sessions)
        self.assertEqual(sessions['ioc-manager'], ['13.ioc-manager'])

    def test_screen_status_one_still_discovers_owned_session(self):
        outputs = [subprocess.CompletedProcess([], 1, ' 12.mw_spd1305x (Detached)\n1 Socket in /tmp/screen.\n', ''),
                   subprocess.CompletedProcess([], 0,
                       f'12 1 SCREEN\n13 12 python {ROOT}/master_ioc.py -s {ROOT}/{shutdown.PROFILE}/spd1305x -i spd1305x\n', '')]
        with patch.object(shutdown.subprocess, 'run', side_effect=outputs):
            selected = shutdown.ownership(shutdown.LocalRuntime(ROOT).snapshot())
        self.assertEqual(selected, {'mw_spd1305x': '12.mw_spd1305x'})

    def test_shell_screen_exists_with_nonzero_listing(self):
        text = (ROOT / 'scripts/microwave_physical.sh').read_text()
        helper = text[text.index('screen_exists()'):text.index('manager_put()')]
        with tempfile.TemporaryDirectory() as directory:
            screen = Path(directory) / 'screen'
            screen.write_text('#!/bin/sh\nprintf " 12.ioc-manager (Detached)\\n"\nexit 1\n')
            screen.chmod(0o755)
            result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + helper + '\nscreen_exists ioc-manager'],
                                    env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']})
            self.assertEqual(result.returncode, 0)

    def test_startup_duplicate_guard_with_nonzero_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts/start_microwave_system.sh'
            script.write_text((ROOT / 'scripts/start_microwave_system.sh').read_text())
            physical = root / 'scripts/microwave_physical.sh'
            physical.write_text('#!/bin/sh\nif [ "$1" = status ]; then exit 0; fi\necho UNEXPECTED_START\nexit 99\n')
            physical.chmod(0o755)
            screen = root / 'screen'
            screen.write_text('#!/bin/sh\nprintf " 12.mw_vaunix_lms (Detached)\\n"\nexit 1\n')
            screen.chmod(0o755)
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                                    env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']})
            self.assertEqual(result.returncode, 1)
            self.assertIn('Refusing to create duplicate', result.stdout)
            self.assertNotIn('UNEXPECTED_START', result.stdout)

    def test_duplicate_session_and_orphan_process_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            shutdown.ownership(({'mw_spd1305x': ['1.mw_spd1305x', '2.mw_spd1305x']}, {}, {}, {}))
        with self.assertRaisesRegex(RuntimeError, 'without a cleanup session'):
            shutdown.ownership(({}, {10: 1}, {10: 'mw_spd1305x'}, {}))

    def test_generic_profile_not_classified_as_owned(self):
        outputs = [subprocess.CompletedProcess([], 1, ' 12.ioc-manager (Detached)\n1 Socket in /tmp/screen.\n', ''),
                   subprocess.CompletedProcess([], 0,
                       f'12 1 SCREEN\n13 12 python {ROOT}/ioc_manager.py -s {ROOT}/configs/ioc_manager\n', '')]
        with patch.object(shutdown.subprocess, 'run', side_effect=outputs):
            self.assertEqual(shutdown.ownership(shutdown.LocalRuntime(ROOT).snapshot()), {})


    def test_generic_archiver_profile_is_positively_shared(self):
        outputs = [subprocess.CompletedProcess([], 1, ' 12.archiver (Detached)\n1 Socket in /tmp/screen.\n', ''),
                   subprocess.CompletedProcess([], 0,
                       f'12 1 SCREEN\n13 12 python {ROOT}/master_ioc.py -s {ROOT}/configs/archiver -i archiver\n', '')]
        with patch.object(shutdown.subprocess, 'run', side_effect=outputs) as run:
            snapshot = shutdown.LocalRuntime(ROOT).snapshot()
            self.assertEqual(snapshot[3], {13: 'archiver'})
            self.assertEqual(shutdown.ownership(snapshot), {})
            self.assertEqual(run.call_args_list[1].args[0],
                             ['ps', '-axww', '-o', 'pid=,ppid=,command='])

    def test_wrong_repository_or_profile_does_not_establish_shared_ownership(self):
        for script, folder in [(ROOT / 'ioc_manager.py', ROOT / 'configs/unknown'),
                               (ROOT / 'other/ioc_manager.py', ROOT / 'configs/ioc_manager')]:
            outputs = [subprocess.CompletedProcess([], 1, ' 12.ioc-manager (Detached)\n1 Socket in /tmp/screen.\n', ''),
                       subprocess.CompletedProcess([], 0, f'12 1 SCREEN\n13 12 python {script} -s {folder}\n', '')]
            with patch.object(shutdown.subprocess, 'run', side_effect=outputs):
                with self.assertRaisesRegex(RuntimeError, 'Ownership not established'):
                    shutdown.ownership(shutdown.LocalRuntime(ROOT).snapshot())

    def test_mixed_or_multiple_owner_provenance_is_ambiguous(self):
        for owned, shared in [({13: 'archiver'}, {14: 'archiver'}),
                              ({}, {13: 'archiver', 14: 'archiver'}),
                              ({13: 'archiver', 14: 'archiver'}, {})]:
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                shutdown.ownership(({'archiver': ['12.archiver']}, {12: 1, 13: 12, 14: 12}, owned, shared))

    def test_unknown_manager_and_archiver_cli_result_is_nonzero(self):
        import sys
        from types import SimpleNamespace
        for name in ('ioc-manager', 'archiver'):
            r = FakeRuntime((name,), unknown=(name,))
            with patch.dict(sys.modules, {'aioca': SimpleNamespace()}), \
                    patch.object(shutdown, 'LocalRuntime', return_value=r):
                self.assertEqual(shutdown.main(), 1)
            self.assertIn(name, r.names)
            self.assertEqual(r.events, [])

    def test_cli_failure_returns_nonzero(self):
        import sys
        from types import SimpleNamespace
        with patch.dict(sys.modules, {'aioca': SimpleNamespace()}), \
                patch.object(shutdown.LocalRuntime, 'snapshot', side_effect=RuntimeError('survivor')), \
                patch.object(shutdown.LocalRuntime, 'close_gui', new_callable=AsyncMock):
            self.assertEqual(shutdown.main(), 1)


if __name__ == '__main__':
    unittest.main()
