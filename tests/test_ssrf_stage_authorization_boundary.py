import ast
import unittest
from pathlib import Path

import numpy as np
import yaml

from genesis_common.ssrf_waveform_contract import (
    SEQUENCER_WAVEFORM_STAGE_REQUEST_PV,
    WAVEFORM_POINTS,
    WAVEGEN_WAVEFORM_CMD_PV,
    WaveformContractError,
    validate_normalized_waveform,
)

from ssrf_analysis.waveform_stager import (
    ALLOWED_STAGE_PVS,
    WAVEFORM_STAGE_REQUEST_PV,
)


ROOT = Path(__file__).resolve().parents[1]

SEQUENCER = (
    ROOT
    / "logic_devices"
    / "ssrf_sequencer.py"
)

RUNTIME = (
    ROOT
    / "ssrf_analysis"
    / "fitter_stage_runtime.py"
)

STAGER = (
    ROOT
    / "ssrf_analysis"
    / "waveform_stager.py"
)

WGEN_CONFIG = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_readonly"
    / "keysight_wavegen"
    / "settings.yaml"
)

NORMAL_SEQ = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_readonly"
    / "sequencer"
    / "settings.yaml"
)

ACTIVE_SEQ = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
    / "sequencer"
    / "settings.yaml"
)


class TestSsrfStageAuthorizationBoundary(
    unittest.TestCase
):

    def test_request_and_device_command_are_distinct(self):

        self.assertNotEqual(
            SEQUENCER_WAVEFORM_STAGE_REQUEST_PV,
            WAVEGEN_WAVEFORM_CMD_PV,
        )

        self.assertEqual(
            SEQUENCER_WAVEFORM_STAGE_REQUEST_PV,
            (
                "UNH:GEN:SSRF:SEQ:"
                "WAVEFORM_STAGE_REQ"
            ),
        )

        self.assertEqual(
            WAVEGEN_WAVEFORM_CMD_PV,
            (
                "UNH:GEN:SSRF:WGEN:"
                "WAVEFORM_CMD"
            ),
        )

    def test_analysis_allowlist_contains_only_request_pv(self):

        self.assertEqual(
            ALLOWED_STAGE_PVS,
            frozenset(
                {
                    WAVEFORM_STAGE_REQUEST_PV,
                }
            ),
        )

        self.assertEqual(
            WAVEFORM_STAGE_REQUEST_PV,
            SEQUENCER_WAVEFORM_STAGE_REQUEST_PV,
        )

    def test_analysis_runtime_has_no_direct_wavegen_target(self):

        runtime = RUNTIME.read_text(
            encoding="utf-8"
        )

        stager = STAGER.read_text(
            encoding="utf-8"
        )

        literal = (
            '"UNH:GEN:SSRF:WGEN:'
            'WAVEFORM_CMD"'
        )

        self.assertNotIn(
            literal,
            runtime,
        )

        self.assertNotIn(
            literal,
            stager,
        )

    def test_sequencer_owns_protected_forwarding(self):

        source = SEQUENCER.read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "WAVEGEN_WAVEFORM_CMD_PV",
            source,
        )

        self.assertIn(
            "await self._execution_io.write(",
            source,
        )

        self.assertIn(
            "WAVEFORM_STAGE_REQ",
            source,
        )

        self.assertIn(
            "validate_normalized_waveform(",
            source,
        )

    def test_sequencer_stage_request_writes_only_waveform_cmd(self):

        source = SEQUENCER.read_text(
            encoding="utf-8"
        )

        tree = ast.parse(
            source
        )

        function = None

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
                function = node
                break

        self.assertIsNotNone(
            function
        )

        writes = []

        for node in ast.walk(
            function
        ):

            if not isinstance(
                node,
                ast.Call,
            ):
                continue

            func = node.func

            if not isinstance(
                func,
                ast.Attribute,
            ):
                continue

            if func.attr != "write":
                continue

            owner = func.value

            if not isinstance(
                owner,
                ast.Attribute,
            ):
                continue

            if owner.attr != "_execution_io":
                continue

            writes.append(
                node
            )

        self.assertEqual(
            len(writes),
            1,
        )

        write = writes[0]

        self.assertGreaterEqual(
            len(write.args),
            2,
        )

        target = write.args[0]

        self.assertIsInstance(
            target,
            ast.Name,
        )

        self.assertEqual(
            target.id,
            "WAVEGEN_WAVEFORM_CMD_PV",
        )

        # Staging may forward only the protected waveform-memory
        # command. LOAD/OUTPUT remain completely separate operations.
        forbidden_targets = {
            "WAVEGEN_LOAD_CMD_PV",
            "WAVEGEN_OUTPUT_CMD_PV",
        }

        for call in writes:

            first = call.args[0]

            if isinstance(
                first,
                ast.Name,
            ):
                self.assertNotIn(
                    first.id,
                    forbidden_targets,
                )

    def test_wavegen_command_remains_access_protected(self):

        cfg = yaml.safe_load(
            WGEN_CONFIG.read_text(
                encoding="utf-8"
            )
        )

        records = cfg[
            "keysight_wavegen"
        ][
            "records"
        ]

        self.assertEqual(
            records[
                "WAVEFORM_CMD"
            ].get(
                "ASG"
            ),
            "GENESIS_SSRF_CMD",
        )

        self.assertEqual(
            records[
                "LOAD_CMD"
            ].get(
                "ASG"
            ),
            "GENESIS_SSRF_CMD",
        )

    def test_normal_sequencer_stays_execution_disabled(self):

        cfg = yaml.safe_load(
            NORMAL_SEQ.read_text(
                encoding="utf-8"
            )
        )

        seq = cfg[
            "ssrf_sequencer"
        ]

        self.assertFalse(
            bool(
                seq.get(
                    "execution_enabled",
                    False,
                )
            )
        )

        self.assertIn(
            "WAVEFORM_STAGE_REQ",
            seq["records"],
        )

    def test_simulator_profile_can_forward_requests(self):

        cfg = yaml.safe_load(
            ACTIVE_SEQ.read_text(
                encoding="utf-8"
            )
        )

        seq = cfg[
            "ssrf_sequencer"
        ]

        self.assertTrue(
            seq["execution_enabled"]
        )

        self.assertEqual(
            seq[
                "execution_mode"
            ],
            "simulator",
        )

        self.assertIn(
            "WAVEFORM_STAGE_REQ",
            seq["records"],
        )

    def test_contract_accepts_exact_normalized_waveform(self):

        values = np.linspace(
            -1.0,
            1.0,
            WAVEFORM_POINTS,
            dtype=np.float32,
        )

        checked = (
            validate_normalized_waveform(
                values
            )
        )

        self.assertEqual(
            checked.size,
            WAVEFORM_POINTS,
        )

        self.assertIsNot(
            checked,
            values,
        )

    def test_contract_rejects_wrong_length(self):

        with self.assertRaises(
            WaveformContractError
        ):
            validate_normalized_waveform(
                np.zeros(
                    WAVEFORM_POINTS - 1,
                    dtype=np.float32,
                )
            )

    def test_contract_rejects_nan(self):

        values = np.zeros(
            WAVEFORM_POINTS,
            dtype=np.float32,
        )

        values[100] = np.nan

        with self.assertRaises(
            WaveformContractError
        ):
            validate_normalized_waveform(
                values
            )

    def test_contract_rejects_out_of_range(self):

        values = np.zeros(
            WAVEFORM_POINTS,
            dtype=np.float32,
        )

        values[100] = 1.001

        with self.assertRaises(
            WaveformContractError
        ):
            validate_normalized_waveform(
                values
            )


if __name__ == "__main__":
    unittest.main()
