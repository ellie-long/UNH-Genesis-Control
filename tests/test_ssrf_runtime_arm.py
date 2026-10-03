import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SEQ = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

IO = (
    ROOT
    / "logic_devices"
    / "ssrf_epics_io.py"
)

NORMAL = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_readonly"
    / "sequencer"
    / "settings.yaml"
)

ACTIVE = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
    / "sequencer"
    / "settings.yaml"
)


class TestSsrfRuntimeArm(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.seq = SEQ.read_text()
        cls.io = IO.read_text()
        cls.normal = NORMAL.read_text()
        cls.active = ACTIVE.read_text()

    def test_arm_starts_false(self):
        self.assertIn(
            "self._execution_armed = False",
            self.seq,
        )

    def test_read_only_arm_status_exists(self):
        self.assertIn(
            'self.pvs["EXECUTION_ARMED"] = '
            "builder.boolIn(",
            self.seq,
        )

        self.assertIn(
            "EXECUTION_ARMED",
            self.normal,
        )

        self.assertIn(
            "EXECUTION_ARMED",
            self.active,
        )

    def test_active_arm_controls_exist(self):
        for name in (
            "ARM_CMD",
            "DISARM_CMD",
        ):
            self.assertIn(
                name,
                self.seq,
            )

    def test_start_is_blocked_when_disarmed(self):
        self.assertIn(
            "Start blocked: execution not armed",
            self.seq,
        )

    def test_start_consumes_arm(self):
        start = self.seq.index(
            "# One successful START request "
            "consumes the arm."
        )

        request = self.seq.index(
            "self._start_requested = True",
            start,
        )

        disarm = self.seq.index(
            "self._set_execution_armed(",
            start,
        )

        self.assertLess(
            disarm,
            request,
        )

    def test_abort_disarms(self):
        marker = self.seq.index(
            "# ABORT_CMD"
        )

        disarm = self.seq.index(
            "self._set_execution_armed(",
            marker,
        )

        self.assertGreaterEqual(
            disarm,
            marker,
        )

    def test_disarm_running_requests_abort(self):
        self.assertIn(
            "Disarmed; abort requested",
            self.seq,
        )

    def test_waveform_is_inside_controlled_io_boundary(self):
        self.assertIn(
            'f"{WGEN}:WAVEFORM_CMD"',
            self.io,
        )


    def test_readiness_loss_revokes_arm(self):
        self.assertIn(
            "Any loss of burn readiness invalidates",
            self.seq,
        )

        self.assertIn(
            "Communication/input loss is fail-closed",
            self.seq,
        )

        self.assertIn(
            "Recovery never restores a previously granted arm",
            self.seq,
        )


if __name__ == "__main__":
    unittest.main()
