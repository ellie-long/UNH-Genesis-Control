from pathlib import Path
import xml.etree.ElementTree as ET
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"


EXPECTED = {
    "genesis_cryo.bob",
    "genesis_cryo_fridge.bob",
    "genesis_cryo_magnet.bob",
    "genesis_cryo_vacuum.bob",
    "genesis_cryo_cell_expert.bob",
    "genesis_cryo_vacuum_expert.bob",
    "genesis_cryo_ion_expert.bob",
    "genesis_cryo_field.bob",
    "genesis_cryo_coldboard.bob",
    "genesis_cryo_safety.bob",
    "genesis_cryo_data.bob",
    "genesis_cryo_flow.bob",
    "genesis_cryo_diagnostics.bob",
}


class CryoGuiContract(unittest.TestCase):

    def test_expected_operator_hierarchy_exists(self):
        for name in EXPECTED:
            self.assertTrue(
                (GUI / name).is_file(),
                name,
            )

    def test_every_bob_parses(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            ET.parse(path)

    def test_main_overview_is_read_only(self):
        root = ET.parse(
            GUI / "genesis_cryo.bob"
        ).getroot()

        self.assertFalse(
            root.findall(
                ".//action[@type='write_pv']"
            )
        )

    def test_cryo_pv_ownership(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            root = ET.parse(path).getroot()

            for node in root.iter(
                "pv_name"
            ):
                pv = (
                    node.text or ""
                ).strip()

                if not pv:
                    continue

                if pv.startswith(
                    "ca://"
                ):
                    pv = pv[5:]

                self.assertTrue(
                    pv.startswith(
                        "UNH:GEN:CRYO:"
                    ),
                    f"{path.name}: {pv}",
                )

    def test_no_rf_nmr_microwave_leak(self):
        forbidden = (
            "UNH:GEN:SSRF",
            "UNH:GEN:MW",
            "Burn_",
            "rf_chain.bob",
            "burn.bob",
        )

        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            text = path.read_text()

            for token in forbidden:
                self.assertNotIn(
                    token,
                    text,
                    path.name,
                )

    def test_navigation_targets_exist(self):
        for path in GUI.glob(
            "genesis_cryo*.bob"
        ):
            root = ET.parse(path).getroot()

            for action in root.findall(
                ".//action[@type='open_display']"
            ):
                target = (
                    action.findtext(
                        "file",
                        "",
                    )
                    .strip()
                )

                if target.startswith(
                    "genesis_cryo"
                ):
                    self.assertTrue(
                        (GUI / target).exists(),
                        f"{path.name} -> {target}",
                    )


if __name__ == "__main__":
    unittest.main()



def test_cryo_gui_rendering_contract():
    """Protect the Phoebus rendering fixes found during physical commissioning."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    repo = Path(__file__).resolve().parents[1]
    gui = repo / "gui"

    bob_paths = sorted(
        gui.glob("genesis_cryo*.bob")
    )

    assert bob_paths

    # "data_browser" is not the Phoebus widget name.
    # Valid embedded plots use "databrowser".
    for path in bob_paths:
        text = path.read_text(
            encoding="utf-8"
        )

        assert 'type="data_browser"' not in text, path

        ET.parse(path)

    vacuum_root = ET.parse(
        gui / "genesis_cryo_vacuum.bob"
    ).getroot()

    data_root = ET.parse(
        gui / "genesis_cryo_data.bob"
    ).getroot()

    assert len(
        vacuum_root.findall(
            ".//widget[@type='databrowser']"
        )
    ) == 1

    assert len(
        data_root.findall(
            ".//widget[@type='databrowser']"
        )
    ) == 2

    # Operator-level numeric/status fields should use the PV's native
    # Channel Access metadata instead of forcing Phoebus string format 6.
    # Archive_Enable is intentionally exempt: live Phoebus acceptance
    # showed that this Boolean/enum field needs format 6 for text rendering.
    native_format_pages = (
        "genesis_cryo.bob",
        "genesis_cryo_vacuum.bob",
        "genesis_cryo_safety.bob",
        "genesis_cryo_data.bob",
        "genesis_cryo_diagnostics.bob",
    )

    for name in native_format_pages:
        root = ET.parse(
            gui / name
        ).getroot()

        bad = []

        for widget in root.findall(
            ".//widget[@type='textupdate']"
        ):
            fmt = widget.findtext("format")
            pv = (
                widget.findtext("pv_name")
                or ""
            )

            allowed_format6 = {
                "ca://UNH:GEN:CRYO:ARCH:Archive_Enable",
            }

            if (
                fmt == "6"
                and pv not in allowed_format6
            ):
                bad.append(
                    (
                        widget.findtext("name"),
                        pv,
                    )
                )

        assert not bad, (
            name,
            bad,
        )

    # Imported instrument pages may retain explicit formats,
    # but every per-channel connection PV must be displayed
    # using its Boolean/enum string rather than numeric format.
    conn_count = 0

    for path in bob_paths:
        root = ET.parse(path).getroot()

        for widget in root.findall(
            ".//widget[@type='textupdate']"
        ):
            pv = (
                widget.findtext("pv_name")
                or ""
            )

            if pv.endswith("_Conn"):
                conn_count += 1

                assert (
                    widget.findtext("format")
                    == "6"
                ), (
                    path.name,
                    pv,
                    widget.findtext("format"),
                )

    assert conn_count > 0

    vacuum_text = (
        gui / "genesis_cryo_vacuum.bob"
    ).read_text(
        encoding="utf-8"
    )

    assert "Torr" in vacuum_text
    assert "mbar" not in vacuum_text

    import yaml
    profile = yaml.safe_load(
        (repo / "configs/target_physical/tpg262/settings.yaml").read_text()
    )["tpg262"]
    assert all(
        profile["records"][pv]["EGU"] == "Torr"
        for pv in ("Pump_PI", "OVC_PI")
    )



def test_cryo_main_navigation_is_unique():
    """Main Cryo overview exposes one canonical route per top-level page."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    repo = Path(__file__).resolve().parents[1]
    root = ET.parse(
        repo / "gui" / "genesis_cryo.bob"
    ).getroot()

    destinations = []

    for action in root.findall(
        ".//action[@type='open_display']"
    ):
        target = (
            action.findtext("file")
            or ""
        ).strip()

        if target:
            destinations.append(target)

    required = [
        "genesis_cryo_fridge.bob",
        "genesis_cryo_magnet.bob",
        "genesis_cryo_field.bob",
        "genesis_cryo_vacuum.bob",
        "genesis_cryo_coldboard.bob",
        "genesis_cryo_safety.bob",
        "genesis_cryo_data.bob",
        "genesis_cryo_diagnostics.bob",
        "genesis_cryo_flow.bob",
        "genesis_cryo_cell_expert.bob",
        "genesis_cryo_ion_expert.bob",
        "genesis_cryo_vacuum_expert.bob",
    ]

    assert sorted(destinations) == sorted(required)
    assert len(destinations) == len(set(destinations))



def test_cryo_gui_headers_and_final_schematic():
    """All Cryo pages belong visually to Genesis and the overview keeps its physical mimic."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    repo = Path(__file__).resolve().parents[1]
    gui = repo / "gui"

    bobs = sorted(
        gui.glob("genesis_cryo*.bob")
    )

    assert len(bobs) == 13

    for path in bobs:
        root = ET.parse(path).getroot()

        header_labels = []

        for widget in root.findall(
            ".//widget[@type='label']"
        ):
            text = (
                widget.findtext("text")
                or ""
            ).strip()

            if text.startswith(
                "UNH Genesis -"
            ):
                header_labels.append(text)

        assert header_labels, (
            path.name,
            "missing UNH Genesis header",
        )

        assert (
            "panopticon"
            not in path.read_text(
                encoding="utf-8"
            ).lower()
        )

    main = ET.parse(
        gui / "genesis_cryo.bob"
    ).getroot()

    names = {
        (
            widget.findtext("name")
            or ""
        )
        for widget in main.findall("widget")
    }

    required = {
        "MagnetCryostat",
        "MagnetCoilLeft",
        "MagnetCoilRight",
        "FridgeInsert",
        "TargetStick",
        "TargetCell",
        "SchematicCryostatLabel",
        "SchematicMagnetLabel",
        "SchematicFridgeLabel",
        "SchematicStickLabel",
        "SchematicCellLabel",
    }

    assert required <= names

    main_text = (
        gui / "genesis_cryo.bob"
    ).read_text(
        encoding="utf-8"
    )

    assert "CRYOSTAT OVERVIEW" in main_text
    assert "MAGNET CRYOSTAT" in main_text
    assert "5 T MAGNET" in main_text
    assert "1 K FRIDGE" in main_text
    assert "TARGET STICK" in main_text
    assert "TARGET CELL" in main_text



def test_cryo_final_operator_acceptance_contract():
    """Protect corrections found during live Phoebus acceptance."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    repo = Path(__file__).resolve().parents[1]
    gui = repo / "gui"

    bobs = sorted(
        gui.glob("genesis_cryo*.bob")
    )

    conn_count = 0

    for path in bobs:
        root = ET.parse(path).getroot()

        assert (
            "all instruments"
            not in path.read_text(
                encoding="utf-8"
            ).lower()
        )

        for widget in root.findall(
            ".//widget[@type='textupdate']"
        ):
            pv = (
                widget.findtext("pv_name")
                or ""
            )

            if pv.endswith("_Conn"):
                conn_count += 1
                assert (
                    widget.findtext("format")
                    == "6"
                ), (
                    path.name,
                    pv,
                    widget.findtext("format"),
                )

    assert conn_count > 0

    main = ET.parse(
        gui / "genesis_cryo.bob"
    ).getroot()

    widgets = {
        (
            widget.findtext("name")
            or ""
        ): widget
        for widget in main.findall("widget")
    }

    for idx in range(6):
        widget = widgets[
            f"Status{idx}V"
        ]

        assert int(
            widget.findtext("width")
        ) <= 105

    for idx in range(4):
        widget = widgets[
            f"Permit{idx}LED"
        ]

        off = widget.find(
            "off_color/color"
        )
        on = widget.find(
            "on_color/color"
        )

        assert off is not None
        assert on is not None

        assert (
            off.get("red"),
            off.get("green"),
            off.get("blue"),
        ) == (
            "200",
            "50",
            "50",
        )

        assert (
            on.get("red"),
            on.get("green"),
            on.get("blue"),
        ) == (
            "30",
            "160",
            "70",
        )

    data = ET.parse(
        gui / "genesis_cryo_data.bob"
    ).getroot()

    enabled = [
        widget
        for widget in data.findall(
            ".//widget[@type='textupdate']"
        )
        if (
            widget.findtext("pv_name")
            or ""
        ).endswith(
            ":ARCH:Archive_Enable"
        )
    ]

    assert len(enabled) == 1
    assert (
        enabled[0].findtext("format")
        == "6"
    )
