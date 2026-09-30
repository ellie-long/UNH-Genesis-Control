"""Centimeter IOC commands using the actual driver and an in-process shim."""
import copy
import math
import unittest
from test_pcl602 import Device, CONFIG, DeviceRefused
from pcl602_shim import Robot, Simulator
from controller import Axis

class CentimeterTests(unittest.TestCase):
    def setUp(self):
        self.backend=Simulator()
        self.robot=Robot(self.backend,copy.deepcopy(CONFIG))
        self.owner=object();self.robot.claim(self.owner)
        self.device=Device('TEST',dict(channels=['X','Y'],steps_per_cm=10000))
        self.calls=[]
        outer=self
        class Peer:
            def request(self,op,**fields):
                outer.calls.append((op,fields))
                return outer.robot.dispatch(outer.owner,dict(op=op,**fields))
        self.device.t=Peer()
        self.device.do_sets(1,'TEST:CONTROL_MODE')
        self.calls.clear()
    def put(self,name,value): self.device.do_sets(value,'TEST:'+name)
    def test_distance_edit_never_moves(self):
        self.put('JOG_DISTANCE_CM',2.5)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.device.jog_distance_cm,2.5)
    def test_four_directions_use_centimeters(self):
        for axis in Axis:
            for sign in (-1,1):
                self.backend.set_position(axis,0)
                self.put('JOG_DISTANCE_CM',2.5)
                self.put(axis.value+'_JOG',sign)
                self.assertEqual(self.backend.targets[axis],sign*25000)
                self.put('ESTOP',1);self.put('CONTROL_MODE',1)
    def test_invalid_distance_reverts_without_motion(self):
        previous=self.device.jog_distance_cm
        for value in (0,-1,float('nan'),float('inf'),0.00001,1000):
            self.put('JOG_DISTANCE_CM',value)
            self.assertEqual(self.device.jog_distance_cm,previous)
            self.assertEqual(self.device.pvs['JOG_DISTANCE_CM'].value,previous)
        self.assertFalse(any(op=='MOVE_REL' for op,_ in self.calls))
    def test_nearest_step_rounding(self):
        self.assertEqual(self.device._jog_steps(.00015),2)
        self.assertEqual(self.device._jog_steps(.00016),2)
        self.assertEqual(self.device._jog_steps(.00005),1)
    def test_both_axis_busy_interlock(self):
        self.backend.move_relative(Axis.Y,100000)
        self.put('X_JOG',1)
        self.assertFalse(any(op=='MOVE_REL' for op,_ in self.calls))
        self.assertIn('both axes ready',self.device.last_error)
    def test_disabled_and_invalid_direction_rejected(self):
        self.put('CONTROL_MODE',0)
        self.put('X_JOG',1)
        self.put('X_JOG',0)
        self.assertFalse(any(op=='MOVE_REL' for op,_ in self.calls))
    def test_existing_travel_limits_apply(self):
        self.backend.set_position(Axis.X,8388600)
        self.put('JOG_DISTANCE_CM',1)
        self.put('X_JOG',1)
        self.assertEqual(self.backend.targets[Axis.X],8388600)
        self.assertIn('outside',self.device.last_error)
    def test_coordinates_and_bounds_use_same_calibration(self):
        self.backend.set_position(Axis.X,25000)
        self.device._apply(self.robot.snapshot())
        self.assertEqual(self.device.pvs['X_CM'].value,2.5)
        self.assertAlmostEqual(self.device.pvs['X_LOW_CM'].value,-838.8607)
        self.assertAlmostEqual(self.device.pvs['Y_HIGH_CM'].value,838.8607)
    def test_calibration_validation_and_change(self):
        for scale in (0,-1,math.nan,math.inf):
            with self.assertRaises(ValueError):Device('TEST',dict(channels=['X','Y'],steps_per_cm=scale))
        d=Device('TEST',dict(channels=['X','Y'],steps_per_cm=20000))
        self.assertEqual(d._jog_steps(2.5),50000)

if __name__=='__main__':unittest.main()
