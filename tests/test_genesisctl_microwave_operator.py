from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "genesisctl.sh"


class GenesisCtlMicrowaveOperator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CTL.read_text(encoding="utf-8")

    def test_help_exposes_explicit_physical_workflow(self):
        for command in (
            "mw-start",
            "mw-status",
            "mw-rf-ready",
            "mw-stop",
        ):
            self.assertIn(command, self.text)

    def test_operator_commands_use_canonical_physical_script(self):
        self.assertIn(
            'MICROWAVE_PHYSICAL_SCRIPT="${ROOT}/scripts/microwave_physical.sh"',
            self.text,
        )

        for command in (
            "start",
            "status",
            "rf-ready",
            "stop",
        ):
            self.assertIn(
                f'"${{MICROWAVE_PHYSICAL_SCRIPT}}" {command}',
                self.text,
            )

    def test_dispatch_is_explicit(self):
        for command in (
            "mw-start",
            "mw-status",
            "mw-rf-ready",
            "mw-stop",
        ):
            self.assertIn(
                f"    {command})",
                self.text,
            )

    def test_generic_phase1_launcher_is_not_reachable_from_cli(self):
        self.assertNotIn(
            'START_SCRIPT="${ROOT}/scripts/start_genesis_mac.sh"',
            self.text,
        )
        self.assertNotIn(
            'STOP_SCRIPT="${ROOT}/scripts/stop_genesis_mac.sh"',
            self.text,
        )
        self.assertNotIn("normal_start() {", self.text)
        self.assertNotIn("normal_stop() {", self.text)
        self.assertNotIn("force_stop() {", self.text)
        self.assertIn("start|stop|force-stop)", self.text)
        self.assertIn("refuse_legacy_generic", self.text)

    def test_old_phase1_repository_fallback_is_absent(self):
        self.assertNotIn(
            "/Users/shrestha/project/unh/genesis/Development/genesis-phase1",
            self.text,
        )
        self.assertNotIn(
            "Could not locate genesis-phase1 repository.",
            self.text,
        )
        self.assertNotIn(
            "DEFAULT_ROOT=",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
