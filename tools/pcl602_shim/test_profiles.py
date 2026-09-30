"""Profile, canonical-position and physical-coordinate safety regressions."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from controller import Axis
from pcl602_shim import Robot, Simulator, Refused
from test_pcl602 import Device

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads(Path(__file__).with_name('config.json').read_text())
        self.raw=Simulator();self.robot=Robot(self.raw,copy.deepcopy(self.config))
        self.owner=object();self.robot.claim(self.owner)
    def call(self, op, **kw):return self.robot.dispatch(self.owner,dict(op=op,**kw))
    def reference(self):
        for a in Axis:self.call('SET_MACHINE_POSITION',axis=a.value,value_cm=0)
    def finish(self):
        for a in Axis:self.raw.pos[a]=self.raw.targets[a]
    def test_startup_allows_epics_motion_without_zero(self):
        self.assertFalse(self.call('READ')['axes']['X']['referenced'])
        self.call('MODE',value=1)
        self.call('MOVE_REL',axis='X',value=1000)
        self.assertGreater(self.raw.targets[Axis.X],0)
        self.call('STOP_ALL')
    def test_physical_speeds_linear_and_acceleration_constant(self):
        for i,expected in enumerate((.15,.30,.45)):
            self.call('SPEED_MODE',value=i)
            state=self.call('READ')
            for a in Axis:
                scale=self.config['axes'][a.value]['steps_per_cm_256']
                factor=self.robot.controller.factor[a]
                self.assertAlmostEqual(state['axes'][a.value]['speed_cm_s'],expected,delta=factor/scale/2 + 1e-12)
                self.assertAlmostEqual(self.raw.accel[a]*factor/scale,.1)
                self.assertAlmostEqual(self.raw.base[a]*factor/scale,.004,delta=factor/scale/2 + 1e-12)
                self.assertTrue(1<=self.raw.base[a]<self.raw.speed[a]<=50000)
    def test_nonzero_odd_position_survives_repeated_switches(self):
        self.reference();self.call('MODE',value=1)
        self.call('MOVE_REL',axis='X',value=10003);self.finish()
        self.call('MOVE_REL',axis='Y',value=-100003);self.finish()
        for index in (1,2,0,2,1,0):
            self.call('SPEED_MODE',value=index)
            s=self.call('READ')
            self.assertEqual(s['axes']['X']['position'],10003)
            self.assertEqual(s['axes']['Y']['position'],-100003)
            self.assertEqual(s['axes']['X']['maximum'],3275400)
            self.assertEqual(s['axes']['Y']['minimum'],-19080000)
    def test_coarse_moves_count_four_fine_steps_per_pulse(self):
        self.reference();self.call('SPEED_MODE',value=1);self.call('MODE',value=1)
        self.call('MOVE_REL',axis='X',value=10000)
        self.assertEqual(self.raw.targets[Axis.X],2500)
        self.finish();self.assertEqual(self.call('READ')['axes']['X']['position'],10000)
        self.call('SPEED_MODE',value=0)
        self.assertEqual(self.call('READ')['axes']['X']['position'],10000)
    def test_travel_bounds_apply_in_every_profile(self):
        self.reference()
        for i in range(3):
            self.call('SPEED_MODE',value=i);self.call('MODE',value=1)
            for a,steps in [('X',-1),('X',3275401),('Y',1),('Y',-19080001)]:
                with self.assertRaises(Refused):self.call('MOVE_REL',axis=a,value=steps)
    def test_busy_change_rejected_without_stopping_or_changing_profile(self):
        self.reference();self.call('MODE',value=1);self.call('MOVE_REL',axis='X',value=50000)
        with self.assertRaises(Refused):self.call('SPEED_MODE',value=1)
        self.assertEqual(self.robot.controller.profile,0)
        self.assertTrue(self.raw.busy(Axis.X))
    def test_profile_disarms_joystick_and_preserves_currents(self):
        self.reference();self.robot.joystick_command({},False);self.call('MODE',value=2)
        self.robot.joystick_command({},False)
        self.call('SPEED_MODE',value=2)
        self.assertEqual(self.robot.motion.mode,0)
        self.assertFalse(self.robot.motion.joystick_armed)
        self.assertTrue(all(self.raw.enabled.values()))
    def test_joystick_direction_unchanged_without_zero(self):
        self.robot.joystick_command({},False);self.call('MODE',value=2)
        self.robot.joystick_command({},False)
        self.robot.joystick_command({Axis.Y:-1},True)
        self.assertLess(self.raw.targets[Axis.Y],0)
    def test_failed_output_verification_latches_fault(self):
        with patch.object(self.raw,'set_output'):
            with self.assertRaises(Refused):self.call('SPEED_MODE',value=1)
        self.assertTrue(self.robot.fault);self.assertEqual(self.robot.motion.mode,0)
        with self.assertRaises(Refused):self.call('MODE',value=1)
    def test_outside_resolution_change_detected_even_when_busy(self):
        self.reference();self.call('MODE',value=1);self.call('MOVE_REL',axis='X',value=50000)
        self.raw.set_output(Axis.X,2,True)
        with self.assertRaises(Refused):self.call('READ')
        self.assertTrue(self.robot.fault);self.assertFalse(self.raw.busy(Axis.X))
    def test_other_output_bits_preserved(self):
        self.raw.output_register[Axis.X]=128
        self.call('SPEED_MODE',value=1)
        self.assertEqual(self.raw.outputs(Axis.X),130)
        self.call('SPEED_MODE',value=0)
        self.assertEqual(self.raw.outputs(Axis.X),128)
    def test_ioc_calibration_and_arrow_sign(self):
        self.reference()
        robot=self.robot;owner=self.owner
        class Peer:
            def request(self,op,**kw):return robot.dispatch(owner,dict(op=op,**kw))
        d=Device('TEST',dict(channels=['X','Y'],require_speed_profiles=True));d.t=Peer()
        d._apply(self.call('READ'));d.do_sets(1,'TEST:CONTROL_MODE')
        d.do_sets(1,'TEST:JOG_DISTANCE_CM');d.do_sets(1,'TEST:Y_JOG')
        self.assertEqual(self.raw.targets[Axis.Y],-318000)
        self.finish();d._apply(self.call('READ'))
        self.assertEqual(d.pvs['Y_CM'].value,1)
        self.assertEqual(d.pvs['Y_LOW_CM'].value,0)
        self.assertEqual(d.pvs['Y_HIGH_CM'].value,60)
        self.assertEqual(d.pvs['Y_STEPS_PER_CM'].value,10*d.pvs['X_STEPS_PER_CM'].value)
    def test_counter_rebase_retains_physical_position(self):
        self.reference();c=self.robot.controller
        self.raw.set_position(Axis.X,8388600);c.raw_anchor[Axis.X]=8388600
        self.call('MODE',value=1);self.call('MOVE_REL',axis='X',value=10000);self.finish()
        self.assertEqual(self.call('READ')['axes']['X']['position'],10000)
    def test_restart_invalidates_references(self):
        self.reference();self.call('SPEED_MODE',value=1)
        restarted=Robot(self.raw,copy.deepcopy(self.config))
        self.assertFalse(any(restarted.controller.referenced.values()))
    def test_full_y_travel_at_coarse_resolution(self):
        self.reference();self.call('SPEED_MODE',value=1);self.call('MODE',value=1)
        self.call('MOVE_REL',axis='Y',value=-19080000)
        self.assertEqual(self.raw.targets[Axis.Y],-4770000)
        self.finish()
        self.assertEqual(self.call('READ')['axes']['Y']['position'],-19080000)
        self.call('SPEED_MODE',value=0)
        self.assertEqual(self.call('READ')['axes']['Y']['position'],-19080000)

    def test_full_y_joystick_travel_rebases_hardware_counter(self):
        self.reference();self.robot.joystick_command({},False);self.call('MODE',value=2)
        self.robot.joystick_command({},False)
        for _ in range(3):
            self.robot.joystick_command({Axis.Y:-1},True)
            self.assertLessEqual(abs(self.raw.targets[Axis.Y]),8388607)
            self.finish()
        self.assertEqual(self.call('READ')['axes']['Y']['position'],-19080000)
        self.robot.joystick_command({},False)
        self.assertFalse(self.raw.busy(Axis.Y))

    def test_oversized_fine_pulse_command_refused_without_counter_change(self):
        self.reference();self.call('MODE',value=1)
        with self.assertRaises(Refused):self.call('MOVE_REL',axis='Y',value=-19080000)
        self.assertEqual(self.raw.position(Axis.Y),0)
        self.assertEqual(self.raw.targets[Axis.Y],0)

    def test_raw_rate_overrides_refused(self):
        for op in ('MAX_SPEED','ACCEL'):
            with self.assertRaises(Refused):self.call(op,axis='X',value=15000)

class ProfileNetworkTests(unittest.TestCase):
    def test_ioc_and_profile_shim_over_tcp(self):
        import threading
        from pcl602_shim import Server
        cfg=json.loads(Path(__file__).with_name('config.json').read_text())
        raw=Simulator();robot=Robot(raw,cfg)
        server=Server(('127.0.0.1',0),robot)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01})
        thread.start()
        host,port=server.server_address
        d=Device('TEST',dict(channels=['X','Y'],ip=host,port=port,require_speed_profiles=True))
        try:
            self.assertTrue(d.connect())
            d.do_sets(1,'TEST:CONTROL_MODE')
            self.assertEqual(d.pvs['CONTROL_MODE'].value,1)
            d.do_sets(.01,'TEST:JOG_DISTANCE_CM')
            d.do_sets(1,'TEST:Y_JOG')
            self.assertLess(raw.targets[Axis.Y],0)
            d.do_sets(1,'TEST:ESTOP')
            for axis in ('X','Y'):
                d.do_sets(0,'TEST:'+axis+'_MACHINE_SET_CM')
                d.do_sets(1,'TEST:'+axis+'_MACHINE_APPLY')
            d.do_sets('Medium','TEST:SPEED_MODE')
            self.assertEqual(d.pvs['SPEED_MODE'].value,1)
            self.assertEqual(d.pvs['Y_RESOLUTION'].value,64)
            d.do_sets('EPICS','TEST:CONTROL_MODE')
            d.do_sets(1,'TEST:JOG_DISTANCE_CM')
            d.do_sets(1,'TEST:Y_JOG')
            self.assertEqual(raw.targets[Axis.Y],-79500)
            raw.pos[Axis.Y]=raw.targets[Axis.Y]
            d.read_outs()
            self.assertEqual(d.pvs['Y_CM'].value,1)
            d.do_sets('Slow','TEST:SPEED_MODE')
            self.assertEqual(d.pvs['Y_CM'].value,1)
            d.do_sets(1,'TEST:ESTOP')
            self.assertEqual(d.pvs['CONTROL_MODE'].value,0)
        finally:
            d.close();server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
