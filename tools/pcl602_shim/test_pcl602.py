"""Run: python3 -m unittest discover -s tools/pcl602_shim -v (no hardware needed)."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pcl602_shim import Robot, Simulator, Server, Refused
from controller import Axis, PCL602Controller

# Lightweight EPICS records let the actual IOC driver run without libca/softioc.
class Record:
    def __init__(self, name, **kw):
        self.value = kw.get('initial_value', 0)
        self.severity = 0
    def set(self, value, **kwargs): self.value = value
    def set_alarm(self, severity, alarm): self.severity = severity

builder = types.SimpleNamespace(**{name: lambda name, *args, **kw: Record(name, **kw)
    for name in ('aIn', 'aOut', 'longIn', 'longOut', 'boolIn', 'boolOut', 'mbbOut', 'stringIn')})
alarm = types.SimpleNamespace(NO_ALARM=0, MAJOR_ALARM=2, COMM_ALARM=9)
root = HERE.parents[1]
# Load driver under isolated package names without affecting real devices imports.
package = types.ModuleType('_pcl_test_devices'); package.__path__ = []
instruments = types.ModuleType('_pcl_test_devices.instruments'); instruments.__path__ = []
with patch.dict(sys.modules, {'softioc': types.SimpleNamespace(builder=builder, alarm=alarm),
                             '_pcl_test_devices': package,
                             '_pcl_test_devices.instruments': instruments}):
    for name, path in [('_pcl_test_devices.base_device', root/'devices/base_device.py'),
                        ('_pcl_test_devices.instruments.pcl602', root/'microwave_ioc/pcl602/toolkit_device.py')]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    Device, Client, DeviceRefused = module.Device, module.DeviceConnection, module.DeviceRefused

CONFIG = json.loads((HERE/'config.json').read_text())
CONFIG.pop('motion_profiles', None)
for axis in ('X', 'Y'):
    CONFIG['axes'][axis].update(minimum=-8388607, maximum=8388607)

class TestSystem(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        self.config['watchdog_seconds'] = .5
        for axis in ('X','Y'):
            self.config['axes'][axis].update(minimum=-100, maximum=200, max_speed=1000)
        self.backend = Simulator()
        self.robot = Robot(self.backend, self.config)
        self.server = Server(('127.0.0.1', 0), self.robot)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.client = Client(*self.server.server_address, timeout=2)
        self.client.request('READ')

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def wait_for(self, predicate):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.01)
        self.fail('timed out waiting for state')

    def test_disabled_start_and_both_axis_limits(self):
        with self.assertRaises(DeviceRefused): self.client.request('MOVE_REL', axis='X', value=1)
        self.client.request('MODE', value=1)
        for axis in ('X', 'Y'):
            with self.assertRaises(DeviceRefused): self.client.request('MOVE_REL', axis=axis, value=201)
            with self.assertRaises(DeviceRefused): self.client.request('MOVE_REL', axis=axis, value=-101)
        self.assertTrue(self.client.request('READ')['healthy'])

    def test_relative_uses_live_position(self):
        self.backend.set_position(Axis.X, 190)
        self.client.request('MODE', value=1)
        with self.assertRaises(DeviceRefused): self.client.request('MOVE_REL', axis='X', value=11)
        self.client.request('MOVE_REL', axis='X', value=10)
        self.assertEqual(self.backend.targets[Axis.X], 200)

    def test_disconnect_stops_and_retains_current(self):
        self.client.request('MODE', value=1)
        self.client.request('MOVE_REL', axis='X', value=200)
        self.client.close()
        self.wait_for(lambda: self.robot.owner is None)
        self.assertEqual(self.robot.motion.mode, 0)
        self.assertFalse(self.backend.busy(Axis.X))
        self.assertTrue(self.backend.motor_enabled(Axis.X))

    def test_timeout_revokes_old_session_and_reconnect_is_disabled(self):
        self.client.request('MODE', value=1)
        self.wait_for(lambda: self.robot.owner is None)
        with self.assertRaises(OSError): self.client.request('MODE', value=1)
        self.client = Client(*self.server.server_address, timeout=2)
        self.assertEqual(self.client.request('READ')['mode'], 0)

    def test_second_client_cannot_control_or_stop_owner(self):
        self.client.request('MODE', value=1)
        other = Client(*self.server.server_address, timeout=2)
        try:
            with self.assertRaises(DeviceRefused): other.request('MODE', value=0)
        finally: other.close()
        self.assertEqual(self.client.request('READ')['mode'], 1)

    def test_joystick_finite_targets_and_deadman(self):
        self.robot.joystick_command({}, False)
        self.client.request('MODE', value=2)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.backend.busy(Axis.X))  # neutral release required after mode change
        self.robot.joystick_command({}, False)
        self.robot.joystick_command({Axis.X: 1, Axis.Y: -1}, True)
        self.assertEqual(self.backend.targets[Axis.X], 200)
        self.assertEqual(self.backend.targets[Axis.Y], -100)
        self.robot.joystick_command({}, False)
        self.assertFalse(self.backend.busy(Axis.X))
        self.assertFalse(self.backend.busy(Axis.Y))

    def test_joystick_stale_updates_disable_even_with_ioc_heartbeat(self):
        self.robot.joystick_command({}, False)
        self.client.request('MODE', value=2)
        self.robot.last_joystick = time.monotonic() - 2
        self.client.request('READ')
        self.assertEqual(self.robot.motion.mode, 0)

    def test_home_rejected_zero_requires_disabled(self):
        with self.assertRaises(DeviceRefused): self.client.request('HOME', axis='X')
        self.client.request('MODE', value=1)
        with self.assertRaises(DeviceRefused): self.client.request('ZERO', axis='X')
        self.client.request('MODE', value=0)
        self.client.request('ZERO', axis='X')

    def test_malformed_requests_do_not_move(self):
        for request in [[], {'op':'MODE','value':True}, {'op':[]}, {'op':'MODE','value':5}]:
            with self.assertRaises(DeviceRefused):
                if isinstance(request, dict): self.client.request(**request)
                else:
                    self.client.sock.sendall(b'[]\n')
                    response = json.loads(self.client.reader.readline())
                    if not response['ok']: raise DeviceRefused(response['error'])
        self.assertEqual(self.robot.motion.mode, 0)

    def test_usb_failure_latches_and_stop_failure_still_disables(self):
        self.client.request('MODE', value=1)
        with patch.object(self.backend, 'position', side_effect=OSError('USB unplugged')):
            with self.assertRaises(DeviceRefused): self.client.request('READ')
        state = self.client.request('READ')
        self.assertFalse(state['healthy'])
        self.assertFalse(state['axes']['X']['position_fresh'])
        self.assertEqual(state['mode'], 0)
        with self.assertRaises(DeviceRefused): self.client.request('MODE', value=1)

    def test_busy_position_is_explicitly_cached(self):
        self.client.request('MODE', value=1)
        self.client.request('MOVE_REL', axis='X', value=200)
        with patch.object(self.backend, 'position', side_effect=lambda a: (_ for _ in ()).throw(
                AssertionError('read moving axis')) if a == Axis.X else 0):
            state = self.client.request('READ')
        self.assertTrue(state['axes']['X']['busy'])
        self.assertFalse(state['axes']['X']['position_fresh'])

    def test_lost_reply_does_not_replay_motion(self):
        self.client.request('MODE', value=1)
        with patch.object(self.backend, 'move_relative', wraps=self.backend.move_relative) as move:
            with patch('pcl602_shim.Handler.send', side_effect=OSError('response lost')):
                with self.assertRaises(OSError):
                    self.client.request('MOVE_REL', axis='X', value=200)
            self.assertEqual(move.call_count, 1)
            self.assertTrue(self.client.closed)

    def test_stop_requires_joystick_neutral_before_restart(self):
        self.robot.joystick_command({}, False)
        self.client.request('MODE', value=2)
        self.robot.joystick_command({}, False)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.client.request('STOP', axis='X')
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.backend.busy(Axis.X))

    def test_oversized_frame_closes_session(self):
        self.client.sock.sendall(b'x' * 8193 + b'\n')
        self.assertEqual(self.client.reader.readline(), b'')
        self.wait_for(lambda: self.robot.owner is None)

    def test_ioc_driver_end_to_end(self):
        self.client.close()
        self.wait_for(lambda: self.robot.owner is None)
        host, port = self.server.server_address
        device = Device('TEST', dict(ip=host, port=port, timeout=2, channels=['X','Y']))
        try:
            self.assertTrue(device.connect())
            self.assertEqual(device.pvs['X_LOW_LIMIT'].value, -100)
            self.assertEqual(device.pvs['Y_HIGH_LIMIT'].value, 200)
            device.do_sets('EPICS', 'TEST:CONTROL_MODE')
            device.do_sets(300, 'TEST:X_MOVE_REL')
            self.assertIn('outside', device.pvs['LAST_ERROR'].value)
            device.do_sets(10, 'TEST:X_MOVE_REL')
            self.assertEqual(self.backend.targets[Axis.X], 10)
            device.do_sets(True, 'TEST:ESTOP')
            self.assertEqual(device.pvs['CONTROL_MODE'].value, 0)
            self.assertTrue(asyncio.run(device.do_reads()))
        finally: device.close()

class ProtocolTests(unittest.TestCase):
    def test_direct_talk_commands_match_existing_controller(self):
        calls=[]
        class Wire:
            def command(self, request, *, expect_reply=False): calls.append((request,expect_reply)); return '12'
        c=PCL602Controller(Wire(),0)
        c.move_relative(Axis.X,-50)
        c.move_absolute(Axis.Y,200)
        c.emergency_stop()
        self.assertEqual(c.position(Axis.X),12)
        self.assertEqual(calls,[('@0X-',False),('@0XN50',False),('@0XG',False),
                               ('@0YP200',False),('@0YG',False),('@0.',False),('@0XVZ',True)])

if __name__ == '__main__': unittest.main()
