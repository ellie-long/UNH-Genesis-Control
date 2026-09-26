import asyncio
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from microwave_ioc.spd1305x.controller import SPD1000XController as Controller, SocketSCPITransport

# Isolate the adapter from EPICS; no live IOC or instrument is contacted.
softioc = types.ModuleType('softioc')
softioc.alarm = types.SimpleNamespace(NO_ALARM=0, MAJOR_ALARM=2, COMM_ALARM=9)
softioc.builder = types.SimpleNamespace(**{name: Mock(side_effect=lambda *a, **k: Mock())
    for name in ('aOut', 'aIn', 'boolOut', 'boolIn', 'stringIn')})
sys.modules['softioc'] = softioc
sys.path.append(str(Path(__file__).resolve().parents[1]))
# Local staging fallback for the shared BaseDevice reference.
if not (Path(__file__).resolve().parents[1] / 'devices').exists():
    sys.path.append('/Users/blimric06/softioc_toolkit')
from microwave_ioc.spd1305x.toolkit_device import Device

class Supply:
    def __init__(self):
        self.commands = []
        self.v, self.i, self.output, self.timer = 12., 1., True, True
        self.ovp, self.ocp = 5.0, 0.0
        self.closed = False
    def write(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith('CH1:VOLTage '): self.v = float(cmd.split()[-1])
        if cmd.startswith('CH1:CURRent '): self.i = float(cmd.split()[-1])
        if cmd.startswith('OUTPut '): self.output = cmd.endswith(',ON')
        if cmd.startswith('OVP '): self.ovp = float(cmd.split()[-1])
        if cmd.startswith('OCP '): self.ocp = float(cmd.split()[-1])
        if cmd.startswith('TIMEr '): self.timer = cmd.endswith(',ON')
    def query(self, cmd):
        return {'*IDN?': 'Siglent,SPD1305X,serial,firmware',
            'CH1:VOLTage?': str(self.v), 'CH1:CURRent?': str(self.i),
            'SYSTem:STATus?': hex((16 if self.output else 0) | (64 if self.timer else 0)),
            'MEASure:VOLTage? CH1': '0', 'MEASure:CURRent? CH1': '0',
            'MEASure:POWEr? CH1': '0', 'OVP?': str(self.ovp), 'OCP?': str(self.ocp)}[cmd]
    def close(self): self.closed = True

class Limits(unittest.TestCase):
    def setUp(self):
        ceiling = patch.object(Controller, "SAFETY_MAX_CURRENT", 0.0)
        ceiling.start()
        self.addCleanup(ceiling.stop)
        self.s = Supply()
        self.c = Controller(self.s, command_delay=0)
    def test_high_config_cannot_raise_limits(self):
        self.assertEqual((self.c.max_voltage, self.c.max_current), (5, 0))
        self.c.set_voltage(5)
        self.assertEqual(self.s.commands, ['CH1:VOLTage 5.000'])
    def test_reject_bad_values_without_writing(self):
        for operation, values in [(self.c.set_voltage, [5.00001, 30, -1, float('nan'), float('inf')]),
                                  (self.c.set_current, [.001, 5, -1, float('nan')]),
                                  (self.c.set_ovp, [5.101]), (self.c.set_ocp, [.111])]:
            for value in values:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError): operation(value)
        self.assertEqual(self.s.commands, [])
    def test_mutable_instance_limits_cannot_bypass_source(self):
        self.c.max_voltage, self.c.max_current = 30, 5
        with self.assertRaises(ValueError): self.c.set_voltage(6)
        with self.assertRaises(ValueError): self.c.set_current(1)
    def test_rounding_cannot_cross_lower_limit(self):
        self.c.max_voltage = 4.9996
        with self.assertRaises(ValueError): self.c.set_voltage(4.9996)
    def test_safe_initialization(self):
        self.c.initialize_safe()
        self.assertEqual(self.s.commands[:4], ['OUTPut CH1,OFF', 'TIMEr CH1,OFF',
                         'CH1:VOLTage 0.000', 'CH1:CURRent 0.000'])
        self.assertFalse(self.s.output)
        self.assertFalse(self.s.timer)
    def test_placeholder_blocks_enable(self):
        with self.assertRaises(ValueError): self.c.set_output(True)
        self.assertNotIn('OUTPut CH1,ON', self.s.commands)
    def test_enable_checks_actual_device_and_timer(self):
        with patch.object(Controller, 'SAFETY_MAX_CURRENT', .1):
            c = Controller(self.s, command_delay=0)
            with self.assertRaises(ValueError): c.set_output(True)
            self.assertFalse(self.s.output)
            self.assertNotIn('OUTPut CH1,ON', self.s.commands)
            c.initialize_safe()
            c.set_voltage(5)
            c.set_current(.1)
            c.set_output(True)
            self.assertTrue(self.s.output)
    def test_poll_shuts_off_unsafe_setpoints(self):
        with self.assertRaises(ValueError): self.c.read_all()
        self.assertFalse(self.s.output)

class Recovery(unittest.TestCase):
    def setUp(self):
        self.d = Device(
            'TEST',
            {
                'channels': ['CH1'],
                'ip': 'unused',
                'command_delay': 0,
                'write_enabled': True,
                'initialize_on_connect': True,
            },
        )
    def test_failed_handshake_closes_socket_and_retries(self):
        bad, good = Supply(), Supply()
        bad.query = Mock(side_effect=TimeoutError('booting'))
        with patch('microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport', side_effect=[bad, good]):
            self.d.connect()
            self.assertTrue(bad.closed)
            self.assertIsNone(self.d.t)
            self.assertTrue(asyncio.run(self.d.do_reads()))
            self.assertFalse(good.output)
    def test_failed_initial_poll_closes_socket(self):
        supply = Supply()
        original = supply.query
        supply.query = lambda cmd: (_ for _ in ()).throw(TimeoutError('OVP?')) if cmd == 'OVP?' else original(cmd)
        with patch('microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport', return_value=supply):
            self.d.connect()
        self.assertTrue(supply.closed)
        self.assertIsNone(self.d.t)
    def test_power_loss_then_boot_failure_then_recovery(self):
        old, new = Supply(), Supply()
        with patch('microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport', side_effect=[old, TimeoutError('offline'), new]):
            self.d.connect()
            old.query = Mock(side_effect=TimeoutError('powered off'))
            self.assertFalse(asyncio.run(self.d.do_reads()))
            self.assertTrue(old.closed)
            self.assertFalse(asyncio.run(self.d.do_reads()))
            self.d.do_sets(5, 'TEST:VSET_CMD')  # disconnected write is dropped
            self.assertTrue(asyncio.run(self.d.do_reads()))
            self.assertEqual(new.v, 0)
            self.assertFalse(new.output)
    def test_invalid_write_does_not_reconnect(self):
        supply = Supply()
        with patch('microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport', return_value=supply) as connect:
            self.d.connect()
            self.d.do_sets(6, 'TEST:VSET_CMD')
            self.assertEqual(connect.call_count, 1)
            self.assertFalse(supply.closed)
            self.assertEqual(supply.v, 0)
    def test_timeout_closes_transport(self):
        sock = Mock()
        sock.recv.side_effect = TimeoutError('late response')
        with patch('socket.create_connection', return_value=sock):
            transport = SocketSCPITransport('unused')
            with self.assertRaisesRegex(OSError, 'IDN'):
                transport.query('*IDN?')
            sock.close.assert_called_once()
    def test_split_tcp_response(self):
        sock = Mock()
        sock.recv.side_effect = [b'5.', b'000\r\n']
        with patch('socket.create_connection', return_value=sock):
            transport = SocketSCPITransport('unused')
            self.assertEqual(transport.query('CH1:VOLTage?'), '5.000')

class ReadbackFeedback(unittest.TestCase):
    def test_polls_never_queue_device_commands(self):
        d = Device('TEST', {'channels': ['CH1'], 'ip': 'unused', 'command_delay': 0})
        supply = Supply()
        controller = Controller(supply, command_delay=0)
        controller.initialize_safe()
        supply.v, supply.i, supply.output = 4.0, .05, True
        d.t = controller
        pending = []

        class OutputRecord:
            # Match pysoftioc's default callback dispatch on .set(process=True).
            def __init__(self, name):
                self.name, self.value = name, None
            def set(self, value, process=True):
                if process and value != self.value:
                    pending.append((self.name, value))
                self.value = value

        for name in ['VSET_CMD', 'ISET_CMD', 'OUTPUT_CMD', 'OVP_CMD', 'OCP_CMD']:
            d.pvs[name] = OutputRecord(name)
        supply.commands.clear()
        for _ in range(3):
            self.assertTrue(asyncio.run(d.do_reads()))
        self.assertEqual(pending, [], 'Readback updates must not queue write callbacks')
        self.assertEqual(supply.commands, [])
        self.assertTrue(supply.output)

    def test_protection_readback_above_entry_ceiling_is_not_written(self):
        d = Device('TEST', {'channels': ['CH1'], 'ip': 'unused', 'command_delay': 0})
        supply = Supply()
        c = Controller(supply, command_delay=0)
        c.initialize_safe()
        values = c.read_all()
        values.update(ovp=12.0, ocp=2.0)
        d._apply_readings(values)
        d.pvs['OVP_CMD'].set.assert_called_once_with(12.0, process=False)
        d.pvs['OCP_CMD'].set.assert_called_once_with(2.0, process=False)

    def test_installed_current_ceiling(self):
        c = Controller(Supply(), command_delay=0)
        c.set_current(Controller.SAFETY_MAX_CURRENT)
        with self.assertRaises(ValueError):
            c.set_current(Controller.SAFETY_MAX_CURRENT + .001)

class ProtectionDefaults(unittest.TestCase):
    def test_initialize_applies_defaults_with_output_off(self):
        supply = Supply()
        c = Controller(supply, command_delay=0)
        c.initialize_safe()
        self.assertEqual((supply.ovp, supply.ocp), (5.1, .11))
        self.assertFalse(supply.output)
        self.assertNotIn('OUTPut CH1,ON', supply.commands)
        c.set_voltage(5)
        c.set_current(.1)
        with self.assertRaises(ValueError): c.set_voltage(5.001)
        with self.assertRaises(ValueError): c.set_current(.101)
        c.set_ovp(5.1)
        c.set_ocp(.11)

    def test_defaults_independent_of_lower_output_configuration(self):
        c = Controller(Supply(), max_voltage=1, max_current=.01, command_delay=0)
        c.initialize_safe()
        self.assertEqual(c.ovp(), 5.1)
        self.assertEqual(c.ocp(), .11)

    def test_protection_record_bounds(self):
        Device('TEST', {'channels': ['CH1'], 'ip': 'unused'})
        calls = {c.args[0]: c.kwargs for c in softioc.builder.aOut.call_args_list}
        self.assertEqual(calls['VSET_CMD']['DRVH'], 5.0)
        self.assertEqual(calls['ISET_CMD']['DRVH'], .1)
        self.assertEqual(calls['OVP_CMD']['DRVH'], 5.1)
        self.assertEqual(calls['OCP_CMD']['DRVH'], .11)

    def test_ignored_protection_write_prevents_connection(self):
        supply = Supply()
        original = supply.write
        supply.write = lambda cmd: None if cmd.startswith('OVP ') else original(cmd)
        d = Device(
            'TEST',
            {
                'channels': ['CH1'],
                'ip': 'unused',
                'command_delay': 0,
                'write_enabled': True,
                'initialize_on_connect': True,
            },
        )
        with patch('microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport', return_value=supply):
            d.connect()
        self.assertIsNone(d.t)
        self.assertTrue(supply.closed)
        self.assertFalse(supply.output)

class ReadOnlyCommissioning(unittest.TestCase):
    def test_controller_blocks_all_writes(self):
        supply = Supply()
        controller = Controller(
            supply,
            command_delay=0,
            write_enabled=False,
        )

        # Queries remain available.
        self.assertIn("SPD1305X", controller.identity())

        # Hardware-changing operations must be rejected.
        operations = [
            lambda: controller.set_voltage(1.0),
            lambda: controller.set_current(0.05),
            lambda: controller.set_output(False),
            lambda: controller.set_ovp(5.1),
            lambda: controller.set_ocp(0.11),
        ]

        for operation in operations:
            with self.assertRaises(PermissionError):
                operation()

        self.assertEqual(
            supply.commands,
            [],
            "Read-only controller must never transmit a write command",
        )

    def test_read_only_device_connects_without_initialization_writes(self):
        supply = Supply()

        # Put the simulated supply in the same safe state as the
        # physical SPD1305X observed during commissioning.
        supply.v = 5.0
        supply.i = 0.05
        supply.output = False
        supply.timer = False
        supply.ovp = 5.1
        supply.ocp = 0.11
        supply.commands.clear()

        device = Device(
            'TEST',
            {
                'channels': ['CH1'],
                'ip': 'unused',
                'command_delay': 0,
                'write_enabled': False,
            },
        )

        with patch(
            'microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport',
            return_value=supply,
        ):
            device.connect()

        self.assertIsNotNone(device.t)
        self.assertFalse(supply.closed)
        self.assertEqual(
            supply.commands,
            [],
            "Read-only connection must not initialize or alter the supply",
        )

        # An EPICS write request must also be rejected without reaching hardware.
        device.do_sets(4.5, 'TEST:VSET_CMD')

        self.assertEqual(supply.commands, [])
        self.assertEqual(supply.v, 5.0)
        device.pvs["ERROR"].set.assert_called_with(
            "Read-only mode; command not sent"
        )

    def test_unsafe_read_only_state_is_reported_without_corrective_write(self):
        supply = Supply()

        # Supply() deliberately starts in an unsafe state:
        # 12 V, 1 A, output ON, timer ON.
        device = Device(
            'TEST',
            {
                'channels': ['CH1'],
                'ip': 'unused',
                'command_delay': 0,
                'write_enabled': False,
            },
        )

        with patch(
            'microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport',
            return_value=supply,
        ):
            device.connect()

        # The IOC refuses the unsafe connection, but commissioning mode
        # must not silently modify the real instrument.
        self.assertIsNone(device.t)
        self.assertTrue(supply.closed)
        self.assertEqual(
            supply.commands,
            [],
            "Read-only mode must not issue corrective hardware writes",
        )
        self.assertTrue(
            supply.output,
            "Read-only commissioning must observe, not change, hardware state",
        )

class ControlledWriteCommissioning(unittest.TestCase):
    def test_writable_connection_can_skip_initialization(self):
        supply = Supply()

        # Safe state matching the physical commissioning setup.
        supply.v = 5.0
        supply.i = 0.05
        supply.output = False
        supply.timer = False
        supply.ovp = 5.1
        supply.ocp = 0.11
        supply.commands.clear()

        device = Device(
            'TEST',
            {
                'channels': ['CH1'],
                'ip': 'unused',
                'command_delay': 0,
                'write_enabled': True,
                'initialize_on_connect': False,
            },
        )

        with patch(
            'microwave_ioc.spd1305x.toolkit_device.SocketSCPITransport',
            return_value=supply,
        ):
            device.connect()

        self.assertIsNotNone(device.t)
        self.assertFalse(supply.closed)
        self.assertEqual(
            supply.commands,
            [],
            "Writable commissioning connection must not initialize hardware",
        )

    def test_controller_allows_explicit_write_when_enabled(self):
        supply = Supply()

        controller = Controller(
            supply,
            command_delay=0,
            write_enabled=True,
        )

        controller.set_voltage(4.5)

        self.assertEqual(supply.v, 4.5)

if __name__ == '__main__': unittest.main()
