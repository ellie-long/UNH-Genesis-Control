import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

SOURCE = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

SAFE_CONFIG = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_readonly"
    / "sequencer"
    / "settings.yaml"
)

ACTIVE_CONFIG = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
    / "sequencer"
    / "settings.yaml"
)


class TestSsrfActiveControls(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text()

        with open(SAFE_CONFIG) as f:
            self.safe = yaml.safe_load(f)

        with open(ACTIVE_CONFIG) as f:
            self.active = yaml.safe_load(f)

    def test_only_active_profile_enables_execution(self):
        self.assertFalse(
            self.safe[
                "ssrf_sequencer"
            ].get(
                "execution_enabled",
                False,
            )
        )

        self.assertIs(
            self.active[
                "ssrf_sequencer"
            ]["execution_enabled"],
            True,
        )

    def test_active_profile_defines_start_abort_records(self):
        records = self.active[
            "ssrf_sequencer"
        ]["records"]

        self.assertIn(
            "START_CMD",
            records,
        )
        self.assertIn(
            "ABORT_CMD",
            records,
        )

    def test_safe_profile_defines_no_start_abort_records(self):
        records = self.safe[
            "ssrf_sequencer"
        ]["records"]

        self.assertNotIn(
            "START_CMD",
            records,
        )
        self.assertNotIn(
            "ABORT_CMD",
            records,
        )

    def test_source_uses_momentary_controls(self):
        self.assertIn(
            '"START_CMD"] = builder.boolOut(',
            self.source,
        )
        self.assertIn(
            '"ABORT_CMD"] = builder.boolOut(',
            self.source,
        )
        self.assertIn(
            "process=False",
            self.source,
        )

    def test_start_requires_published_ready(self):
        self.assertIn(
            'self.pvs["READY"].get()',
            self.source,
        )
        self.assertIn(
            "Start blocked: burn not ready",
            self.source,
        )

    def test_executor_rechecks_live_readiness(self):
        self.assertIn(
            "await self._executor_readiness()",
            self.source,
        )

    def test_plan_uses_live_waveform_identity(self):
        self.assertIn(
            "build_burn_plan(",
            self.source,
        )
        self.assertIn(
            '"WAVEFORM_POINTS"',
            self.source,
        )
        self.assertIn(
            '"WAVEFORM_ID"',
            self.source,
        )

    def test_executor_is_called_once_through_task_path(self):
        self.assertIn(
            "self._executor.run(",
            self.source,
        )
        self.assertIn(
            "asyncio.create_task(",
            self.source,
        )

    def test_abort_is_cooperative(self):
        self.assertIn(
            "self._abort_requested = True",
            self.source,
        )
        self.assertIn(
            "return bool(\n"
            "            self._abort_requested",
            self.source,
        )

    def test_sequencer_has_no_direct_caput(self):
        self.assertNotIn(
            "caput(",
            self.source,
        )


    def test_active_record_descriptions_fit_epics(self):
        records = self.active[
            "ssrf_sequencer"
        ]["records"]

        for name, fields in records.items():
            desc = fields.get("DESC")

            if desc is None:
                continue

            self.assertLessEqual(
                len(str(desc).encode()),
                40,
                name,
            )




if __name__ == "__main__":
    unittest.main()
