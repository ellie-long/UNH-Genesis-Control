from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "genesisctl.sh"


class GenesisCtlCanonicalStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CTL.read_text(encoding="utf-8")

    def function_block(self, start_marker, end_marker):
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def test_generic_status_is_profile_neutral(self):
        block = self.function_block(
            "show_status() {",
            "show_logs() {",
        )

        self.assertIn(
            "PV status is profile-specific.",
            block,
        )
        self.assertIn(
            "Use: genesis mw-status",
            block,
        )
        self.assertIn(
            "Use: genesis ssrf-physical-status",
            block,
        )
        self.assertIn(
            "Use: genesis sim-status",
            block,
        )
        self.assertNotIn(
            "ssrf_sim_pv_status",
            block,
        )
        self.assertNotIn(
            "EPICS_CA_ADDR_LIST",
            block,
        )

    def test_sim_status_uses_canonical_simulator_manager_only(self):
        block = self.function_block(
            "ssrf_sim_pv_status() {",
            "show_status() {",
        )

        self.assertIn(
            "configs/commissioning/ssrf_sim_active/ioc_manager/settings.yaml",
            block,
        )
        self.assertNotIn(
            "configs/ioc_manager",
            block,
        )
        self.assertNotIn(
            "cfg_normal",
            block,
        )

        self.assertIn(
            "    sim-status)\n"
            "        ssrf_sim_pv_status",
            self.text,
        )

    def test_endpoints_use_four_canonical_profiles(self):
        block = self.function_block(
            "show_endpoints() {",
            "physical_status() {",
        )

        expected = (
            "configs/commissioning/microwave_physical",
            "configs/commissioning/microwave_sim",
            "configs/ssrf_physical",
            "configs/commissioning/ssrf_sim_active",
        )

        for value in expected:
            self.assertIn(value, block)

        for label in (
            "MICROWAVE PHYSICAL",
            "MICROWAVE SIMULATOR",
            "SSRF PHYSICAL",
            "SSRF SIMULATOR",
        ):
            self.assertIn(label, block)

    def test_endpoints_do_not_use_retired_generic_profiles(self):
        block = self.function_block(
            "show_endpoints() {",
            "physical_status() {",
        )

        self.assertNotIn(
            "configs/ioc_manager",
            block,
        )
        self.assertNotIn(
            "configs/microwave/",
            block,
        )
        self.assertNotIn(
            "configs/ssrf/",
            block,
        )
        self.assertNotIn(
            "NORMAL CONFIG ENDPOINTS",
            block,
        )

    def test_genesisctl_has_no_runtime_dependency_on_retired_config_roots(self):
        self.assertNotIn(
            'local cfg_normal="${ROOT}/configs/ioc_manager/settings.yaml"',
            self.text,
        )
        self.assertNotIn(
            'Path("configs/ssrf/smc100a/settings.yaml")',
            self.text,
        )
        self.assertNotIn(
            'Path("configs/ssrf/keysight_wavegen/settings.yaml")',
            self.text,
        )
        self.assertNotIn(
            'Path("configs/ssrf/nmr_switch/settings.yaml")',
            self.text,
        )
        self.assertNotIn(
            'Path("configs/ssrf/rf_switch/settings.yaml")',
            self.text,
        )
        self.assertNotIn(
            'Path("configs/ssrf/sequencer/settings.yaml")',
            self.text,
        )

    def test_endpoints_command_reports_canonical_profiles(self):
        env = os.environ.copy()
        env["CONDA_DEFAULT_ENV"] = "genesis-p1"

        result = subprocess.run(
            ["bash", str(CTL), "endpoints"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )

        output = result.stdout + result.stderr

        for label in (
            "MICROWAVE PHYSICAL",
            "MICROWAVE SIMULATOR",
            "SSRF PHYSICAL",
            "SSRF SIMULATOR",
        ):
            self.assertIn(label, output)

        self.assertIn(
            "192.168.1.19:5025",
            output,
        )
        self.assertIn(
            "192.168.1.16:5025",
            output,
        )
        self.assertIn(
            "USB0::10893::5990::MY57252012::0::INSTR",
            output,
        )
        self.assertIn(
            "execution_mode = physical",
            output,
        )
        self.assertIn(
            "execution_mode = simulator",
            output,
        )

        self.assertNotIn(
            "configs/ioc_manager",
            output,
        )
        self.assertNotIn(
            "configs/microwave/",
            output,
        )
        self.assertNotIn(
            "configs/ssrf/",
            output,
        )


if __name__ == "__main__":
    unittest.main()
