import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

START_SCRIPT = (
    ROOT
    / "scripts"
    / "start_ssrf_sim_active.sh"
)


class TestSsrfSimActiveStartup(unittest.TestCase):

    def setUp(self):
        self.text = START_SCRIPT.read_text()

    def test_manager_wait_exists(self):
        self.assertIn(
            "async def wait_manager():",
            self.text,
        )

    def test_manager_wait_targets_control_pv(self):
        self.assertIn(
            "ssrf_smc100a_control",
            self.text,
        )

    def test_manager_wait_is_bounded(self):
        self.assertIn(
            "timeout_s = 60.0",
            self.text,
        )

    def test_manager_wait_retries(self):
        self.assertIn(
            "await asyncio.sleep(0.5)",
            self.text,
        )

    def test_manager_wait_reports_last_error(self):
        self.assertIn(
            "last error:",
            self.text,
        )

    def test_screen_discovery_tolerates_screen_status(self):
        self.assertRegex(
            self.text,
            re.compile(
                r"screen -ls 2>/dev/null \|\| true"
            ),
        )


if __name__ == "__main__":
    unittest.main()
