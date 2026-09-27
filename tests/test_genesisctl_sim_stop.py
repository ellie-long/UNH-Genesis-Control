import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCRIPT = (
    ROOT
    / "scripts"
    / "genesisctl.sh"
)


class TestGenesisCtlSimStop(
    unittest.TestCase
):

    @classmethod
    def setUpClass(cls):

        cls.source = SCRIPT.read_text(
            encoding="utf-8"
        )

        start = cls.source.index(
            "sim_stop() {"
        )

        end = cls.source.index(
            "test_ssrf() {",
            start,
        )

        cls.function = cls.source[
            start:end
        ]

    def test_requires_simulator_manager_identity(self):

        self.assertIn(
            "ssrf-sim-manager",
            self.function,
        )

        self.assertIn(
            "refusing to guess",
            self.function,
        )

    def test_stops_exact_screen_references(self):

        self.assertIn(
            'screen \\\n'
            '                -S "${ref}" \\\n'
            '                -X quit',
            self.function,
        )

    def test_expected_simulator_sessions(self):

        for name in (
            "ssrf_sequencer",
            "ssrf_interlock",
            "ssrf_rf_switch",
            "ssrf_nmr_switch",
            "ssrf_keysight_wavegen",
            "ssrf_smc100a",
            "ssrf-sim-manager",
        ):

            self.assertIn(
                name,
                self.function,
            )

    def test_no_legacy_restore_guessing(self):

        self.assertNotIn(
            "--restore",
            self.function,
        )

        self.assertNotIn(
            "--deactivate",
            self.function,
        )

    def test_does_not_restore_normal_genesis(self):

        self.assertNotIn(
            "start_genesis_mac.sh",
            self.function,
        )

        self.assertNotIn(
            "normal_start",
            self.function,
        )

    def test_help_matches_behavior(self):

        self.assertIn(
            (
                "sim-stop             Stop only the active "
                "localhost simulator IOC stack."
            ),
            self.source,
        )

        self.assertIn(
            (
                "Requires the dedicated "
                "ssrf-sim-manager session."
            ),
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
