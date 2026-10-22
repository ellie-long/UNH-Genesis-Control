from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


GUI = (
    Path(__file__).resolve().parents[1]
    / "gui"
    / "genesis_ssrf_fitter.bob"
)


class TestSsrfFitterGuiStatic(
    unittest.TestCase
):

    @classmethod
    def setUpClass(cls):

        cls.root = ET.parse(
            GUI
        ).getroot()

        cls.widgets = (
            cls.root.findall(
                "widget"
            )
        )

        cls.pvs = {
            node.text
            for node
            in cls.root.iter(
                "pv_name"
            )
            if node.text
        }

    def widget_by_name(
        self,
        name,
    ):

        for widget in self.widgets:

            if (
                widget.findtext(
                    "name"
                )
                == name
            ):
                return widget

        self.fail(
            f"widget not found: {name}"
        )

    def write_action(
        self,
        widget_name,
    ):

        widget = self.widget_by_name(
            widget_name
        )

        self.assertEqual(
            widget.attrib.get(
                "type"
            ),
            "action_button",
        )

        actions = widget.findall(
            "actions/action"
        )

        self.assertEqual(
            len(actions),
            1,
        )

        action = actions[0]

        self.assertEqual(
            action.attrib.get(
                "type"
            ),
            "write_pv",
        )

        return action

    def test_display_identity(self):

        self.assertEqual(
            self.root.findtext(
                "name"
            ),
            "Genesis ssRF NMR Fitter",
        )

        self.assertEqual(
            self.root.findtext(
                "macros/P"
            ),
            "UNH:GEN:SSRF:FIT",
        )

    def test_runtime_status_pvs(self):

        for name in (
            "FITTER_UP",
            "PUBLISHER_CONNECTED",
            "CALIBRATED",
            "UPDATE_BUSY",
            "LAST_TS",
            "WARNING",
        ):

            self.assertIn(
                f"ca://$(P):{name}",
                self.pvs,
            )

    def test_mode_buttons(self):

        expected = {
            "Mode_Equilibrium":
                "equilibrium",
            "Mode_SSRF":
                "ssRF",
            "Mode_FreqHop":
                "freq_hopping",
            "Mode_Baseline":
                "baseline",
            "Mode_NoNMR":
                "no_nmr",
        }

        for name, value in expected.items():

            action = self.write_action(
                name
            )

            self.assertEqual(
                action.findtext(
                    "pv_name"
                ),
                "ca://$(P):MODE_CMD",
            )

            self.assertEqual(
                action.findtext(
                    "value"
                ),
                value,
            )

    def test_material_buttons(self):

        expected = {
            "Material_Butanol":
                "d-butanol",
            "Material_Propanediol":
                "d-propanediol",
            "Material_ND3":
                "ND3",
        }

        for name, value in expected.items():

            action = self.write_action(
                name
            )

            self.assertEqual(
                action.findtext(
                    "pv_name"
                ),
                "ca://$(P):MATERIAL_CMD",
            )

            self.assertEqual(
                action.findtext(
                    "value"
                ),
                value,
            )

    def test_update_constants_is_momentary_action(self):

        action = self.write_action(
            "UPDATE_CONSTANTS_ACTION"
        )

        self.assertEqual(
            action.findtext(
                "pv_name"
            ),
            (
                "ca://$(P):"
                "UPDATE_CONSTANTS_CMD"
            ),
        )

        self.assertEqual(
            action.findtext(
                "value"
            ),
            "1",
        )

    def test_no_free_text_mode_or_material(self):

        for name in (
            "MODE_CMD",
            "MATERIAL_CMD",
        ):

            matches = [
                widget
                for widget
                in self.widgets
                if (
                    widget.findtext(
                        "name"
                    )
                    == name
                )
            ]

            self.assertEqual(
                matches,
                [],
            )

    def test_larmor_remains_numeric_entry(self):

        widget = self.widget_by_name(
            "LARMOR_KHZ_CMD"
        )

        self.assertEqual(
            widget.attrib.get(
                "type"
            ),
            "textentry",
        )

        self.assertEqual(
            widget.findtext(
                "pv_name"
            ),
            (
                "ca://$(P):"
                "LARMOR_KHZ_CMD"
            ),
        )

    def test_operator_readbacks(self):

        for name in (
            "MODE_RBV",
            "MATERIAL_RBV",
            "LARMOR_KHZ_RBV",
        ):

            self.assertIn(
                f"ca://$(P):{name}",
                self.pvs,
            )

    def test_fit_summary_pvs(self):

        for name in (
            "FIT_TYPE",
            "MODEL",
            "NPTS",
            "P_FIT",
            "P_AREA",
            "Q_FIT",
            "Q_EQ",
            "R",
            "PHASE_DEG",
            "RED_CHISQ",
        ):

            self.assertIn(
                f"ca://$(P):{name}",
                self.pvs,
            )

    def test_constant_pvs(self):

        for name in (
            "CONSTANTS_SOURCE",
            "CC",
            "CC_DAQ",
            "OMEGD",
            "OMEGQ1",
            "OMEGQ2",
            "A1",
            "ETA1",
            "ETA2",
            "K",
            "SCALE",
        ):

            self.assertIn(
                f"ca://$(P):{name}",
                self.pvs,
            )

    def test_xyplot_sources(self):

        plots = {
            widget.findtext("name"): widget
            for widget in self.widgets
            if widget.attrib.get("type") == "xyplot"
        }

        self.assertEqual(
            set(plots),
            {"Incoming_NMR_Plot", "NMR_Fit_Plot"},
        )

        expected = {
            "Incoming_NMR_Plot": {"BASESUB"},
            "NMR_Fit_Plot": {"DATA", "TOTAL_FIT"},
        }

        for name, signals in expected.items():

            traces = plots[name].findall("traces/trace")

            self.assertEqual(
                {
                    trace.findtext("x_pv")
                    for trace in traces
                },
                {"ca://$(P):FREQS_MHZ"},
            )

            self.assertEqual(
                {
                    trace.findtext("y_pv")
                    for trace in traces
                },
                {
                    f"ca://$(P):{signal}"
                    for signal in signals
                },
            )

    def test_polarization_history(self):

        widget = self.widget_by_name("P_Q_History")

        self.assertEqual(
            widget.attrib.get("type"),
            "databrowser",
        )

        self.assertEqual(
            widget.findtext("file"),
            "genesis_ssrf_fitter_pq.plt",
        )

        plot = ET.parse(
            GUI.parent / "genesis_ssrf_fitter_pq.plt"
        ).getroot()

        self.assertEqual(
            {
                pv.findtext("name")
                for pv in plot.findall("pvlist/pv")
            },
            {
                "UNH:GEN:SSRF:FIT:P_FIT",
                "UNH:GEN:SSRF:FIT:Q_FIT",
            },
        )

    def test_no_hardware_execution_pvs(self):

        forbidden = (
            ":WGEN:",
            ":GEN:",
            ":NMR_SW:",
            ":RF_SW:",
            "WAVEFORM_CMD",
            "LOAD_CMD",
            "OUTPUT_CMD",
            "RF_CMD",
            "FM_CMD",
            "ARM_CMD",
            "START_CMD",
        )

        for pv in self.pvs:

            for fragment in forbidden:

                self.assertNotIn(
                    fragment,
                    pv,
                    msg=(
                        "hardware authority "
                        "leaked into fitter GUI: "
                        f"{pv}"
                    ),
                )

    def test_all_pvs_are_pva(self):

        for pv in self.pvs:

            self.assertTrue(
                pv.startswith(
                    "ca://"
                ),
                msg=pv,
            )


if __name__ == "__main__":
    unittest.main()
