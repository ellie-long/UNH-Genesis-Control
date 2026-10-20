import unittest

from logic_devices.ssrf_epics_io import ALLOWED_COMMAND_PVS


class TestExplicitWaveformStageAuthorization(unittest.TestCase):

    def test_waveform_transfer_allowed(self):
        self.assertIn(
            "UNH:GEN:SSRF:WGEN:WAVEFORM_CMD",
            ALLOWED_COMMAND_PVS,
        )

    def test_explicit_stage_allowed(self):
        self.assertIn(
            "UNH:GEN:SSRF:WGEN:WAVEFORM_STAGE_CMD",
            ALLOWED_COMMAND_PVS,
        )

    def test_arbitrary_write_not_allowed(self):
        self.assertNotIn(
            "UNH:GEN:SSRF:WGEN:UNSAFE_TEST_CMD",
            ALLOWED_COMMAND_PVS,
        )


if __name__ == "__main__":
    unittest.main()
