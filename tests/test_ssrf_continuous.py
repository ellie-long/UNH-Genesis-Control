"""Offline continuous-mode state-machine tests; no instrument connections."""
import unittest
from unittest import mock
from ssrf_analysis import continuous_burn as cb

class FakeIO:
    def __init__(self, bad=False, failed_fit=False):
        self.failed_fit = failed_fit
        self.starts = 0
        self.read_state_count = 0
        self.v = {
            cb.SEQ+':EXECUTION_ENABLED': 1,
            cb.RECIPE+':BURN_MODE_RBV': 'OPERATOR',
            cb.RECIPE+':SESSION_ID': '',
            cb.RECIPE+':STATE': 'OPERATOR_MODE',
            cb.RECIPE+':REASON': '',
            cb.RECIPE+':PLAN_VALID': 0,
            cb.SEQ+':PREPARED': 0,
            cb.SEQ+':READY': 1,
            cb.SEQ+':EXECUTION_ARMED': 0,
            cb.SEQ+':EXECUTION_STATE': 'IDLE',
            cb.SEQ+':SAFE_OFF_ID': 0,
            'UNH:GEN:SSRF:GEN:RF_RBV': int(bad),
            'UNH:GEN:SSRF:GEN:FM_RBV': 0,
            'UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV': 0,
            'UNH:GEN:SSRF:WGEN:OUTPUT_RBV': 0,
            'UNH:GEN:SSRF:NMR_SW:MASK_RBV': 0,
            'UNH:GEN:SSRF:RF_SW:MASK_RBV': 3,
        }
    async def get(self, pv):
        if pv == cb.SEQ+':EXECUTION_STATE' and self.v[pv] == 'RUNNING':
            self.read_state_count += 1
            if self.read_state_count >= 2:
                self.v[pv] = 'COMPLETE'
                self.v[cb.SEQ+':PREPARED'] = 0
        return self.v[pv]
    async def put(self, pv, value):
        if pv == cb.RECIPE+':GENERATE_LOAD_CMD':
            if self.failed_fit:
                self.v[cb.RECIPE+':STATE'] = 'FAILED'
                self.v[cb.RECIPE+':REASON'] = 'no fitter result available'
            else:
                self.v[cb.RECIPE+':SESSION_ID'] = str(self.starts+1)
                self.v[cb.RECIPE+':STATE'] = 'READY_TO_ARM'
                self.v[cb.RECIPE+':PLAN_VALID'] = 1
                self.v[cb.SEQ+':PREPARED'] = 1
        elif pv == cb.SEQ+':ARM_CMD':
            self.v[cb.SEQ+':EXECUTION_ARMED'] = 1
        elif pv == cb.SEQ+':START_CMD':
            self.starts += 1
            self.read_state_count = 0
            self.v[cb.SEQ+':EXECUTION_ARMED'] = 0
            self.v[cb.SEQ+':EXECUTION_STATE'] = 'RUNNING'
        elif pv == cb.SEQ+':ABORT_CMD':
            self.v[cb.SEQ+':SAFE_OFF_ID'] += 1
            self.v[cb.SEQ+':EXECUTION_STATE'] = 'ABORTED'
        else:
            raise AssertionError('unexpected command '+pv)

class ContinuousTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_authorized_cycles_and_cleanup(self):
        io = FakeIO()
        with mock.patch.object(cb, '_read', io.get), mock.patch.object(cb, '_put', io.put):
            await cb.run(2,0,'operator')
        self.assertEqual(io.starts,2)
        self.assertEqual(io.v[cb.SEQ+':SAFE_OFF_ID'],1)
    async def test_unsafe_readback_prevents_start(self):
        io = FakeIO(bad=True)
        with mock.patch.object(cb, '_read',io.get),mock.patch.object(cb, '_put',io.put):
            with self.assertRaises(RuntimeError):
                await cb.run(1,0,'operator')
        self.assertEqual(io.starts,0)
    async def test_operator_stop_before_first_command(self):
        import asyncio
        io = FakeIO()
        stop = asyncio.Event()
        stop.set()
        with mock.patch.object(cb, '_read', io.get), mock.patch.object(cb, '_put', io.put):
            await cb.run(0,0,'operator',stop=stop)
        self.assertEqual(io.starts,0)
        self.assertEqual(io.v[cb.SEQ+':SAFE_OFF_ID'],1)

    async def test_bad_fit_stops_before_arm(self):
        io = FakeIO(failed_fit=True)
        with mock.patch.object(cb, '_read',io.get),mock.patch.object(cb, '_put',io.put):
            with self.assertRaisesRegex(RuntimeError,'no fitter result'):
                await cb.run(1,0,'operator')
        self.assertEqual(io.starts,0)

    async def test_stale_previous_complete_is_not_new_completion(self):
        io = FakeIO()
        stale_reads = 0

        async def delayed_get(pv):
            nonlocal stale_reads
            if (pv == cb.SEQ+':EXECUTION_STATE'
                    and io.v[pv] == 'COMPLETE'
                    and io.v[cb.SEQ+':PREPARED'] == 1):
                stale_reads += 1
                if stale_reads % 3 == 0:
                    io.v[pv] = 'RUNNING'
            return await io.get(pv)

        async def delayed_put(pv, value):
            if pv == cb.SEQ+':START_CMD':
                io.starts += 1
                io.read_state_count = 0
                io.v[cb.SEQ+':EXECUTION_ARMED'] = 0
                io.v[cb.SEQ+':EXECUTION_STATE'] = 'COMPLETE'  # stale readback
            else:
                await io.put(pv, value)

        with mock.patch.object(cb, '_read', delayed_get), mock.patch.object(cb, '_put', delayed_put):
            await cb.run(2, 0, 'operator')
        self.assertEqual(io.starts, 2)
        self.assertGreaterEqual(stale_reads, 3)
        self.assertEqual(io.v[cb.SEQ+':SAFE_OFF_ID'], 1)

if __name__ == '__main__':
    unittest.main()
