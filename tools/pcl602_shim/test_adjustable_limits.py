"""Machine limits, user-zero isolation, persistence, and IOC calibration commands."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from controller import Axis
from pcl602_shim import Robot, Simulator, Refused
from test_pcl602 import Device

CONFIG = json.loads(Path(__file__).with_name('config.json').read_text())

class AdjustableLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'config.limits.json'
        self.raw = Simulator()
        self.robot = Robot(self.raw, copy.deepcopy(CONFIG), limits_path=self.path)
        self.owner = object(); self.robot.claim(self.owner)
    def call(self, op, **kw):
        return self.robot.dispatch(self.owner, dict(op=op, **kw))
    def limits(self, axis='X', low=-10, high=110, enabled=True):
        return self.call('SET_LIMITS', axis=axis, low_cm=low, high_cm=high, enabled=enabled)
    def machine(self, axis, cm):
        return self.call('SET_MACHINE_POSITION', axis=axis, value_cm=cm)
    def finish(self):
        for a in Axis: self.raw.pos[a] = self.raw.targets[a]
    def device(self):
        robot, owner = self.robot, self.owner
        class Peer:
            def request(self, op, **kw): return robot.dispatch(owner, dict(op=op, **kw))
        d=Device('TEST',dict(channels=['X','Y'],require_speed_profiles=True));d.t=Peer()
        d._apply(self.call('READ'))
        return d
    def test_negative_limits_and_sign_conversion(self):
        for a in ('X','Y'): self.limits(a, -5, 10)
        d=self.device()
        for a in ('X','Y'):
            self.assertEqual(d.pvs[a+'_LOW_CM'].value,-5)
            self.assertEqual(d.pvs[a+'_HIGH_CM'].value,10)
        self.assertEqual(self.robot.axes[Axis.Y].minimum,-3180000)
        self.assertEqual(self.robot.axes[Axis.Y].maximum,1590000)
    def test_disabled_limits_allow_epics_beyond_both_default_envelopes(self):
        for a in ('X','Y'): self.limits(a,0,1,False)
        self.call('MODE',value=1)
        for a,n in [('X',-31800),('Y',318000)]:
            self.call('MOVE_REL',axis=a,value=n);self.finish()
            self.assertEqual(self.call('READ')['axes'][a]['position'],n)
    def test_reenable_restores_enforcement_and_allows_gradual_recovery(self):
        self.machine('X',-5);self.limits('X',0,10,True);self.call('MODE',value=1)
        with self.assertRaises(Refused):self.call('MOVE_REL',axis='X',value=-31800)
        self.call('MOVE_REL',axis='X',value=31800);self.finish()
        self.assertEqual(self.call('READ')['axes']['X']['position'],-4*31800)
    def test_apply_busy_or_enabled_rejected(self):
        self.call('MODE',value=1)
        with self.assertRaises(Refused):self.limits()
        self.call('STOP_ALL');self.raw.move_relative(Axis.Y,-318000)
        with self.assertRaises(Refused):self.limits()
        with self.assertRaises(Refused):self.machine('X',1)
        self.assertFalse(self.path.exists())
    def test_invalid_requests_preserve_settings_and_health(self):
        for low, high, en in [(1,1,True),(5,1,True),(float('nan'),10,True),(-1,float('inf'),True),(-1e308,1,True),(-1,1,1),(-1,1,'false')]:
            with self.assertRaises(Refused):self.limits(low=low,high=high,enabled=en)
            self.assertEqual(self.robot.axes[Axis.X].minimum,0)
            self.assertTrue(self.call('READ')['healthy'])
        self.assertFalse(self.path.exists())
    def test_limits_persist_independently_per_axis(self):
        self.limits('X',-12,120,False);self.limits('Y',-3,65,True)
        restarted=Robot(self.raw,copy.deepcopy(CONFIG),limits_path=self.path)
        self.assertFalse(restarted.axes[Axis.X].limits_enabled)
        self.assertEqual(restarted.axes[Axis.X].minimum,-12*31800)
        self.assertEqual(restarted.axes[Axis.Y].minimum,-65*318000)
    def test_disk_failure_does_not_apply_change_or_latch_usb_fault(self):
        with patch('limit_settings.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(Refused):self.limits(enabled=False)
        self.assertTrue(self.robot.axes[Axis.X].limits_enabled)
        self.assertTrue(self.robot.controller.axes['X']['limits_enabled'])
        self.assertTrue(self.call('READ')['healthy'])
        self.assertEqual(list(self.path.parent.iterdir()),[])
    def test_corrupt_saved_settings_are_not_silently_ignored(self):
        self.path.write_text('{bad')
        with self.assertRaises(ValueError):Robot(self.raw,copy.deepcopy(CONFIG),limits_path=self.path)
    def test_changed_scale_does_not_reinterpret_saved_limits(self):
        self.limits();cfg=copy.deepcopy(CONFIG);cfg['axes']['X']['steps_per_cm_256']=10000
        with self.assertRaises(ValueError):Robot(self.raw,cfg,limits_path=self.path)
    def test_user_zero_does_not_rewrite_raw_machine_position_or_limits(self):
        self.machine('X',50);before=self.raw.position(Axis.X)
        self.call('ZERO',axis='X');d=self.device()
        self.assertEqual(d.pvs['X_CM'].value,0)
        self.assertEqual(d.pvs['X_MACHINE_CM'].value,50)
        self.assertEqual(d.pvs['X_LOW_CM'].value,0)
        self.assertEqual(d.pvs['X_HIGH_CM'].value,103)
        self.assertEqual(self.raw.position(Axis.X),before)
        self.call('MODE',value=1);self.call('MOVE_REL',axis='X',value=-31800);self.finish()
        d._apply(self.call('READ'));self.assertEqual(d.pvs['X_CM'].value,-1)
        self.assertEqual(d.pvs['X_MACHINE_CM'].value,49)
    def test_machine_reference_and_work_zero_survive_speed_changes(self):
        self.machine('Y',20);self.call('ZERO',axis='Y')
        for mode in (1,2,0,1):
            self.call('SPEED_MODE',value=mode);d=self.device()
            self.assertEqual(d.pvs['Y_CM'].value,0)
            self.assertEqual(d.pvs['Y_MACHINE_CM'].value,20)
    def test_disabled_joystick_uses_bounded_segments_and_deadman(self):
        self.limits('X',0,1,False)
        self.robot.joystick_command({},False);self.call('MODE',value=2)
        self.robot.joystick_command({},False)
        self.robot.joystick_command({Axis.X:-1},True)
        self.assertEqual(self.raw.targets[Axis.X],-8388607)
        self.robot.joystick_command({},False)
        self.assertFalse(self.raw.busy(Axis.X))
    def test_limit_changes_apply_in_all_profiles(self):
        self.limits('X',-1,1)
        for mode in (0,1,2):
            self.call('SPEED_MODE',value=mode);self.call('MODE',value=1)
            with self.assertRaises(Refused):self.call('MOVE_REL',axis='X',value=31804)
            self.call('MOVE_REL',axis='X',value=-31800);self.finish()
            self.call('STOP_ALL');self.machine('X',0)
    def test_numerical_guard_remains_with_limits_disabled(self):
        self.limits(enabled=False)
        self.robot.controller.position_anchor[Axis.X]=2147483640
        self.call('MODE',value=1)
        with self.assertRaises(Refused):self.call('MOVE_REL',axis='X',value=100)
    def test_ioc_draft_edits_do_not_change_active_limits(self):
        d=self.device()
        for suffix,value in [('LIMIT_LOW_SET_CM',-10),('LIMIT_HIGH_SET_CM',105),('LIMIT_ENABLE_SET',0)]:
            d.do_sets(value,'TEST:X_'+suffix)
        self.assertTrue(self.robot.axes[Axis.X].limits_enabled)
        d._apply(self.call('READ'))
        self.assertEqual(d.pvs['X_LIMIT_LOW_SET_CM'].value,-10)
        d.do_sets(1,'TEST:X_LIMIT_APPLY')
        self.assertEqual(d.last_error,'')
        self.assertFalse(self.robot.axes[Axis.X].limits_enabled)
        self.assertEqual(d.pvs['X_LOW_CM'].value,-10)
    def test_ioc_rejected_apply_does_not_change_readbacks(self):
        d=self.device();d.do_sets(500,'TEST:X_LIMIT_LOW_SET_CM');d.do_sets(1,'TEST:X_LIMIT_APPLY')
        self.assertIn('lower limit',d.last_error)
        self.assertEqual(d.pvs['X_LOW_CM'].value,0)
        d.do_sets(1,'TEST:X_LIMIT_RELOAD')
        self.assertEqual(d.pvs['X_LIMIT_LOW_SET_CM'].value,0)
    def test_ioc_machine_entry_requires_explicit_apply(self):
        d=self.device();d.do_sets(25,'TEST:Y_MACHINE_SET_CM')
        self.assertEqual(d.pvs['Y_MACHINE_CM'].value,0)
        d.do_sets(1,'TEST:Y_MACHINE_APPLY')
        self.assertEqual(d.last_error,'');self.assertEqual(d.pvs['Y_MACHINE_CM'].value,25)
        self.assertTrue(d.pvs['Y_REFERENCED'].value)
    def test_invalid_ioc_entry_reverts_to_previous_draft(self):
        d=self.device();d.do_sets(-5,'TEST:X_LIMIT_LOW_SET_CM')
        d.do_sets(float('nan'),'TEST:X_LIMIT_LOW_SET_CM')
        self.assertEqual(d.pvs['X_LIMIT_LOW_SET_CM'].value,-5)
        self.assertEqual(d.limit_drafts['X']['low_cm'],-5)
    def test_disabled_one_axis_does_not_disable_other_axis(self):
        self.limits('X',0,1,False);self.call('MODE',value=1)
        with self.assertRaises(Refused):self.call('MOVE_REL',axis='Y',value=100)
        self.call('MOVE_REL',axis='X',value=-100);self.finish()
    def test_hardware_pulse_range_still_applies_with_limits_disabled(self):
        self.limits('Y',0,60,False);self.call('MODE',value=1)
        with self.assertRaises(Refused):self.call('MOVE_REL',axis='Y',value=8388608)
        self.assertEqual(self.raw.targets[Axis.Y],0)
    def test_unknown_reference_does_not_lock_out_modes(self):
        self.assertFalse(self.robot.controller.referenced[Axis.X])
        self.call('MODE',value=1)
        self.assertEqual(self.robot.motion.mode,1)

if __name__=='__main__':unittest.main()
