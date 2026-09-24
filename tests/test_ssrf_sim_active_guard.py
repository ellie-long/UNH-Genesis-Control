import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.validate_ssrf_sim_active import (
    DEFAULT_PROFILE,
    validate_profile,
)


class TestSsrfSimulatorActiveGuard(
    unittest.TestCase
):
    def test_current_profile_passes(self):
        self.assertEqual(
            validate_profile(),
            [],
        )

    def _copy_profile(self):
        tmp = tempfile.TemporaryDirectory()

        dst = (
            Path(tmp.name)
            / "ssrf_sim_active"
        )

        shutil.copytree(
            DEFAULT_PROFILE,
            dst,
        )

        return tmp, dst

    def test_rejects_physical_smc_ip(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "smc100a"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data["smc100a"]["ip"] = (
                "192.168.1.50"
            )

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "smc100a: non-local"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()

    def test_rejects_physical_nmr_switch_ip(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "nmr_switch"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data[
                "minicircuits_switch"
            ]["ip"] = "192.168.1.8"

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "nmr_switch: non-local"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()

    def test_rejects_physical_rf_switch_ip(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "rf_switch"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data[
                "minicircuits_switch"
            ]["ip"] = "192.168.1.11"

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "rf_switch: non-local"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()

    def test_rejects_nonlocal_wavegen(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "keysight_wavegen"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data[
                "keysight_wavegen"
            ]["resource"] = (
                "TCPIP0::192.168.1.20::"
                "5027::SOCKET"
            )

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "keysight_wavegen:"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()

    def test_rejects_disabled_execution(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "sequencer"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data[
                "ssrf_sequencer"
            ]["execution_enabled"] = False

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "execution_enabled"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()

    def test_rejects_initialize_on_connect(self):
        tmp, profile = self._copy_profile()

        try:
            path = (
                profile
                / "smc100a"
                / "settings.yaml"
            )

            data = yaml.safe_load(
                path.read_text()
            )

            data[
                "smc100a"
            ]["initialize_on_connect"] = True

            path.write_text(
                yaml.safe_dump(
                    data,
                    sort_keys=False,
                )
            )

            errors = validate_profile(
                profile
            )

            self.assertTrue(
                any(
                    "initialize_on_connect"
                    in item
                    for item in errors
                )
            )

        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
