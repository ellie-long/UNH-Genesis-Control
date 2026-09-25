import ast
from pathlib import Path
import unittest


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "online_fitter"
    / "epics_run.py"
)


class TestFitterEpicsRuntimeStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SOURCE.read_text()
        cls.tree = ast.parse(cls.text)

    def _method_node(self, class_name, method_name):
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in node.body:
                    if (
                        isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and child.name == method_name
                    ):
                        return child
        self.fail(f"{class_name}.{method_name} not found")

    def _string_constants(self, node):
        return {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
        }

    def test_expected_prefix(self):
        self.assertIn(
            'DEFAULT_PREFIX = "UNH:GEN:SSRF:FIT"',
            self.text,
        )

    def test_loopback_only(self):
        self.assertIn(
            'DEFAULT_HOST = "127.0.0.1"',
            self.text,
        )
        self.assertIn(
            "fitter publisher must remain loopback-only",
            self.text,
        )

    def test_authoritative_engine_is_reused(self):
        self.assertIn(
            "_install_capture_hooks",
            self.text,
        )
        self.assertIn(
            "R.main(",
            self.text,
        )

    def test_update_constants_uses_fit_engine(self):
        self.assertIn(
            "self.holder.engine.update_constants()",
            self.text,
        )

    def test_mode_uses_shared_state(self):
        self.assertIn(
            "self.holder.state.set_mode(mode)",
            self.text,
        )
        self.assertIn(
            "self.holder.mode_hint = mode",
            self.text,
        )

    def test_asyncio_dispatcher_is_explicit(self):
        self.assertIn(
            "asyncio_dispatcher.AsyncioDispatcher()",
            self.text,
        )
        self.assertIn(
            "softioc.iocInit(dispatcher)",
            self.text,
        )
        self.assertNotIn(
            "softioc.iocInit()\n",
            self.text,
        )

    def test_publisher_idle_timeout_does_not_poison_reader(self):
        method = self._method_node(
            "FitterEpicsInterface",
            "_subscriber_loop",
        )

        calls = {
            getattr(child.func, "attr", None)
            for child in ast.walk(method)
            if isinstance(child, ast.Call)
        }

        self.assertIn("recv", calls)
        self.assertNotIn("makefile", calls)

        source = ast.get_source_segment(
            self.text,
            method,
        )

        self.assertIn(
            "except socket.timeout:",
            source,
        )

    def test_mode_readback_has_revision_tolerant_resolution(self):
        method = self._method_node(
            "FitterEpicsInterface",
            "_state_mode",
        )

        constants = self._string_constants(
            method
        )

        self.assertIn("mode", constants)
        self.assertIn("_mode", constants)
        self.assertIn("get_mode", constants)

        self.assertIn(
            "holder.mode_hint = str(mode)",
            self.text,
        )

    def test_update_constants_uses_same_mode_resolver(self):
        method = self._method_node(
            "FitterEpicsInterface",
            "_on_update_constants",
        )

        source = ast.get_source_segment(
            self.text,
            method,
        )

        self.assertIn(
            "self._state_mode(",
            source,
        )

        self.assertNotIn(
            'getattr(state, "mode"',
            source,
        )

    def test_no_hardware_command_authority(self):
        forbidden = (
            "LOAD_CMD",
            "OUTPUT_CMD",
            "RF_CMD",
            "FM_CMD",
            "ARM_CMD",
            "START_CMD",
            "WAVEFORM_CMD",
        )

        # Ignore explanatory module docstring/comments and inspect AST string
        # literals actually used by the program.
        strings = {
            node.value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        }

        for name in forbidden:
            self.assertNotIn(
                name,
                strings,
            )

    def test_required_operator_pvs_exist(self):
        required = (
            "MODE_CMD",
            "MODE_RBV",
            "MATERIAL_CMD",
            "MATERIAL_RBV",
            "UPDATE_CONSTANTS_CMD",
            "CALIBRATED",
            "UPDATE_BUSY",
            "P_FIT",
            "Q_FIT",
            "CC",
            "CC_DAQ",
            "FREQS_MHZ",
            "DATA",
            "TOTAL_FIT",
            "RAW",
        )

        strings = {
            node.value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        }

        for name in required:
            self.assertIn(
                name,
                strings,
            )


if __name__ == "__main__":
    unittest.main()
