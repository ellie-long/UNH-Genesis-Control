import os
import unittest
from unittest.mock import patch

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

    def test_physical_requires_explicit_allow_list(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
            )

    def test_physical_rejects_string_instead_of_list(self):
        with self.assertRaises(TypeError):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
                required_os_users="utsav",
                os_user="utsav",
            )

    def test_physical_wrong_account_fails(self):
        with self.assertRaises(
            ExecutionAuthorizationError
        ):
            validate_execution_authorization(
                execution_enabled=True,
                execution_mode="physical",
                required_os_users=[
                    "shrestha",
                    "utsav",
                    "sliferlab",
                ],
                os_user="ordinary_operator",
            )

    def test_each_explicitly_allowed_account_is_accepted(self):
        allowed = [
            "shrestha",
            "utsav",
            "sliferlab",
        ]

        for user in allowed:
            with self.subTest(user=user):
                self.assertEqual(
                    validate_execution_authorization(
                        execution_enabled=True,
                        execution_mode="physical",
                        required_os_users=allowed,
                        os_user=user,
                    ),
                    MODE_PHYSICAL,
                )

    def test_shell_environment_cannot_spoof_effective_user(self):
        with patch.dict(
            os.environ,
            {
                "USER": "utsav",
                "LOGNAME": "utsav",
            },
            clear=False,
        ):
            with patch(
                "genesis_common.execution_authorization.effective_os_user",
                return_value="ordinary_operator",
            ):
                with self.assertRaises(
                    ExecutionAuthorizationError
                ):
                    validate_execution_authorization(
                        execution_enabled=True,
                        execution_mode="physical",
                        required_os_users=[
                            "shrestha",
                            "utsav",
                            "sliferlab",
                        ],
                    )


if __name__ == "__main__":
    unittest.main()
