"""Ownership and loss regressions using in-process peers only, never sockets/USB."""
import copy
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from controller import Axis
from joystick import JoystickConfig, PygameJoystickWorker
from pcl602_shim import Robot, Simulator, Refused
from test_pcl602 import CONFIG, Device


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket', side_effect=AssertionError('no network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        config = copy.deepcopy(CONFIG)
        for data in config['axes'].values():
            data.update(minimum=-100, maximum=200, max_speed=1000)
        self.backend = Simulator()
        self.robot = Robot(self.backend, config)
        self.owner = Mock()
        self.robot.claim(self.owner)

    def call(self, op, **fields):
        return self.robot.dispatch(self.owner, dict(op=op, **fields))

    def joystick_mode(self):
        self.robot.joystick_command({}, False)
        self.call('MODE', value=2)
        self.robot.joystick_command({}, False)

    def moving(self):
        return any(self.backend.busy(a) for a in Axis)

    def test_startup_stationary_disabled_preserves_currents(self):
        self.assertEqual(self.call('READ')['mode'], 0)
        self.assertFalse(self.moving())
        self.assertTrue(all(self.backend.enabled.values()))
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())
        with self.assertRaises(Refused):
            self.call('MOVE_REL', axis='X', value=100)

    def test_epics_moves_and_ignores_joystick(self):
        self.call('MODE', value=1)
        self.call('MOVE_REL', axis='X', value=100)
        target = self.backend.targets[Axis.X]
        self.robot.joystick_command({}, False)
        self.robot.joystick_command({Axis.X: -1, Axis.Y: 1}, True)
        self.assertEqual(self.backend.targets[Axis.X], target)
        self.assertFalse(self.backend.busy(Axis.Y))
        self.assertEqual(self.robot.motion.mode, 1)

    def test_inactive_joystick_does_not_revoke_epics_with_disabled_motor(self):
        self.call('MODE', value=1)
        self.backend.set_motor_enabled(Axis.Y, False)
        self.call('READ')
        self.robot.joystick_command({Axis.Y: 1}, True)
        self.assertEqual(self.robot.motion.mode, 1)
        self.assertFalse(self.moving())

    def test_joystick_moves_and_rejects_epics(self):
        self.joystick_mode()
        with self.assertRaisesRegex(Refused, 'CONTROL_MODE=EPICS'):
            self.call('MOVE_REL', axis='X', value=100)
        self.robot.joystick_command({Axis.X: 1, Axis.Y: -1}, True)
        self.assertEqual(self.backend.targets[Axis.X], 200)
        self.assertEqual(self.backend.targets[Axis.Y], -100)

    def test_mode_change_stops_before_new_permission(self):
        self.call('MODE', value=1)
        self.call('MOVE_REL', axis='X', value=100)
        self.robot.joystick_command({}, False)
        original = self.backend.emergency_stop
        events = []
        def stop():
            events.append(self.robot.motion.mode)
            original()
        with patch.object(self.backend, 'emergency_stop', side_effect=stop):
            self.call('MODE', value=2)
        self.assertTrue(events)
        self.assertTrue(all(mode == 0 for mode in events))
        self.assertFalse(self.moving())
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())
        self.call('MODE', value=0)
        self.assertFalse(self.moving())

    def test_held_input_cannot_move_on_mode_change(self):
        self.robot.joystick_command({Axis.X: 1}, True)
        self.call('MODE', value=2)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())
        self.robot.joystick_command({}, False)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertTrue(self.moving())

    def test_joystick_to_epics_stops_active_motion(self):
        self.joystick_mode()
        self.robot.joystick_command({Axis.X: 1, Axis.Y: -1}, True)
        self.call('MODE', value=1)
        self.assertFalse(self.moving())
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())

    def test_joystick_emergency_stop_disables_epics_too(self):
        for mode in (1, 2):
            if mode == 2:
                self.joystick_mode()
                self.robot.joystick_command({Axis.X: 1}, True)
            else:
                self.call('MODE', value=1)
                self.call('MOVE_REL', axis='X', value=100)
            self.robot.emergency_stop()
            self.assertFalse(self.moving())
            self.assertEqual(self.robot.motion.mode, 0)

    def test_stop_all_and_axis_stop_override_both_modes_idempotently(self):
        for mode in (1, 2):
            for op in ('STOP', 'STOP_ALL'):
                self.call('STOP_ALL')
                if mode == 2:
                    self.joystick_mode()
                    self.robot.joystick_command({Axis.X: 1}, True)
                else:
                    self.call('MODE', value=1)
                    self.call('MOVE_REL', axis='X', value=100)
                for _ in range(3):
                    self.call(op, **({'axis': 'X'} if op == 'STOP' else {}))
                self.assertFalse(self.moving())
                self.robot.joystick_command({Axis.X: 1}, True)
                self.assertFalse(self.moving())
                self.assertTrue(all(self.backend.enabled.values()))
                if op == 'STOP_ALL':
                    self.assertEqual(self.robot.motion.mode, 0)

    def test_stop_is_attempted_even_with_latched_fault(self):
        self.robot.fault = 'fake controller failure'
        with patch.object(self.backend, 'stop', wraps=self.backend.stop) as stop:
            self.call('STOP', axis='X')
        stop.assert_called_once_with(Axis.X, hard=True)

    def test_joystick_limits_block_outward_allow_inward(self):
        for axis in Axis:
            for position, outward in ((-100, -1), (200, 1)):
                self.call('STOP_ALL')
                self.backend.set_position(axis, position)
                self.joystick_mode()
                self.robot.joystick_command({axis: outward}, True)
                self.assertFalse(self.moving())
                self.robot.joystick_command({axis: -outward}, True)
                self.assertTrue(self.backend.busy(axis))
                self.assertTrue(-100 <= self.backend.targets[axis] <= 200)

    def test_disconnect_and_reconnect_never_resume(self):
        self.joystick_mode()
        self.robot.joystick_command({Axis.X: 1}, True)
        self.robot.joystick_disconnected()
        self.assertFalse(self.moving())
        self.assertEqual(self.robot.motion.mode, 0)
        self.assertFalse(self.robot.motion.joystick_connected)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())
        self.call('MODE', value=2)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())

    def test_stale_input_stops_and_invalidates_connection(self):
        self.joystick_mode()
        self.robot.joystick_command({Axis.X: 1}, True)
        self.robot.last_joystick = time.monotonic() - 2
        self.call('READ')
        self.assertFalse(self.moving())
        self.assertEqual(self.robot.motion.mode, 0)
        self.assertFalse(self.robot.motion.joystick_connected)
        self.robot.joystick_command({Axis.X: 1}, True)
        self.assertFalse(self.moving())

    def test_control_connection_loss_and_reclaim_never_resume(self):
        for mode in (1, 2):
            self.call('MODE', value=1)
            if mode == 2:
                self.joystick_mode()
                self.robot.joystick_command({Axis.X: 1}, True)
            else:
                self.call('MOVE_REL', axis='X', value=100)
            self.robot.release(self.owner)
            self.assertFalse(self.moving())
            self.robot.claim(self.owner)
            self.assertEqual(self.robot.motion.mode, 0)
            self.assertFalse(self.moving())

    def test_control_watchdog_stops_both_modes(self):
        for mode in (1, 2):
            if mode == 2:
                self.joystick_mode()
                self.robot.joystick_command({Axis.X: 1}, True)
            else:
                self.call('MODE', value=1)
                self.call('MOVE_REL', axis='X', value=100)
            self.robot.deadline = time.monotonic() - 1
            self.robot.expire()
            self.assertFalse(self.moving())
            self.assertIsNone(self.robot.owner)
            self.assertEqual(self.robot.motion.mode, 0)
            self.robot.claim(self.owner)

    def test_ioc_status_and_loss_readbacks(self):
        device = Device('TEST', dict(channels=['X', 'Y']))
        device._apply(self.call('READ'))
        self.assertTrue(device.pvs['MOTION_INHIBITED'].value)
        self.call('MODE', value=1)
        device._apply(self.call('READ'))
        self.assertFalse(device.pvs['MOTION_INHIBITED'].value)
        self.call('STOP_ALL')
        device._apply(self.call('READ'))
        self.assertTrue(device.pvs['MOTION_INHIBITED'].value)
        self.joystick_mode()
        device._apply(self.call('READ'))
        device._set_comm(False, 'fake loss')
        self.assertEqual(device.pvs['CONTROL_MODE'].value, 0)
        self.assertFalse(device.pvs['JOYSTICK_CONNECTED'].value)
        self.assertFalse(device.pvs['JOYSTICK_ACTIVE'].value)
        self.assertEqual(device.pvs['MOTION_INHIBITED'].severity, 2)

    def test_worker_polling_failure_stops_before_reconnect_wait(self):
        self.joystick_mode()
        self.robot.joystick_command({Axis.X: 1}, True)
        worker = PygameJoystickWorker(self.robot, JoystickConfig())
        pygame = Mock()
        pygame.joystick.get_count.return_value = 1
        def wait(_):
            self.assertFalse(self.moving())
            self.assertEqual(self.robot.motion.mode, 0)
            worker._stop.set()
        with patch.dict(sys.modules, {'pygame': pygame}), \
             patch.object(worker, '_poll_device', side_effect=OSError('fake unplug')), \
             patch('joystick.time.sleep', side_effect=wait), \
             self.assertLogs('joystick', level='ERROR'):
            worker._run()


if __name__ == '__main__':
    unittest.main()
