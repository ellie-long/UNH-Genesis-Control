from pathlib import Path
import itertools
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



def write_pvs(path):
    """Include action writes as well as direct PV input widgets."""
    root = parse(path)
    writes = widget_pvs(path, {
        "textentry", "combo", "spinner", "slider", "slide_button",
        "bool_button", "choice_button", "radio", "checkbox",
    })
    writes.update(action.findtext("pv_name") for action in root.iter("action")
                  if action.attrib.get("type") == "write_pv")
    return writes


def open_files(path):
    root = parse(path)

    return {
        node.text
        for node in root.iter("file")
        if node.text
    }


class MicrowaveGUI(unittest.TestCase):

    def test_all_gui_files_are_valid_xml(self):
        for path in GUI.rglob("*.bob"):
            with self.subTest(path=path):
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
            5,
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

    def test_robot_operator_commands_are_bounded_and_explicit(self):
        self.assertEqual(write_pvs(ROBOT), {
            "pva://$(P):" + name for name in (
                "CONTROL_MODE", "SPEED_MODE", "JOG_DISTANCE_CM",
                "X_JOG", "Y_JOG", "ESTOP",
            )
        })
        root = parse(ROBOT)
        jogs = [action for action in root.iter("action")
                if action.findtext("pv_name") in
                {"pva://$(P):X_JOG", "pva://$(P):Y_JOG"}]
        self.assertEqual(len(jogs), 4)
        for axis in ("X", "Y"):
            self.assertEqual(sorted(action.findtext("value") for action in jogs
                                    if action.findtext("pv_name") ==
                                    f"pva://$(P):{axis}_JOG"), ["-1", "1"])
            for action in jogs:
                self.assertEqual(action.attrib["type"], "write_pv")
        inputs = {widget.findtext("name"): widget for widget in root.iter("widget")}
        self.assertEqual(inputs["Speed_Mode"].attrib["type"], "combo")
        self.assertEqual(inputs["Speed_Mode"].findtext("items_from_pv"), "true")
        self.assertEqual(inputs["Jog_Distance_CM"].attrib["type"], "textentry")

    def test_robot_operator_status_is_read_only(self):
        readbacks = widget_pvs(ROBOT, {"textupdate", "led"})
        for name in ("COMM_STATUS", "CONTROL_MODE", "LAST_ERROR", "CONTROLLER_ERROR",
                     "X_CM", "Y_CM", "X_BUSY", "Y_BUSY", "X_POSITION_FRESH",
                     "Y_POSITION_FRESH", "X_MOTOR_ENABLE", "Y_MOTOR_ENABLE"):
            self.assertIn("pva://$(P):" + name, readbacks)
        for pv in write_pvs(ROBOT):
            for forbidden in ("MOVE_REL", "HOME", "ZERO", "MACHINE_", "LIMIT_",
                              "SOFT_LIMIT", "MAX_SPEED", "ACCEL", "MOTOR_ENABLE",
                              "READ_ERROR"):
                self.assertNotIn(forbidden, pv)
        for pv in pv_names(ROBOT):
            for forbidden in ("MOVE_REL", "_HOME", "_ZERO", "MACHINE_SET",
                              "MACHINE_APPLY", "LIMIT_LOW_SET", "LIMIT_HIGH_SET",
                              "LIMIT_ENABLE_SET", "LIMIT_APPLY", "MAX_SPEED", "ACCEL"):
                self.assertNotIn(forbidden, pv)

    def test_robot_jog_rules_require_healthy_epics_and_both_axes_ready(self):
        expected = ["COMM_STATUS", "CONTROL_MODE", "X_BUSY", "Y_BUSY",
                    "X_POSITION_FRESH", "Y_POSITION_FRESH",
                    "X_MOTOR_ENABLE", "Y_MOTOR_ENABLE"]
        jog_widgets = [widget for widget in parse(ROBOT).iter("widget")
                       if widget.findtext("name", "").startswith(("X_Jog_", "Y_Jog_"))]
        self.assertEqual(len(jog_widgets), 4)
        for widget in jog_widgets:
            self.assertEqual(widget.findtext("enabled"), "false")
            rule = widget.find("rules/rule")
            self.assertEqual(rule.attrib["prop_id"], "enabled")
            self.assertEqual(rule.attrib["out_exp"], "false")
            self.assertEqual([pv.text for pv in rule.findall("pv_name")],
                             ["pva://$(P):" + name for name in expected])
            expressions = rule.findall("exp")
            self.assertEqual([exp.findtext("value") for exp in expressions],
                             ["true", "false"])
            # Exercise the serialized Boolean rules across every binary readiness
            # combination, all ownership modes and individual alarm/disconnect states.
            def evaluate(values, severities):
                variables = {f"pv{i}": value for i, value in enumerate(values)}
                variables.update({f"pvSev{i}": value for i, value in enumerate(severities)})
                results = [eval(exp.attrib["bool_exp"].replace("&&", " and ")
                                .replace("!", "not "), {"__builtins__": {}}, variables)
                           for exp in expressions]
                return results

            ready = [1, 1, 0, 0, 1, 1, 1, 1]
            for values in itertools.product((0, 1), repeat=7):
                for mode in (0, 1, 2):
                    state = [values[0], mode, *values[1:]]
                    allowed = state == ready
                    self.assertEqual(evaluate(state, [0] * 8), [allowed, not allowed])
            for index in range(8):
                for severity in (1, 2, 3):
                    alarms = [0] * 8
                    alarms[index] = severity
                    self.assertEqual(evaluate(ready, alarms), [False, True])

    def test_robot_stop_all_is_explicit_and_ungated(self):
        stop = next(widget for widget in parse(ROBOT).iter("widget")
                    if widget.findtext("name") == "ESTOP")
        self.assertEqual(stop.attrib["type"], "action_button")
        self.assertEqual(stop.findtext("text"), "STOP ALL")
        self.assertEqual(stop.findtext("enabled", "true"), "true")
        self.assertIsNone(stop.find("rules"))
        actions = stop.findall("actions/action")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].attrib["type"], "write_pv")
        self.assertEqual(actions[0].findtext("pv_name"), "pva://$(P):ESTOP")
        self.assertEqual(actions[0].findtext("value"), "1")

    def test_robot_ownership_actions_and_status(self):
        root = parse(ROBOT)
        modes = [action.findtext('value') for action in root.iter('action')
                 if action.attrib.get('type') == 'write_pv'
                 and action.findtext('pv_name') == 'pva://$(P):CONTROL_MODE']
        self.assertEqual(modes, ['0', '1', '2'])
        for path, prefix in ((ROBOT, 'pva://$(P):'),
                             (MAIN, 'pva://UNH:GEN:MW:ROBOT:')):
            pvs = widget_pvs(path, {'textupdate', 'led'})
            for name in ('CONTROL_MODE', 'JOYSTICK_CONNECTED', 'MOTION_INHIBITED',
                         'X_BUSY', 'Y_BUSY', 'X_LIMITS', 'Y_LIMITS'):
                self.assertIn(prefix + name, pvs)
        self.assertIn('pva://$(P):JOYSTICK_ACTIVE', pv_names(ROBOT))

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
