"""Joystick reversal and controller reply regressions, without hardware."""
import unittest
from controller import Axis, PCL602Controller
from motion import AxisConfig, ControlMode, MotionCoordinator

class IndexedController:
    def __init__(self):
        self.positions = {a: 0 for a in Axis}
        self.moving = {a: False for a in Axis}
        self.targets = dict(self.positions)
        self.events = []
    def emergency_stop(self):
        for a in Axis: self.moving[a] = False
    def busy(self, a): return self.moving[a]
    def position(self, a):
        if self.moving[a]: raise AssertionError('position read while busy')
        return self.positions[a]
    def move_relative(self, a, steps):
        if self.moving[a]: raise AssertionError('new move while busy')
        if not 0 < abs(steps) <= 8388607: raise AssertionError('index overflow')
        self.events.append(('move', a, steps))
        self.targets[a] = self.positions[a] + steps
        self.moving[a] = True
    def stop(self, a, *, hard=True):
        self.events.append(('stop', a, hard))
        # Stop acknowledgement may lag: remain busy until the test clears it.
    def finish(self, a):
        self.positions[a] = self.targets[a]
        self.moving[a] = False

class ReversalTests(unittest.TestCase):
    def setUp(self):
        self.c = IndexedController()
        self.m = MotionCoordinator(self.c, {a: AxisConfig() for a in Axis})
        self.m.set_mode(ControlMode.JOYSTICK)
        self.m.joystick_command({}, False)
    def test_both_axes_reverse_only_after_stop_is_complete(self):
        for a in Axis:
            self.m.joystick_command({a: 1}, True)
            self.c.positions[a] = 100
            self.m.joystick_command({a: -1}, True)
            self.assertEqual(self.c.events[-1], ('stop', a, True))
            count = len(self.c.events)
            self.m.joystick_command({a: -1}, True)
            self.assertEqual(len(self.c.events), count)
            self.c.moving[a] = False
            self.m.joystick_command({a: -1}, True)
            self.assertEqual(self.c.events[-1], ('move', a, -8388607))
            self.assertEqual(self.c.targets[a], -8388507)
            self.c.finish(a)
            self.m.joystick_command({a: -1}, True)
            self.assertEqual(self.c.events[-1], ('move', a, -100))
            self.c.finish(a)
            count = len(self.c.events)
            self.m.joystick_command({a: -1}, True)
            self.assertEqual(len(self.c.events), count)
            self.m.joystick_command({}, False)
            self.m.joystick_command({}, False)
    def test_negative_first_move_sets_negative_direction(self):
        self.m.joystick_command({Axis.X: -1, Axis.Y: -1}, True)
        self.assertEqual(self.c.events, [('move', Axis.X, -8388607), ('move', Axis.Y, -8388607)])
    def test_neutral_cancels_index_with_hard_stop(self):
        self.m.joystick_command({Axis.X: 1}, True)
        self.m.joystick_command({}, True)
        self.assertEqual(self.c.events[-1], ('stop', Axis.X, True))
    def test_latest_direction_after_stop_is_used(self):
        self.m.joystick_command({Axis.X: 1}, True)
        self.m.joystick_command({Axis.X: -1}, True)
        self.c.moving[Axis.X] = False
        self.m.joystick_command({Axis.X: 1}, True)
        self.assertEqual(self.c.events[-1], ('move', Axis.X, 8388607))
    def test_release_cancels_pending_reversal(self):
        self.m.joystick_command({Axis.X: 1}, True)
        self.m.joystick_command({Axis.X: -1}, True)
        self.c.moving[Axis.X] = False
        count = len(self.c.events)
        self.m.joystick_command({}, False)
        self.assertEqual(len(self.c.events), count)

class ReplyTests(unittest.TestCase):
    def controller(self, reply):
        class Transport:
            def command(self, request, **kwargs): return reply
        return PCL602Controller(Transport())
    def test_error_reply_contains_command_and_raw_value(self):
        with self.assertRaisesRegex(RuntimeError, "@0!.*not an integer.*bad reply"):
            self.controller('bad reply').error_code()
    def test_busy_code_cannot_be_mistaken_for_motion(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid busy status'):
            self.controller('1024').busy(Axis.X)
    def test_integer_readbacks_accept_negative_positions(self):
        self.assertEqual(self.controller('-123').position(Axis.X), -123)
    def test_explicit_negative_wire_sequence(self):
        commands = []
        class Transport:
            def command(self, request, **kwargs): commands.append(request)
        PCL602Controller(Transport()).move_relative(Axis.Y, -200)
        self.assertEqual(commands, ['@0Y-', '@0YN200', '@0YG'])

if __name__ == '__main__': unittest.main()
