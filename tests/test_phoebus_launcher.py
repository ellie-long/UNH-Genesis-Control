from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/start_phoebus_instance.sh"
MICROWAVE = ROOT / "scripts/microwave_physical.sh"


class PhoebusLauncherPolicy(unittest.TestCase):
    def test_explicit_override_is_supported(self):
        text = LAUNCHER.read_text()
        self.assertIn("PHOEBUS_BIN", text)
        self.assertIn("resolve_executable", text)

    def test_validated_macos_default_is_retained(self):
        text = LAUNCHER.read_text()
        self.assertIn("uname -s", text)
        self.assertIn(
            "/Applications/CSS_Phoebus.app/Contents/MacOS/main.command",
            text,
        )

    def test_linux_path_discovery_is_supported(self):
        text = LAUNCHER.read_text()
        self.assertIn("command -v", text)
        self.assertIn("phoebus phoebus.sh", text)

    def test_missing_launcher_fails_with_override_guidance(self):
        text = LAUNCHER.read_text()
        self.assertIn("Phoebus launcher not found", text)
        self.assertIn("Set PHOEBUS_BIN", text)

    def test_microwave_has_dedicated_phoebus_port(self):
        script = (
            ROOT / "scripts/start_microwave_phoebus.sh"
        ).read_text()
        self.assertIn('"4921"', script)
        self.assertIn(
            "start_phoebus_instance.sh",
            script,
        )

    def test_microwave_physical_stack_has_no_phoebus_binary_path(self):
        text = MICROWAVE.read_text()
        self.assertNotIn(
            "/Applications/CSS_Phoebus.app/Contents/MacOS/main.command",
            text,
        )
        self.assertNotIn("PHOEBUS=", text)
        self.assertIn("./scripts/start_microwave_phoebus.sh", text)


if __name__ == "__main__":
    unittest.main()
