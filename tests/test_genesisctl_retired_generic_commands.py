from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "genesisctl.sh"


class GenesisCtlRetiredGenericCommands(unittest.TestCase):
    def run_command(self, command):
        return subprocess.run(
            ["bash", str(CTL), command],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_retired(self, command):
        result = self.run_command(command)

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"{command} returned {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )

        combined = result.stdout + result.stderr

        self.assertIn(
            f"REFUSED: 'genesis {command}' is a retired generic Phase-I command.",
            combined,
        )

        self.assertIn(
            "The older combined macOS launcher is not invoked by genesisctl.",
            combined,
        )

        self.assertNotIn("start_genesis_mac.sh", combined)
        self.assertNotIn("stop_genesis_mac.sh", combined)
        self.assertNotIn("GENESIS_FORCE_STOP", combined)

    def test_start_is_refused(self):
        self.assert_retired("start")

    def test_stop_is_refused(self):
        self.assert_retired("stop")

    def test_force_stop_is_refused(self):
        self.assert_retired("force-stop")


if __name__ == "__main__":
    unittest.main()
