"""Offline GUI START/STOP integration; does not touch EPICS or hardware."""
import asyncio
import unittest
from unittest.mock import patch
from ssrf_analysis import burn_recipe_ioc as recipe
from ssrf_analysis import continuous_burn


class PV:
    def __init__(self, value=0):
        self.value = value

    def get(self):
        return self.value

    def set(self, value, **kwargs):
        self.value = value


def device(cycles=2, delay=0.01):
    d = recipe.Device.__new__(recipe.Device)
    d.burn_mode = 'operator'
    d._continuous_requested = False
    d._continuous_stop_requested = False
    d._continuous_stop = None
    d._continuous_task = None
    d._generate_requested = False
    d._generate_task = None
    d._active_session_id = ''
    d._latest_snapshot = lambda: (None, float('inf'))
    d.pvs = {key: PV(value) for key, value in {
        'CONT_CYCLES': cycles, 'CONT_DELAY_S': delay,
        'CONT_START_CMD': 0, 'CONT_STOP_CMD': 0,
        'CONT_CYCLE': 0, 'CONT_ACTIVE': 0,
        'CONT_STATE': 'IDLE', 'CONT_REASON': '',
        'FIT_AGE_S': 0, 'LATEST_FIT_VALID': 0,
    }.items()}
    return d


class ContinuousGui(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_uses_existing_runner(self):
        d = device()
        phases = []

        async def fake_run(cycles, delay, expected_mode, stop, progress):
            self.assertEqual((cycles, delay, expected_mode), (2, .01, 'operator'))
            progress(1, 'PREPARING')
            phases.append('PREPARING')
            await asyncio.wait_for(stop.wait(), 1.0)
            progress(1, 'SAFE_OFF')
            phases.append('SAFE_OFF')

        with patch.object(recipe, 'run_continuous_burn', fake_run):
            d._continuous_start_command(1, None)
            self.assertTrue(d._continuous_requested)
            await d.do_reads()
            await asyncio.sleep(0)
            self.assertEqual(d.pvs['CONT_ACTIVE'].get(), 1)
            d._continuous_stop_command(1, None)
            await d.do_reads()
            await asyncio.wait_for(d._continuous_task, 1.0)
            self.assertEqual(d.pvs['CONT_STATE'].get(), 'STOPPED')
            self.assertEqual(d.pvs['CONT_CYCLE'].get(), 1)
            self.assertEqual(d.pvs['CONT_ACTIVE'].get(), 0)
            self.assertEqual(phases, ['PREPARING', 'SAFE_OFF'])

    async def test_invalid_input_refuses_start(self):
        for cycles, delay in ((-1, 1), (2, float('nan')), (2, -1), (100001, 1)):
            with self.subTest(cycles=cycles, delay=delay):
                d = device(cycles, delay)
                d._continuous_start_command(1, None)
                self.assertFalse(d._continuous_requested)
                self.assertEqual(d.pvs['CONT_STATE'].get(), 'INVALID')

    async def test_duplicate_start_rejected(self):
        d = device()
        d._continuous_start_command(1, None)
        d._continuous_start_command(1, None)
        self.assertTrue(d._continuous_requested)
        self.assertIn('active', d.pvs['CONT_REASON'].get())

    async def test_runner_failure_visible(self):
        d = device()

        async def failed_run(**kwargs):
            raise RuntimeError('Safety baseline mismatch')

        with patch.object(recipe, 'run_continuous_burn', failed_run):
            d._continuous_start_command(1, None)
            await d.do_reads()
            await asyncio.wait_for(d._continuous_task, 1.0)
            self.assertEqual(d.pvs['CONT_STATE'].get(), 'FAILED')
            self.assertIn('Safety baseline mismatch', d.pvs['CONT_REASON'].get())
            self.assertEqual(d.pvs['CONT_ACTIVE'].get(), 0)

    async def test_prepared_one_shot_not_taken_over(self):
        writes = []

        async def fake_read(pv):
            if pv.endswith(':EXECUTION_ENABLED'):
                return 1
            if pv.endswith(':BURN_MODE_RBV'):
                return 'OPERATOR'
            if pv.endswith(':PREPARED'):
                return 1
            raise AssertionError(pv)

        async def fake_safe():
            return None

        async def fake_put(pv, value):
            writes.append((pv, value))

        with patch.object(continuous_burn, '_read', fake_read), \
             patch.object(continuous_burn, '_safe_baseline', fake_safe), \
             patch.object(continuous_burn, '_put', fake_put):
            with self.assertRaisesRegex(RuntimeError, 'manual burn already prepared'):
                await continuous_burn.run(1, .01, 'operator')
        self.assertEqual(writes, [])


if __name__ == '__main__':
    unittest.main()
