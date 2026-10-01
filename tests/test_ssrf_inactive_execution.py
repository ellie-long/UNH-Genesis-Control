import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SEQUENCER = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

SAFE_CONFIG = (
    ROOT
    / "configs"
    / "ssrf"
    / "sequencer"
    / "settings.yaml"
)


class TestInactiveExecution(unittest.TestCase):
    def setUp(self):
        self.source = SEQUENCER.read_text()
        self.config = SAFE_CONFIG.read_text()

    def test_execution_defaults_disabled(self):
        self.assertIn(
            '"execution_enabled",\n'
            '                False,',
            self.source,
        )

    def test_safe_config_does_not_enable_execution(self):
        self.assertNotIn(
            "execution_enabled: true",
            self.config,
        )

    def test_safe_config_has_no_start_control(self):
        self.assertNotIn(
            "START_CMD",
            self.config,
        )

    def test_safe_config_has_no_abort_control(self):
        self.assertNotIn(
            "ABORT_CMD",
            self.config,
        )

    def test_controls_are_conditional_in_source(self):
        self.assertIn(
            "if execution_enabled:",
            self.source,
        )
        self.assertIn(
            'self.pvs["START_CMD"]',
            self.source,
        )
        self.assertIn(
            'self.pvs["ABORT_CMD"]',
            self.source,
        )

    def test_no_direct_caput_in_sequencer(self):
        self.assertNotIn(
            "caput(",
            self.source,
        )

    def test_epics_io_remains_execution_boundary(self):
        self.assertIn(
            "self._execution_io.read_safety(",
            self.source,
        )
        self.assertIn(
            "self._executor.run(",
            self.source,
        )

    def test_safe_disabled_status_still_exists(self):
        self.assertIn(
            "EXEC_STATE_DISABLED",
            self.source,
        )
        self.assertIn(
            "Inactive: execution disabled",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
