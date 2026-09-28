from __future__ import annotations

import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PHYSICAL = ROOT / "configs" / "ssrf_physical"
START = ROOT / "scripts" / "start_ssrf_physical.sh"
STOP = ROOT / "scripts" / "stop_ssrf_physical.sh"
DEPLOY = ROOT / "scripts" / "deploy_ssrf_keysight_pi.sh"
RECIPE = ROOT / "ssrf_analysis" / "burn_recipe_ioc.py"
ARCHIVER = ROOT / "logic_devices" / "archiver.py"


class FinalPhysicalOperatorContract(unittest.TestCase):
    def _yaml(self, name):
        return yaml.safe_load((PHYSICAL / name / "settings.yaml").read_text())

    def test_real_endpoints_and_fail_safe_connect_policy(self):
        smc = self._yaml("smc100a")["smc100a"]
        nmr = self._yaml("nmr_switch")["minicircuits_switch"]
        rf = self._yaml("rf_switch")["minicircuits_switch"]
        wg = self._yaml("keysight_wavegen")["keysight_wavegen"]
        self.assertEqual(smc["ip"], "192.168.1.16")
        self.assertEqual(nmr["ip"], "192.168.1.8")
        self.assertEqual(rf["ip"], "192.168.1.11")
        self.assertEqual(wg["resource"], "USB0::10893::5990::MY57252012::0::INSTR")
        for cfg in (smc, nmr, rf, wg):
            self.assertTrue(cfg["write_enabled"])
            self.assertFalse(cfg["initialize_on_connect"])

    def test_physical_sequencer_requires_prepare_and_physical_authorization(self):
        seq = self._yaml("sequencer")["ssrf_sequencer"]
        self.assertTrue(seq["execution_enabled"])
        self.assertEqual(seq["execution_mode"], "physical")
        self.assertEqual(seq["required_os_user"], "shrestha")
        self.assertTrue(seq["require_prepared"])
        self.assertEqual(seq["vhigh_v"], 0.7)
        self.assertEqual(seq["vlow_v"], -0.7)
        self.assertEqual(seq["fm_repeats"], 10)

    def test_single_final_start_uses_same_production_pv_namespaces(self):
        text = START.read_text()
        self.assertIn("configs/ssrf_physical/smc100a", text)
        self.assertIn("configs/ssrf_physical/keysight_wavegen", text)
        self.assertIn("configs/ssrf_physical/nmr_switch", text)
        self.assertIn("configs/ssrf_physical/rf_switch", text)
        self.assertIn("configs/ssrf_physical/interlock", text)
        self.assertIn("configs/ssrf_physical/sequencer", text)
        self.assertIn("configs/ssrf_physical/burn_recipe", text)
        self.assertIn("configs/ssrf_physical/archiver", text)
        self.assertNotIn("ssrf_physical_no_rf_demo_ioc.py -", text)
        self.assertIn("genesis_ssrf_sequencer", text)

    def test_startup_does_not_command_energy_outputs(self):
        text = START.read_text()
        for forbidden in (
            'caput("UNH:GEN:SSRF:GEN:RF_CMD"',
            'caput("UNH:GEN:SSRF:GEN:FM_CMD"',
            'caput("UNH:GEN:SSRF:WGEN:OUTPUT_CMD"',
            'caput("UNH:GEN:SSRF:NMR_SW:PORT_CMD"',
            'caput("UNH:GEN:SSRF:RF_SW:PORT_CMD"',
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("SMC RF is not OFF", text)
        self.assertIn("SMC FM is not OFF", text)
        self.assertIn("WaveGen output is not OFF", text)

    def test_stop_requires_verified_safe_baseline(self):
        text = STOP.read_text()
        self.assertIn("ABORT_CMD", text)
        self.assertIn("safe baseline not confirmed; refusing to stop IOCs", text)
        self.assertIn("(rf,fm,wg,sr,nm)==(0,0,0,3,0)", text)

    def test_screen_session_detection_is_pipefail_safe(self):
        for script in (START, STOP):
            text = script.read_text()
            self.assertIn(
                'listing="$(screen -ls 2>&1 || true)"',
                text,
            )
            self.assertIn(
                'grep -E "\\\\.${name}[[:space:]]" <<< "${listing}" >/dev/null',
                text,
            )
            self.assertNotIn(
                'screen -list 2>/dev/null | grep -q',
                text,
            )

    def test_keysight_deploy_is_copy_compile_only(self):
        text = DEPLOY.read_text()
        self.assertIn("rsync", text)
        self.assertIn("py_compile", text)
        self.assertNotIn("master_ioc.py -s", text)
        self.assertIn("No IOC was started", text)

    def test_recipe_has_archived_correlated_session_and_no_hardware_authority(self):
        text = RECIPE.read_text()
        self.assertIn("new_session_id", text)
        self.assertIn("SessionDataWriter", text)
        self.assertIn("JsonlEventLogger", text)
        self.assertIn("SESSION_ID", text)
        self.assertIn("waveform_planned", text)
        self.assertIn("source_prepared", text)
        self.assertIn('f"{SEQ}:PREPARE_CMD"', text)
        for forbidden in (
            "UNH:GEN:SSRF:GEN:RF_CMD",
            "UNH:GEN:SSRF:GEN:FM_CMD",
            "UNH:GEN:SSRF:WGEN:OUTPUT_CMD",
            "UNH:GEN:SSRF:NMR_SW:PORT_CMD",
            "UNH:GEN:SSRF:RF_SW:PORT_CMD",
        ):
            self.assertNotIn(forbidden, text)

    def test_physical_csv_archiver_uses_explicit_pv_list(self):
        cfg = self._yaml("archiver")["archiver"]
        self.assertGreater(len(cfg["explicit_pvs"]), 50)
        self.assertIn("UNH:GEN:SSRF:GEN:RF_RBV", cfg["explicit_pvs"])
        self.assertIn("UNH:GEN:SSRF:SEQ:EXECUTION_STATE", cfg["explicit_pvs"])
        self.assertIn("UNH:GEN:SSRF:RECIPE:SESSION_ID", cfg["explicit_pvs"])
        self.assertIn("explicit_pvs", ARCHIVER.read_text())


if __name__ == "__main__":
    unittest.main()
