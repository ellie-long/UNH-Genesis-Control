"""V2 mode safety tests; no hardware or network needed."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from pcl602_shim import Robot, Simulator, Refused
from controller import Axis

class ModeCurrentTests(unittest.TestCase):
    def setUp(self):
        self.c = Simulator()
        config = json.loads(Path(__file__).with_name('config.json').read_text())
        config.pop('motion_profiles', None)
        for axis in ('X','Y'): config['axes'][axis].update(minimum=-8388607, maximum=8388607)
        self.r = Robot(self.c, config)
        self.owner = object()
        self.r.claim(self.owner)
        for a in Axis: self.c.set_motor_enabled(a, False)
        self.r.snapshot()
    def request(self, op, **fields):
        return self.r.dispatch(self.owner, dict(op=op, **fields))
    def test_both_modes_enable_currents_before_permission(self):
        for mode in (1, 2):
            self.request('MODE', value=0)
            self.r.joystick_command({}, False)
            for a in Axis: self.c.set_motor_enabled(a, False)
            original = self.c.set_motor_enabled
            def enable(a, value):
                self.assertEqual(self.r.motion.mode, 0)
                self.assertFalse(self.r.motion.joystick_armed)
                original(a, value)
            with patch.object(self.c, 'set_motor_enabled', side_effect=enable):
                self.request('MODE', value=mode)
            state = self.request('READ')
            self.assertEqual(state['mode'], mode)
            self.assertTrue(all(a['motor_enabled'] for a in state['axes'].values()))
            self.assertFalse(self.r.motion.joystick_armed)
    def test_missing_joystick_does_not_energize(self):
        with self.assertRaises(Refused): self.request('MODE', value=2)
        self.assertEqual(self.r.motion.mode, 0)
        self.assertFalse(any(self.c.enabled.values()))
    def test_idle_check_precedes_enable(self):
        with patch.object(self.c, 'busy', return_value=True):
            with self.assertRaisesRegex(Refused, 'still stopping'): self.request('MODE', value=1)
        self.assertEqual(self.r.motion.mode, 0)
        self.assertFalse(any(self.c.enabled.values()))
    def test_failed_readback_disables_preserves_partial_current(self):
        original = self.c.motor_enabled
        with patch.object(self.c, 'motor_enabled', side_effect=lambda a: False if a==Axis.Y else original(a)):
            with self.assertRaisesRegex(Refused, 'did not enable'): self.request('MODE', value=1)
        self.assertEqual(self.r.motion.mode, 0)
        self.assertTrue(self.c.motor_enabled(Axis.X))
        self.assertFalse(self.r.motion.joystick_armed)
        with self.assertRaises(Refused): self.request('MOVE_REL', axis='X', value=10)
    def test_usb_failure_latches_fault(self):
        original=self.c.set_motor_enabled
        def enable(a,value):
            if a==Axis.Y: raise OSError('USB failure')
            original(a,value)
        with patch.object(self.c,'set_motor_enabled',side_effect=enable):
            with self.assertRaises(Refused): self.request('MODE',value=1)
        self.assertEqual(self.r.motion.mode,0)
        self.assertTrue(self.r.fault)
        self.assertTrue(self.c.motor_enabled(Axis.X))
        with self.assertRaises(Refused): self.request('MODE',value=1)
    def test_stop_failure_prevents_enable(self):
        with patch.object(self.c,'emergency_stop',side_effect=OSError('stop failed')):
            with self.assertRaises(Refused): self.request('MODE',value=1)
        self.assertEqual(self.r.motion.mode,0)
        self.assertFalse(any(self.c.enabled.values()))
    def test_stop_all_stops_both_disables_retains_current(self):
        self.request('MODE',value=1)
        for a in Axis: self.request('MOVE_REL',axis=a.value,value=100000)
        self.request('STOP_ALL')
        self.assertEqual(self.r.motion.mode,0)
        self.assertFalse(any(self.c.busy(a) for a in Axis))
        self.assertTrue(all(self.c.motor_enabled(a) for a in Axis))
    def test_disabled_and_repeat_mode_preserve_behavior(self):
        self.request('MODE',value=0)
        self.assertFalse(any(self.c.enabled.values()))
        self.request('MODE',value=1)
        self.request('MOVE_REL',axis='X',value=100000)
        with patch.object(self.c,'set_motor_enabled') as enable:
            self.request('MODE',value=1)
            enable.assert_not_called()
        self.assertTrue(self.c.busy(Axis.X))

if __name__=='__main__': unittest.main()
