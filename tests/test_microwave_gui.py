from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"

MAIN = GUI / "genesis_microwave.bob"
SUP = GUI / "genesis_mw_supervisor.bob"
UCA = GUI / "genesis_mw_spd1305x.bob"
VAUNIX = GUI / "genesis_mw_vaunix.bob"
ROBOT = GUI / "genesis_mw_robot.bob"


def parse(path):
    return ET.parse(path).getroot()


def pv_names(path):
    root = parse(path)

    return {
        node.text
        for node in root.iter("pv_name")
        if node.text
    }


def widget_pvs(path, kinds):
    root = parse(path)
    result = set()

    for widget in root.iter("widget"):
        if widget.attrib.get("type") not in kinds:
            continue

        node = widget.find("pv_name")

        if node is not None and node.text:
            result.add(node.text)

    return result


def open_files(path):
    root = parse(path)

    return {
        node.text
        for node in root.iter("file")
        if node.text
    }


class MicrowaveGUI(unittest.TestCase):

    def test_all_gui_files_are_valid_xml(self):
        for path in (
            MAIN,
            SUP,
            UCA,
            VAUNIX,
            ROBOT,
        ):
            parse(path)

    def test_main_uses_genesis_1180_layout(self):
        root = parse(MAIN)

        self.assertEqual(
            root.findtext("width"),
            "1180",
        )

        self.assertEqual(
            root.findtext("name"),
            "Genesis Microwave Subsystem",
        )

    def test_uca_detail_uses_genesis_layout(self):
        root = parse(UCA)

        self.assertEqual(
            root.findtext("width"),
            "1040",
        )

        pvs = pv_names(UCA)

        for pv in (
            "pva://$(P):COMM",
            "pva://$(P):VMEAS",
            "pva://$(P):IMEAS",
            "pva://$(P):PMEAS",
            "pva://$(P):OUTPUT_CMD",
            "pva://$(P):VSET_CMD",
            "pva://$(P):ISET_CMD",
            "pva://$(P):OVP_CMD",
            "pva://$(P):OCP_CMD",
            "pva://$(P):OUTPUT_RBV",
            "pva://$(P):VSET_RBV",
            "pva://$(P):ISET_RBV",
            "pva://$(P):OVP_RBV",
            "pva://$(P):OCP_RBV",
        ):
            self.assertIn(
                pv,
                pvs,
            )


    def test_main_explicitly_displays_rf_state_text(self):
        root = parse(MAIN)

        match = None

        for widget in root.iter("widget"):
            if widget.findtext("name") == "SUP_RF_StateText":
                match = widget
                break

        self.assertIsNotNone(match)

        self.assertEqual(
            match.findtext("pv_name"),
            "pva://UNH:GEN:MW:SUP:RFState",
        )

    def test_supervisor_rf_column_is_below_header_band(self):
        root = parse(SUP)

        positions = {}

        for widget in root.iter("widget"):
            name = widget.findtext("name")

            if name in {
                "Freq_CMD_H",
                "Freq_RBV_H",
            }:
                positions[name] = int(
                    widget.findtext("y")
                )

        self.assertGreaterEqual(
            positions["Freq_CMD_H"],
            355,
        )

        self.assertGreaterEqual(
            positions["Freq_RBV_H"],
            355,
        )

    def test_uca_detail_is_compact_and_aligned(self):
        root = parse(UCA)

        self.assertEqual(
            root.findtext("height"),
            "870",
        )

        widgets = {
            widget.findtext("name"): widget
            for widget in root.iter("widget")
            if widget.findtext("name")
        }

        self.assertEqual(
            widgets["Measurement_Card"].findtext("height"),
            "260",
        )

        self.assertEqual(
            widgets["Control_Card"].findtext("height"),
            "260",
        )

        self.assertEqual(
            widgets["ISET_RBV_V"].findtext("y"),
            "513",
        )

        self.assertEqual(
            widgets["OCP_CMD_V"].findtext("y"),
            "512",
        )


    def test_main_navigation_opens_in_same_phoebus_window(self):
        root = parse(MAIN)

        targets = [
            action.findtext("target")
            for action in root.iter("action")
            if action.attrib.get("type") == "open_display"
        ]

        self.assertEqual(
            len(targets),
            4,
        )

        self.assertEqual(
            set(targets),
            {"tab"},
        )


    def test_main_navigation(self):
        files = open_files(MAIN)

        self.assertIn(
            "genesis_mw_supervisor.bob",
            files,
        )
        self.assertIn(
            "genesis_mw_spd1305x.bob",
            files,
        )
        self.assertIn(
            "genesis_mw_vaunix.bob",
            files,
        )
        self.assertIn(
            "genesis_mw_robot.bob",
            files,
        )

    def test_main_has_all_four_ioc_heartbeats(self):
        pvs = pv_names(MAIN)

        for pv in (
            "pva://UNH:GEN:MAN:mw_spd1305x_hb",
            "pva://UNH:GEN:MAN:mw_vaunix_lms_hb",
            "pva://UNH:GEN:MAN:mw_supervisor_hb",
            "pva://UNH:GEN:MAN:mw_pcl602_hb",
        ):
            self.assertIn(pv, pvs)

    def test_normal_frequency_and_rf_use_supervisor(self):
        writes = widget_pvs(
            MAIN,
            {
                "textentry",
                "slide_button",
                "bool_button",
            },
        )

        self.assertIn(
            "pva://UNH:GEN:MW:SUP:OutputFrequency",
            writes,
        )

        self.assertIn(
            "pva://UNH:GEN:MW:SUP:RFEnable",
            writes,
        )

        self.assertNotIn(
            "pva://UNH:GEN:MW:SRC:MW_FC",
            writes,
        )

        self.assertNotIn(
            "pva://UNH:GEN:MW:SRC:MW_RF",
            writes,
        )

    def test_vaunix_detail_is_diagnostic_only(self):
        writes = widget_pvs(
            VAUNIX,
            {
                "textentry",
                "slide_button",
                "bool_button",
            },
        )

        forbidden = {
            "pva://$(P):MW_FC",
            "pva://$(P):MW_PWRC",
            "pva://$(P):MW_RF",
            "pva://$(P):MW_SweepRun",
        }

        self.assertTrue(
            forbidden.isdisjoint(writes)
        )

    def test_robot_detail_exposes_stop_but_not_motion(self):
        writes = widget_pvs(
            ROBOT,
            {
                "textentry",
                "slide_button",
                "bool_button",
            },
        )

        self.assertIn(
            "pva://$(P):ESTOP",
            writes,
        )

        forbidden_fragments = (
            "MOVE_REL",
            "_JOG",
            "MOTOR_ENABLE",
            "_HOME",
            "_ZERO",
        )

        for pv in writes:
            for fragment in forbidden_fragments:
                self.assertNotIn(
                    fragment,
                    pv,
                )

    def test_supervisor_detail_contains_safety_interface(self):
        pvs = pv_names(SUP)

        for pv in (
            "pva://$(P):RFPermissive",
            "pva://$(P):InterlockMessage",
            "pva://$(P):RFEnable",
            "pva://$(P):OutputFrequency",
            "pva://$(P):OutputFrequencyRBV",
            "pva://$(P):SweepStartCommand",
            "pva://$(P):SweepStopCommand",
        ):
            self.assertIn(pv, pvs)


if __name__ == "__main__":
    unittest.main()
