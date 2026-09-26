"""Configuration tests for the laptop launcher."""
import unittest

from tools.vaunix_shim.run_from_laptop import ssh_target


class SshTargetTests(unittest.TestCase):
    def test_target_is_one_plain_string(self):
        target = ssh_target({"ssh_user": "unhpoltarg", "ssh_host": "mmWaveRobot.local"})
        self.assertIsInstance(target, str)
        self.assertEqual(target, "unhpoltarg@mmWaveRobot.local")

    def test_rejects_combined_user_and_host(self):
        with self.assertRaises(SystemExit):
            ssh_target({"ssh_user": "unhpoltarg", "ssh_host": "user@host"})


if __name__ == "__main__":
    unittest.main()
