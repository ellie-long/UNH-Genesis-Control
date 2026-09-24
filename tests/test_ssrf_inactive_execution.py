import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SEQUENCER = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

CONFIG = (
    ROOT
    / "configs"
    / "ssrf"
    / "sequencer"
    / "settings.yaml"
)


class TestInactiveExecution(unittest.TestCase):
    def setUp(self):
        self.source = SEQUENCER.read_text()
        self.config = CONFIG.read_text()

    def test_execution_gate_is_hard_disabled(self):
        self.assertIn(
            "EpicsIO(\n"
            "            execution_enabled=False,",
            self.source,
        )

    def test_executor_is_constructed(self):
        self.assertIn(
            "SequenceExecutor(",
            self.source,
        )

    def test_executor_is_never_run(self):
        self.assertNotIn(
            "._executor.run(",
            self.source,
        )
        self.assertNotIn(
            "await self._executor.run(",
            self.source,
        )

    def test_no_start_control(self):
        self.assertNotIn(
            "START_CMD",
            self.source,
        )
        self.assertNotIn(
            "START_CMD",
            self.config,
        )

    def test_no_abort_control(self):
        self.assertNotIn(
            "ABORT_CMD",
            self.source,
        )
        self.assertNotIn(
            "ABORT_CMD",
            self.config,
        )

    def test_no_direct_caput_in_sequencer(self):
        self.assertNotIn(
            "caput(",
            self.source,
        )

    def test_status_pvs_exist(self):
        for name in (
            "EXECUTION_ENABLED",
            "EXECUTION_STATE",
            "EXECUTION_REASON",
        ):
            self.assertIn(
                name,
                self.source,
            )
            self.assertIn(
                name,
                self.config,
            )

    def test_initial_state_disabled(self):
        self.assertIn(
            '"DISABLED"',
            self.source,
        )
        self.assertIn(
            '"Inactive: no start control"',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
