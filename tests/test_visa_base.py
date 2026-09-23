import unittest
from unittest.mock import patch

from genesis_common.visa_base import (
    VisaConnection,
)


RESOURCE = (
    "USB0::0x2A8D::0x1766::"
    "MY57252012::INSTR"
)


class FakeVisaResource:
    def __init__(self):
        self.timeout = None
        self.write_termination = None
        self.read_termination = None

        self.writes = []
        self.binary_writes = []
        self.ascii_writes = []

        self.closed = False

        self.error_queue = [
            '-100,"Synthetic test error"',
            '+0,"No error"',
        ]

        self.fail_binary_write = False

    def write(self, command):
        self.writes.append(command)
        return len(command)

    def query(self, command):
        if command == "*IDN?":
            return (
                "Keysight Technologies,"
                "DSOX3024T,"
                "MY57252012,"
                "TEST\n"
            )

        if command == "MEAS?":
            return "1.250\n"

        if command == "OUTP?":
            return "1\n"

        if command == "*OPC?":
            return "1\n"

        if command == ":SYST:ERR?":
            if self.error_queue:
                return self.error_queue.pop(0)

            return '+0,"No error"'

        raise RuntimeError(
            f"unexpected query: {command}"
        )

    def write_binary_values(
        self,
        command,
        values,
        datatype="f",
        is_big_endian=False,
    ):
        if self.fail_binary_write:
            raise RuntimeError(
                "synthetic binary transfer failure"
            )

        self.binary_writes.append(
            (
                command,
                list(values),
                datatype,
                is_big_endian,
            )
        )

        return len(values)

    def write_ascii_values(
        self,
        command,
        values,
    ):
        self.ascii_writes.append(
            (
                command,
                list(values),
            )
        )

        return len(values)

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self):
        self.resource = FakeVisaResource()
        self.opened_resource = None
        self.closed = False

    def open_resource(self, resource):
        self.opened_resource = resource
        return self.resource

    def close(self):
        self.closed = True


class TestVisaConnection(unittest.TestCase):
    def setUp(self):
        self.rm = FakeResourceManager()

        patcher = patch(
            "genesis_common.visa_base."
            "pyvisa.ResourceManager",
            return_value=self.rm,
        )

        self.addCleanup(patcher.stop)
        patcher.start()

        self.connection = VisaConnection(
            resource=RESOURCE,
            backend="@py",
            timeout=2.5,
            settle=0.0,
        )

    def tearDown(self):
        self.connection.close()

    def test_open_configures_resource(self):
        self.assertEqual(
            self.rm.opened_resource,
            RESOURCE,
        )

        self.assertEqual(
            self.rm.resource.timeout,
            2500,
        )

        self.assertEqual(
            self.rm.resource.write_termination,
            "\n",
        )

        self.assertEqual(
            self.rm.resource.read_termination,
            "\n",
        )

    def test_query_helpers(self):
        self.assertIn(
            "Keysight Technologies",
            self.connection.idn(),
        )

        self.assertAlmostEqual(
            self.connection.query_float("MEAS?"),
            1.25,
        )

        self.assertTrue(
            self.connection.query_bool("OUTP?")
        )

        self.assertTrue(
            self.connection.opc()
        )

    def test_write(self):
        self.connection.write(
            ":WGEN:OUTP OFF"
        )

        self.assertEqual(
            self.rm.resource.writes,
            [":WGEN:OUTP OFF"],
        )

    def test_binary_and_ascii_writes(self):
        values = [-1.0, 0.0, 1.0]

        self.connection.write_binary_values(
            ":WGEN:ARB:DATA ",
            values,
            datatype="f",
            is_big_endian=False,
        )

        self.assertEqual(
            self.rm.resource.binary_writes[0],
            (
                ":WGEN:ARB:DATA ",
                values,
                "f",
                False,
            ),
        )

        self.connection.write_ascii_values(
            ":WGEN:ARB:DATA ",
            values,
        )

        self.assertEqual(
            self.rm.resource.ascii_writes[0],
            (
                ":WGEN:ARB:DATA ",
                values,
            ),
        )

    def test_error_queue(self):
        errors = self.connection.errors()

        self.assertEqual(
            errors,
            ['-100,"Synthetic test error"'],
        )

    def test_close_closes_resource_and_manager(self):
        self.connection.close()

        self.assertTrue(
            self.rm.resource.closed
        )

        self.assertTrue(
            self.rm.closed
        )

        self.assertIsNone(
            self.connection.handle
        )

        self.assertIsNone(
            self.connection.rm
        )

    def test_binary_failure_can_preserve_connection(self):
        self.rm.resource.fail_binary_write = True

        with self.assertRaises(OSError):
            self.connection.write_binary_values(
                ":WGEN:ARB:DATA ",
                [-1.0, 0.0, 1.0],
                datatype="f",
                is_big_endian=False,
                close_on_error=False,
            )

        self.assertIsNotNone(
            self.connection.handle
        )

        self.assertIsNotNone(
            self.connection.rm
        )

        self.assertEqual(
            self.connection.idn(),
            (
                "Keysight Technologies,"
                "DSOX3024T,"
                "MY57252012,"
                "TEST"
            ),
        )


if __name__ == "__main__":
    unittest.main()
