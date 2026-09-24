import tempfile
import unittest
from pathlib import Path

from genesis_common.access_security import (
    AccessSecurityConfigurationError,
    configure_access_security,
)


class FakeFunction:

    def __init__(self, status=0):
        self.status = status
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, value):
        self.calls.append(value)
        return self.status


class FakeDbCore:

    def __init__(
        self,
        *,
        filename_status=0,
        substitutions_status=0,
    ):
        self.asSetFilename = FakeFunction(
            filename_status
        )

        self.asSetSubstitutions = FakeFunction(
            substitutions_status
        )


class TestAccessSecurityConfiguration(
    unittest.TestCase
):

    def test_disabled_by_default(self):
        core = FakeDbCore()

        result = configure_access_security(
            {
                "general": {},
            },
            dbcore=core,
        )

        self.assertIsNone(result)
        self.assertEqual(
            core.asSetFilename.calls,
            [],
        )

    def test_absolute_policy_registered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy = root / "policy.acf"
            policy.write_text(
                "ASG(DEFAULT) { RULE(1,READ) }\n"
            )

            core = FakeDbCore()

            result = configure_access_security(
                {
                    "general": {
                        "access_security_file":
                            "policy.acf",
                    },
                },
                project_root=root,
                dbcore=core,
            )

            self.assertEqual(
                result,
                str(
                    policy.resolve()
                ),
            )

            self.assertEqual(
                core.asSetFilename.calls,
                [
                    str(
                        policy.resolve()
                    ).encode(),
                ],
            )

    def test_missing_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(
                FileNotFoundError
            ):
                configure_access_security(
                    {
                        "general": {
                            "access_security_file":
                                "missing.acf",
                        },
                    },
                    project_root=td,
                    dbcore=FakeDbCore(),
                )

    def test_filename_registration_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy = root / "policy.acf"
            policy.write_text(
                "ASG(DEFAULT) { RULE(1,READ) }\n"
            )

            with self.assertRaises(
                AccessSecurityConfigurationError
            ):
                configure_access_security(
                    {
                        "general": {
                            "access_security_file":
                                "policy.acf",
                        },
                    },
                    project_root=root,
                    dbcore=FakeDbCore(
                        filename_status=1,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
