"""Test actual burn-recipe preparation without any EPICS or physical hardware."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ssrf_analysis import burn_recipe_ioc as recipe


class PV:
    def __init__(self, value=0):
        self.value = value

    def get(self):
        return self.value

    def set(self, value, **kwargs):
        self.value = value


class RecipeModeIntegration(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, mode, msg=None, age=float('inf'), polarization=.30):
        dev = recipe.Device.__new__(recipe.Device)
        dev.burn_mode = mode
        dev.operator_material = 'ND3'
        dev.operator_polarization = polarization
        dev.operator_center_mhz = None
        dev.max_fit_age_s = 60
        dev._generate_requested = True
        dev._generate_task = None
        dev._active_session_id = ''
        dev._emit = Mock()
        dev._latest_snapshot = lambda: (msg, age)
        dev._session_writer = Mock()
        dev._session_writer.write_products.return_value = SimpleNamespace(
            fit_data_path=None, waveform_data_path='mock-waveform.npz'
        )
        dev.pvs = {name: PV(value) for name, value in {
            'OBJECTIVE_RBV': 'Enhancement', 'CARRIER_MHZ': 32.677988031,
            'START_LEVEL_DBM': -120.0, 'TARGET_LEVEL_DBM': -22.0,
            'FM_DEV_KHZ': 10.0, 'FM_REPEATS': 10,
            'SESSION_ID': '', 'ARCHIVE_STATUS': '', 'STATE': '',
            'REASON': '', 'PLAN_VALID': 0,
            'MATERIAL': '', 'FIT_TYPE': '', 'FIT_TS': '',
            'CURRENT_POLARIZATION': 0.0, 'REFERENCE_POLARIZATION': 0.0,
            'FM_PERIOD_S': 0.0, 'SWEEP_STEP_DB': 0.0,
            'SWEEP_DWELL_MS': 0.0, 'WAVEFORM_POINTS': 0,
            'VLOW_V': 0.0, 'VHIGH_V': 0.0,
        }.items()}
        writes = []
        plans = []

        async def fake_caput(pv, value, timeout=5.0):
            writes.append((pv, value))

        async def fake_caget(pv, timeout=3.0):
            if pv.endswith(':WAVEFORM_STAGE_ID'):
                return 0
            if pv.endswith(':WAVEFORM_STAGE_POINTS'):
                return 8192
            raise AssertionError(f'unexpected PV read: {pv}')

        async def fake_wait_for(pv, predicate, **kwargs):
            assert predicate(1), f'bad wait predicate: {pv}'
            return 1

        class FakeStager:
            def __init__(self, write):
                assert write == fake_caput

            async def stage(self, plan):
                plan.validate()
                plans.append(plan)
                return SimpleNamespace(
                    pv='mock:WAVEFORM_CMD', points=8192,
                    minimum=float(plan.normalized_samples.min()),
                    maximum=float(plan.normalized_samples.max()),
                    material=plan.material, case=plan.case, fit_ts=plan.fit_ts,
                )

        dev._caput = fake_caput
        dev._caget = fake_caget
        dev._wait_for = fake_wait_for
        with patch.object(recipe, 'WaveformStager', FakeStager):
            await dev._generate_and_prepare()
        return dev, writes, plans

    async def test_operator_without_any_fit_prepares(self):
        dev, writes, plans = await self.prepare('operator')
        self.assertEqual(dev.pvs['STATE'].get(), 'READY_TO_ARM', dev.pvs['REASON'].get())
        self.assertEqual(dev.pvs['PLAN_VALID'].get(), 1)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].fit_type, 'OPERATOR_REFERENCE')
        self.assertTrue(any(pv.endswith(':PREPARE_CMD') for pv, _ in writes))

    async def test_operator_ignores_bad_fit(self):
        dev, writes, plans = await self.prepare(
            'operator', msg={'constants_good': False, 'P_fit': None}, age=999
        )
        self.assertEqual(dev.pvs['STATE'].get(), 'READY_TO_ARM', dev.pvs['REASON'].get())
        self.assertTrue(writes)
        self.assertEqual(len(plans), 1)

    async def test_fit_mode_blocks_missing_and_bad_fit(self):
        for msg, age in [(None, float('inf')),
                         ({'constants_good': False, 'P_fit': None}, 0)]:
            with self.subTest(msg=msg):
                dev, writes, plans = await self.prepare('fit', msg=msg, age=age)
                self.assertEqual(dev.pvs['STATE'].get(), 'FAILED')
                self.assertEqual(dev.pvs['PLAN_VALID'].get(), 0)
                self.assertEqual(writes, [])
                self.assertEqual(plans, [])

    async def test_invalid_reference_blocks_before_writes(self):
        dev, writes, plans = await self.prepare('operator', polarization=float('nan'))
        self.assertEqual(dev.pvs['STATE'].get(), 'FAILED')
        self.assertEqual(dev.pvs['PLAN_VALID'].get(), 0)
        self.assertEqual(writes, [])
        self.assertEqual(plans, [])


if __name__ == '__main__':
    unittest.main()
