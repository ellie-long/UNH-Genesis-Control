import ast
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

WGEN = (
    ROOT
    / "ssrf_ioc"
    / "keysight_wavegen.py"
)

SEQ = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

CONTRACT = (
    ROOT
    / "genesis_common"
    / "ssrf_waveform_contract.py"
)

CONFIGS = (
    ROOT
    / "configs"
    / "ssrf"
    / "keysight_wavegen"
    / "settings.yaml",

    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
    / "keysight_wavegen"
    / "settings.yaml",

    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_physical_readonly"
    / "keysight_wavegen"
    / "settings.yaml",
)


class TestSsrfWavegenStageAck(
    unittest.TestCase
):

    def test_contract_has_stage_ack_pv(self):

        text = CONTRACT.read_text()

        self.assertIn(
            "WAVEGEN_WAVEFORM_STAGE_ID_PV",
            text,
        )

        self.assertIn(
            (
                "UNH:GEN:SSRF:WGEN:"
                "WAVEFORM_STAGE_ID"
            ),
            text,
        )

    def test_wavegen_exposes_separate_stage_counter(self):

        text = WGEN.read_text()

        self.assertIn(
            '"WAVEFORM_STAGE_ID"',
            text,
        )

        self.assertIn(
            "self._waveform_stage_id += 1",
            text,
        )

        self.assertIn(
            '"WAVEFORM_ID"',
            text,
        )

    def test_stage_callback_increments_ack_after_validation(self):

        source = WGEN.read_text()
        tree = ast.parse(source)

        method = None

        for node in ast.walk(tree):

            if (
                isinstance(
                    node,
                    ast.FunctionDef,
                )
                and
                node.name == "stage_waveform"
            ):
                method = node
                break

        self.assertIsNotNone(method)

        body = ast.get_source_segment(
            source,
            method,
        )

        self.assertIn(
            'self._staged_waveform =',
            body,
        )

        self.assertIn(
            '"WAVEFORM_POINTS"',
            body,
        )

        self.assertIn(
            "self._waveform_stage_id += 1",
            body,
        )

        self.assertIn(
            '"WAVEFORM_STAGE_ID"',
            body,
        )

        self.assertNotIn(
            '"LOAD_CMD"',
            body,
        )

    def test_sequencer_requires_new_acknowledgment(self):

        source = SEQ.read_text()
        tree = ast.parse(source)

        method = None

        for node in ast.walk(tree):

            if (
                isinstance(
                    node,
                    ast.AsyncFunctionDef,
                )
                and
                node.name
                == "_run_waveform_stage_request"
            ):
                method = node
                break

        self.assertIsNotNone(method)

        body = ast.get_source_segment(
            source,
            method,
        )

        self.assertIn(
            "WAVEGEN_WAVEFORM_STAGE_ID_PV",
            body,
        )

        self.assertIn(
            "baseline_stage_id",
            body,
        )

        self.assertIn(
            "last_stage_id > baseline_stage_id",
            body,
        )

        self.assertIn(
            "last_points == WAVEFORM_POINTS",
            body,
        )

        self.assertIn(
            "await self._execution_io.sleep(",
            body,
        )

        self.assertNotIn(
            '"LOAD_CMD"',
            body,
        )

        self.assertNotIn(
            '"OUTPUT_CMD"',
            body,
        )

    def test_all_wavegen_profiles_publish_stage_ack(self):

        for path in CONFIGS:

            cfg = yaml.safe_load(
                path.read_text()
            )

            device_names = [
                name
                for name, value in cfg.items()
                if (
                    name != "general"
                    and
                    isinstance(value, dict)
                    and
                    "records" in value
                )
            ]

            self.assertEqual(
                len(device_names),
                1,
                msg=str(path),
            )

            device = cfg[
                device_names[0]
            ]

            self.assertIn(
                "WAVEFORM_STAGE_ID",
                device["records"],
                msg=str(path),
            )

            self.assertIn(
                "WAVEFORM_STAGE_ID",
                device["archive_records"],
                msg=str(path),
            )


if __name__ == "__main__":
    unittest.main()
