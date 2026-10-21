"""Offline waveform and mode tests; no IOC and no RF hardware needed."""
import unittest
import numpy as np
from ssrf_analysis.operator_waveform import build_operator_waveform_plan
from ssrf_analysis.waveform_planner import WaveformPlanError

class TestOperatorWaveform(unittest.TestCase):
    def test_chhetra_voltage_contract(self):
        plan = build_operator_waveform_plan(material='ND3',case='Enhancement')
        self.assertEqual(plan.vhigh_v, 0.5)
        self.assertEqual(plan.vlow_v, -0.5)
    def test_reference_plan_both_materials(self):
        for material, center in [('Butanol',31.694211),('ND3',32.677988031)]:
            for case in ('Enhancement','Suppression','Zero'):
                with self.subTest(material=material, case=case):
                    p = build_operator_waveform_plan(material=material, case=case,
                                                     center_mhz=center)
                    p.validate()
                    self.assertEqual(len(p.normalized_samples),8192)
                    self.assertEqual(len(p.f_peak_mhz),41)
                    self.assertEqual(len(p.f_shoulder_mhz),41)
                    self.assertAlmostEqual(p.period_s,0.01)
                    self.assertTrue(np.isfinite(p.normalized_samples).all())
                    self.assertTrue(np.max(np.abs(p.normalized_samples)) <= 1.0)
                    self.assertFalse(p.used_measured_heights)
                    self.assertEqual(p.fit_type,'OPERATOR_REFERENCE')
    def test_invalid_reference_rejected(self):
        for p in (float('nan'), float('inf'), 2.0):
            with self.assertRaises(WaveformPlanError):
                build_operator_waveform_plan(material='ND3',case='Enhancement',polarization=p)
        with self.assertRaises(WaveformPlanError):
            build_operator_waveform_plan(material='ND3',case='Enhancement',center_mhz=float('nan'))
        with self.assertRaises(WaveformPlanError):
            build_operator_waveform_plan(material='water',case='Enhancement')

if __name__ == '__main__':
    unittest.main()
