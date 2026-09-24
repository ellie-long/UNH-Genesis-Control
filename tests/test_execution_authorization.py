import unittest

from genesis_common.execution_authorization import (
    ExecutionAuthorizationError,
    MODE_INACTIVE,
    MODE_PHYSICAL,
    MODE_SIMULATOR,
    validate_execution_authorization,
)


class TestExecutionAuthorization(unittest.TestCase):

    def test_inactive_needs_no_mode(self):
        self.assertEqual(
            validate_execution_authorization(
                execution_enabled=False,
            ),
            MODE_INACTIVE,
        )

    def test_simulator_mode_allowed(self):
        self.assertEqual(
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="simulator",
            ),
            MODE_SIMULATOR,
        )

    def test_active_without_mode_fails(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
            )

    def test_unknown_mode_fails(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="unknown",
            )

    def test_physical_requires_account(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
            )

    def test_physical_wrong_account_fails(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
                required_os_user=(
                    "genesis_ssrf_sequencer"
                ),
                os_user="ordinary_operator",
            )

    def test_physical_correct_account_allowed(self):
        self.assertEqual(
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
                required_os_user=(
                    "genesis_ssrf_sequencer"
                ),
                os_user=(
                    "genesis_ssrf_sequencer"
                ),
            ),
            MODE_PHYSICAL,
        )


if __name__ == "__main__":
    unittest.main()
