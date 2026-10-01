"""Cross-process ownership tests, using isolated temporary lock directories."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from logic_devices.ssrf_lifecycle import OwnershipLock, OwnershipConflict, startup_lifecycle_held
from logic_devices.ssrf_shutdown import establish_safe_shutdown
from logic_devices.ssrf_sequencer import Device
from genesis_common.execution_authorization import effective_os_user
from tests.test_ssrf_rf_operation import IO, Builder


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name

    def child(self, name, die=False):
        code = '''import os, sys
from logic_devices.ssrf_lifecycle import OwnershipLock
lock = OwnershipLock(sys.argv[1], sys.argv[2])
print("READY", flush=True)
if sys.argv[3] == "die": os._exit(0)
sys.stdin.readline()
'''
        p = subprocess.Popen([sys.executable, '-B', '-c', code, name, self.directory,
                              'die' if die else 'wait'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.close_child, p)
        self.assertEqual(p.stdout.readline().strip(), 'READY')
        return p

    @staticmethod
    def close_child(p):
        if p.poll() is None:
            p.stdin.write('\n'); p.stdin.flush()
        p.communicate(timeout=5)

    def physical_device(self):
        with patch('logic_devices.ssrf_sequencer.builder', Builder()), \
             patch.dict(os.environ, {}, clear=True):
            return Device('UNH:GEN:SSRF:SEQ', {
                'execution_enabled': True, 'execution_mode': 'physical',
                'required_os_user': effective_os_user(), 'ownership_lock_dir': self.directory,
            })

    async def test_cross_process_lifecycle_conflict_no_fallback_commands(self):
        self.child('lifecycle')
        io = IO()
        with self.assertRaises(OwnershipConflict):
            await establish_safe_shutdown(io, lock_directory=self.directory)
        self.assertEqual(io.writes, [])

    async def test_sequencer_owner_blocks_fallback_even_unreachable(self):
        p = self.child('control')
        io = IO()
        with self.assertRaises(OwnershipConflict):
            await establish_safe_shutdown(io, lock_directory=self.directory)
        self.assertEqual(io.writes, [])
        self.close_child(p)

    async def test_two_concurrent_fallback_attempts(self):
        first, second = IO(), IO()
        entered, release = asyncio.Event(), asyncio.Event()
        original = first.write
        async def blocked_write(pv, value):
            entered.set()
            await release.wait()
            await original(pv, value)
        first.write = blocked_write
        task = asyncio.create_task(establish_safe_shutdown(first, lock_directory=self.directory))
        await entered.wait()
        try:
            with self.assertRaises(OwnershipConflict):
                await establish_safe_shutdown(second, lock_directory=self.directory)
            self.assertEqual(second.writes, [])
            # Real Device constructor participates in lifecycle before control.
            with self.assertRaises(OwnershipConflict): self.physical_device()
        finally:
            release.set()
            await task
        self.assertEqual(len(first.writes), 6)

    async def test_physical_sequencer_lifetime_control_lock(self):
        device = self.physical_device()
        try:
            with self.assertRaises(OwnershipConflict): OwnershipLock('control', self.directory)
            io = IO()
            with self.assertRaises(OwnershipConflict):
                await establish_safe_shutdown(io, lock_directory=self.directory)
            self.assertEqual(io.writes, [])
        finally:
            device._control_lock.close()
        with OwnershipLock('control', self.directory): pass

    async def test_failure_releases_both_locks(self):
        io = IO()
        io.fail.add('UNH:GEN:SSRF:WGEN:OUTPUT_RBV')
        with self.assertRaises(RuntimeError):
            await establish_safe_shutdown(io, lock_directory=self.directory)
        with OwnershipLock('lifecycle', self.directory), OwnershipLock('control', self.directory): pass

    async def test_dead_owner_does_not_wedge_lock(self):
        p = self.child('control', die=True)
        p.wait(timeout=5)
        self.assertTrue((Path(self.directory)/'control.lock').exists())
        with OwnershipLock('control', self.directory): pass

    async def test_healthy_sequencer_preferred_while_control_locked(self):
        self.child('control')
        io = IO()
        seq = 'UNH:GEN:SSRF:SEQ'
        io.values.update({f'{seq}:EXECUTION_ENABLED': 1, f'{seq}:SAFE_OFF_ID': 1,
                          f'{seq}:MANUAL_STATE': 'DISARMED',
                          f'{seq}:MANUAL_REASON': 'Safe baseline verified'})
        async def abort(): io.values[f'{seq}:SAFE_OFF_ID'] = 2
        await establish_safe_shutdown(io, request_abort=abort, lock_directory=self.directory)
        self.assertEqual(io.writes, [])

    async def test_startup_lease_live_then_stale_inherited_environment(self):
        with OwnershipLock('lifecycle', self.directory) as parent:
            token = parent.token
            with patch.dict(os.environ, {'GENESIS_SSRF_LIFECYCLE_TOKEN': token}):
                self.assertTrue(startup_lifecycle_held(self.directory))
                with patch('logic_devices.ssrf_sequencer.builder', Builder()):
                    device = Device('UNH:GEN:SSRF:SEQ', {
                        'execution_enabled': True, 'execution_mode': 'physical',
                        'required_os_user': effective_os_user(), 'ownership_lock_dir': self.directory,
                    })
                device._control_lock.close()
        with patch.dict(os.environ, {'GENESIS_SSRF_LIFECYCLE_TOKEN': token}):
            self.assertFalse(startup_lifecycle_held(self.directory))
            with OwnershipLock('lifecycle', self.directory):
                self.assertFalse(startup_lifecycle_held(self.directory))
                with patch('logic_devices.ssrf_sequencer.builder', Builder()):
                    with self.assertRaises(OwnershipConflict):
                        Device('UNH:GEN:SSRF:SEQ', {
                            'execution_enabled': True, 'execution_mode': 'physical',
                            'required_os_user': effective_os_user(), 'ownership_lock_dir': self.directory,
                        })
