#!/usr/bin/env python3
"""Generate the Genesis Cryogenic Target Phoebus operator GUI.

Design policy
-------------
* Farooq/UNH target DAQ remains the operational reference.
* Genesis owns PV namespace, subsystem boundaries, navigation and safety UX.
* Main screen is process-oriented and read-only.
* Controls are one level down.
* Farooq's proven device detail/trend screens are preserved underneath.
"""

from pathlib import Path
import os
from xml.sax.saxutils import escape
import re
import shutil
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"

_farooq_source = os.environ.get(
    "GENESIS_FAROOQ_GUI_SOURCE"
)

if not _farooq_source:
    raise SystemExit(
        "Set GENESIS_FAROOQ_GUI_SOURCE to Farooq's "
        "unh_daq_ioc/phoebus directory before regenerating "
        "the imported expert screens."
    )

FAROOQ = Path(
    _farooq_source
).expanduser().resolve()

if not FAROOQ.is_dir():
    raise SystemExit(
        f"Farooq GUI source directory not found: {FAROOQ}"
    )

GUI.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Genesis / UNH visual palette.
# ---------------------------------------------------------------------------

NAVY = (0, 53, 145)
NAVY_DARK = (0, 36, 92)
BLUE_LIGHT = (225, 235, 247)
CARD = (245, 248, 252)
CARD_HEADER = (220, 231, 243)
WHITE = (255, 255, 255)
TEXT = (28, 38, 48)
MUTED = (85, 96, 108)
WARNING = (255, 245, 220)


def col(rgb):
    r, g, b = rgb
    return (
        f'<color red="{r}" green="{g}" '
        f'blue="{b}" alpha="255"/>'
    )


def font(size, bold=False):
    style = "BOLD" if bold else "REGULAR"
    return (
        f'<font><font family="Liberation Sans" '
        f'style="{style}" size="{size}.0"/></font>'
    )


def rect(name, x, y, w, h, bg, radius=0):
    extra = ""
    if radius:
        extra = (
            f"<corner_width>{radius}</corner_width>"
            f"<corner_height>{radius}</corner_height>"
        )
    return f"""
  <widget type="rectangle" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>{h}</height>
    <line_width>0</line_width>
    {extra}
    <background_color>{col(bg)}</background_color>
  </widget>"""


def label(
    name,
    x,
    y,
    w,
    h,
    text,
    size=13,
    bold=False,
    fg=TEXT,
    align=0,
):
    return f"""
  <widget type="label" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>{h}</height>
    <text>{escape(text)}</text>
    <transparent>true</transparent>
    <horizontal_alignment>{align}</horizontal_alignment>
    {font(size, bold)}
    <foreground_color>{col(fg)}</foreground_color>
  </widget>"""


def textupdate(
    name,
    x,
    y,
    w,
    h,
    pv,
    size=13,
    bold=False,
    transparent=False,
    fmt=None,
    precision=None,
):
    transparent_xml = (
        "<transparent>true</transparent>"
        if transparent
        else f"<background_color>{col(WHITE)}</background_color>"
    )

    format_xml = (
        f"<format>{fmt}</format>"
        if fmt is not None
        else ""
    )

    precision_xml = (
        f"<precision>{precision}</precision>"
        if precision is not None
        else ""
    )

    return f"""
  <widget type="textupdate" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>{h}</height>
    <pv_name>ca://{escape(pv)}</pv_name>
    {format_xml}
    {precision_xml}
    {font(size, bold)}
    {transparent_xml}
    <foreground_color>{col(TEXT)}</foreground_color>
  </widget>"""


def led(name, x, y, pv, size=22):
    return f"""
  <widget type="led" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{size}</width><height>{size}</height>
    <pv_name>ca://{escape(pv)}</pv_name>
  </widget>"""


def open_button(name, x, y, w, text, target):
    return f"""
  <widget type="action_button" version="3.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>34</height>
    <actions>
      <action type="open_display">
        <file>{escape(target)}</file>
        <target>tab</target>
        <description>Open {escape(text)}</description>
      </action>
    </actions>
    <text>{escape(text)}</text>
  </widget>"""


def write_button(name, x, y, w, text, pv, value="1"):
    return f"""
  <widget type="action_button" version="3.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>34</height>
    <actions>
      <action type="write_pv">
        <pv_name>ca://{escape(pv)}</pv_name>
        <value>{escape(value)}</value>
        <description>{escape(text)}</description>
      </action>
    </actions>
    <text>{escape(text)}</text>
  </widget>"""


def combo(name, x, y, w, pv):
    return f"""
  <widget type="combo" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>30</height>
    <pv_name>ca://{escape(pv)}</pv_name>
  </widget>"""


def bool_button(name, x, y, w, text, pv):
    return f"""
  <widget type="bool_button" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>32</height>
    <pv_name>ca://{escape(pv)}</pv_name>
    <text>{escape(text)}</text>
  </widget>"""


def data_browser(name, x, y, w, h, filename):
    return f"""
  <widget type="databrowser" version="2.0.0">
    <name>{escape(name)}</name>
    <x>{x}</x><y>{y}</y>
    <width>{w}</width><height>{h}</height>
    <file>{escape(filename)}</file>
    <show_toolbar>true</show_toolbar>
  </widget>"""


def display(name, width, height, widgets):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<display version="2.0.0">\n'
        f'  <name>{escape(name)}</name>\n'
        f'  <width>{width}</width>\n'
        f'  <height>{height}</height>\n'
        + "".join(widgets)
        + "\n</display>\n"
    )


def card(widgets, name, x, y, width, height, title):
    widgets.append(
        rect(
            f"{name}_Card",
            x,
            y,
            width,
            height,
            CARD,
            8,
        )
    )
    widgets.append(
        rect(
            f"{name}_Header",
            x,
            y,
            width,
            38,
            CARD_HEADER,
            8,
        )
    )
    widgets.append(
        label(
            f"{name}_Title",
            x + 14,
            y + 7,
            width - 28,
            24,
            title,
            15,
            True,
        )
    )


# ---------------------------------------------------------------------------
# Farooq -> Genesis PV translation.
# ---------------------------------------------------------------------------

PV_MAP = {
    "UNH:material": "UNH:GEN:CRYO:STATE:material",
    "UNH:production": "UNH:GEN:CRYO:STATE:production",
    "UNH:conditions": "UNH:GEN:CRYO:STATE:conditions",
    "UNH:unmet": "UNH:GEN:CRYO:STATE:unmet",
    "UNH:state_applied": "UNH:GEN:CRYO:STATE:state_applied",
    "UNH:status": "UNH:GEN:CRYO:STATE:status",

    "UNH:CB_Permit": "UNH:GEN:CRYO:SAFE:CB_Permit",
    "UNH:Vac_Permit": "UNH:GEN:CRYO:SAFE:Vac_Permit",
    "UNH:Data_Permit": "UNH:GEN:CRYO:SAFE:Data_Permit",
    "UNH:Safe_Permit": "UNH:GEN:CRYO:SAFE:Safe_Permit",
    "UNH:Safe_State": "UNH:GEN:CRYO:SAFE:Safe_State",
    "UNH:Safe_Fault": "UNH:GEN:CRYO:SAFE:Safe_Fault",
    "UNH:Safe_Reason": "UNH:GEN:CRYO:SAFE:Safe_Reason",
    "UNH:Safe_Trips": "UNH:GEN:CRYO:SAFE:Safe_Trips",
    "UNH:Safe_Stale": "UNH:GEN:CRYO:SAFE:Safe_Stale",
    "UNH:Safe_Enforce": "UNH:GEN:CRYO:SAFE:Safe_Enforce",
    "UNH:Safe_Limits": "UNH:GEN:CRYO:SAFE:Safe_Limits",

    "UNH:Alarm_Count": "UNH:GEN:CRYO:ALARM:Alarm_Count",
    "UNH:Alarm_Major": "UNH:GEN:CRYO:ALARM:Alarm_Major",
    "UNH:Alarm_Worst": "UNH:GEN:CRYO:ALARM:Alarm_Worst",
    "UNH:Alarm_First": "UNH:GEN:CRYO:ALARM:Alarm_First",
    "UNH:Alarm_Latch": "UNH:GEN:CRYO:ALARM:Alarm_Latch",
    "UNH:Alarm_LatchPV": "UNH:GEN:CRYO:ALARM:Alarm_LatchPV",
    "UNH:Alarm_LatchAt": "UNH:GEN:CRYO:ALARM:Alarm_LatchAt",
    "UNH:Alarm_Scanned": "UNH:GEN:CRYO:ALARM:Alarm_Scanned",
    "UNH:Alarm_Missing": "UNH:GEN:CRYO:ALARM:Alarm_Missing",

    "UNH:Archive_Enable": "UNH:GEN:CRYO:ARCH:Archive_Enable",
    "UNH:Archive_PV_Count": "UNH:GEN:CRYO:ARCH:Archive_PV_Count",
    "UNH:Archive_Writes": "UNH:GEN:CRYO:ARCH:Archive_Writes",
    "UNH:Archive_Status": "UNH:GEN:CRYO:ARCH:Archive_Status",

    "UNH:SR_Last": "UNH:GEN:CRYO:SAVE:SR_Last",
    "UNH:SR_Status": "UNH:GEN:CRYO:SAVE:SR_Status",
}

for i in range(1, 12):
    for suffix in ("TI", "RI", "Status", "Conn"):
        PV_MAP[
            f"UNH:F{i}_{suffix}"
        ] = (
            f"UNH:GEN:CRYO:FRIDGE:F{i}_{suffix}"
        )

for i in range(1, 6):
    for suffix in ("TI", "RI", "Status", "Conn"):
        PV_MAP[
            f"UNH:M{i}_{suffix}"
        ] = (
            f"UNH:GEN:CRYO:MAG:M{i}_{suffix}"
        )

for suffix in ("PI", "Status", "IDN", "Conn"):
    PV_MAP[
        f"UNH:Cell_{suffix}"
    ] = (
        f"UNH:GEN:CRYO:CELL:Cell_{suffix}"
    )

for channel in ("Pump", "OVC"):
    for suffix in ("PI", "Status", "Conn"):
        PV_MAP[
            f"UNH:{channel}_{suffix}"
        ] = (
            f"UNH:GEN:CRYO:VAC:{channel}_{suffix}"
        )

for suffix in ("PI", "Raw", "Conn"):
    PV_MAP[
        f"UNH:Ion_{suffix}"
    ] = (
        f"UNH:GEN:CRYO:ION:Ion_{suffix}"
    )

for suffix in (
    "Field",
    "FieldT",
    "Unit",
    "IDN",
    "Conn",
):
    PV_MAP[
        f"UNH:Fringe_{suffix}"
    ] = (
        f"UNH:GEN:CRYO:FIELD:Fringe_{suffix}"
    )

for suffix in (
    "VI",
    "CI",
    "PI",
    "VC",
    "CC",
    "Mode",
    "CVCC",
    "IDN",
    "Conn",
):
    PV_MAP[
        f"UNH:Coldboard_{suffix}"
    ] = (
        f"UNH:GEN:CRYO:COLDBOARD:Coldboard_{suffix}"
    )

PV_MAP.update(
    {
        "UNH:MAN:ls224_fridge_time":
            "UNH:GEN:CRYO:FRIDGE:MAN:ls224_fridge_time",
        "UNH:MAN:ls224_fridge_hb":
            "UNH:GEN:CRYO:FRIDGE:MAN:ls224_fridge_hb",

        "UNH:MAN:ls224_magnet_time":
            "UNH:GEN:CRYO:MAG:MAN:ls224_magnet_time",
        "UNH:MAN:ls224_magnet_hb":
            "UNH:GEN:CRYO:MAG:MAN:ls224_magnet_hb",

        "UNH:MAN:mks902b_time":
            "UNH:GEN:CRYO:CELL:MAN:mks902b_time",
        "UNH:MAN:mks902b_hb":
            "UNH:GEN:CRYO:CELL:MAN:mks902b_hb",

        "UNH:MAN:tpg262_time":
            "UNH:GEN:CRYO:VAC:MAN:tpg262_time",

        "UNH:MAN:ionivac_time":
            "UNH:GEN:CRYO:ION:MAN:ionivac_time",

        "UNH:MAN:ls455_time":
            "UNH:GEN:CRYO:FIELD:MAN:ls455_time",

        "UNH:MAN:siglent_coldboard_time":
            "UNH:GEN:CRYO:COLDBOARD:"
            "MAN:siglent_coldboard_time",
    }
)


def translate(text):
    for old in sorted(
        PV_MAP,
        key=len,
        reverse=True,
    ):
        text = text.replace(
            old,
            PV_MAP[old],
        )
    return text


# ---------------------------------------------------------------------------
# Port Farooq expert/device screens and trends.
# ---------------------------------------------------------------------------

PORTED = {
    "ls224_fridge.bob": "genesis_cryo_fridge.bob",
    "ls224_fridge.plt": "genesis_cryo_fridge.plt",

    "ls224_magnet.bob": "genesis_cryo_magnet.bob",
    "ls224_magnet.plt": "genesis_cryo_magnet.plt",

    "mks902b.bob": "genesis_cryo_cell_expert.bob",
    "mks902b.plt": "genesis_cryo_cell_expert.plt",

    "tpg262.bob": "genesis_cryo_vacuum_expert.bob",
    "tpg262.plt": "genesis_cryo_vacuum_expert.plt",

    "ionivac.bob": "genesis_cryo_ion_expert.bob",
    "ionivac.plt": "genesis_cryo_ion_expert.plt",

    "ls455.bob": "genesis_cryo_field.bob",
    "ls455.plt": "genesis_cryo_field.plt",

    "siglent_coldboard.bob": "genesis_cryo_coldboard.bob",
    "siglent_coldboard.plt": "genesis_cryo_coldboard.plt",
}


def append_device_status(text, prefix, title):
    root = ET.fromstring(text)

    old_height = int(
        float(
            root.findtext("height", "600")
        )
    )

    root.find("height").text = str(
        old_height + 120
    )

    block = f"""
  <widget type="rectangle" version="2.0.0">
    <name>Genesis_Status_Card</name>
    <x>8</x><y>{old_height + 10}</y>
    <width>760</width><height>95</height>
    <line_width>0</line_width>
    <background_color>{col(BLUE_LIGHT)}</background_color>
  </widget>

  <widget type="label" version="2.0.0">
    <name>Genesis_Status_Title</name>
    <x>20</x><y>{old_height + 18}</y>
    <width>180</width><height>22</height>
    <text>Genesis status</text>
    <transparent>true</transparent>
    {font(13, True)}
  </widget>

  <widget type="label" version="2.0.0">
    <name>Genesis_COMM_L</name>
    <x>20</x><y>{old_height + 48}</y>
    <width>65</width><height>22</height>
    <text>COMM:</text>
    <transparent>true</transparent>
    {font(12)}
  </widget>

  <widget type="textupdate" version="2.0.0">
    <name>Genesis_COMM_V</name>
    <x>85</x><y>{old_height + 46}</y>
    <width>85</width><height>25</height>
    <pv_name>ca://{prefix}:COMM</pv_name>
  </widget>

  <widget type="label" version="2.0.0">
    <name>Genesis_WRITE_L</name>
    <x>190</x><y>{old_height + 48}</y>
    <width>75</width><height>22</height>
    <text>Writes:</text>
    <transparent>true</transparent>
    {font(12)}
  </widget>

  <widget type="textupdate" version="2.0.0">
    <name>Genesis_WRITE_V</name>
    <x>265</x><y>{old_height + 46}</y>
    <width>90</width><height>25</height>
    <pv_name>ca://{prefix}:WRITE_ENABLED</pv_name>
  </widget>

  <widget type="label" version="2.0.0">
    <name>Genesis_ERROR_L</name>
    <x>375</x><y>{old_height + 48}</y>
    <width>55</width><height>22</height>
    <text>Error:</text>
    <transparent>true</transparent>
    {font(12)}
  </widget>

  <widget type="textupdate" version="2.0.0">
    <name>Genesis_ERROR_V</name>
    <x>430</x><y>{old_height + 46}</y>
    <width>325</width><height>25</height>
    <pv_name>ca://{prefix}:ERROR</pv_name>
  </widget>

  <widget type="label" version="2.0.0">
    <name>Genesis_LASTOK_L</name>
    <x>20</x><y>{old_height + 77}</y>
    <width>65</width><height>22</height>
    <text>Last OK:</text>
    <transparent>true</transparent>
    {font(12)}
  </widget>

  <widget type="textupdate" version="2.0.0">
    <name>Genesis_LASTOK_V</name>
    <x>85</x><y>{old_height + 75}</y>
    <width>220</width><height>25</height>
    <pv_name>ca://{prefix}:LAST_OK</pv_name>
  </widget>

  <widget type="action_button" version="3.0.0">
    <name>Genesis_Back</name>
    <x>575</x><y>{old_height + 72}</y>
    <width>180</width><height>30</height>
    <actions>
      <action type="open_display">
        <file>genesis_cryo.bob</file>
        <target>tab</target>
        <description>Return to Cryo Overview</description>
      </action>
    </actions>
    <text>&lt; Cryo Overview</text>
  </widget>
"""

    insert = ET.fromstring(
        "<holder>" + block + "</holder>"
    )

    for child in list(insert):
        root.append(child)

    root.find("name").text = title

    return ET.tostring(
        root,
        encoding="unicode",
        xml_declaration=False,
    )


prefixes = {
    "genesis_cryo_fridge.bob":
        ("UNH:GEN:CRYO:FRIDGE", "Genesis Cryo - Fridge"),
    "genesis_cryo_magnet.bob":
        ("UNH:GEN:CRYO:MAG", "Genesis Cryo - Magnet"),
    "genesis_cryo_cell_expert.bob":
        ("UNH:GEN:CRYO:CELL", "Genesis Cryo - Cell Gauge"),
    "genesis_cryo_vacuum_expert.bob":
        ("UNH:GEN:CRYO:VAC", "Genesis Cryo - Pump / OVC"),
    "genesis_cryo_ion_expert.bob":
        ("UNH:GEN:CRYO:ION", "Genesis Cryo - Ion Gauge"),
    "genesis_cryo_field.bob":
        ("UNH:GEN:CRYO:FIELD", "Genesis Cryo - Fringe Field"),
    "genesis_cryo_coldboard.bob":
        ("UNH:GEN:CRYO:COLDBOARD", "Genesis Cryo - Cold Board"),
}

for source_name, output_name in PORTED.items():
    source = FAROOQ / source_name

    if not source.is_file():
        raise SystemExit(
            f"missing Farooq GUI source: {source}"
        )

    text = translate(
        source.read_text(
            encoding="utf-8"
        )
    )

    text = text.replace(
        "<file>main.bob</file>",
        "<file>genesis_cryo.bob</file>",
    )

    for old, new in PORTED.items():
        text = text.replace(
            f"<file>{old}</file>",
            f"<file>{new}</file>",
        )

    if output_name.endswith(".bob"):
        prefix, title = prefixes[output_name]
        text = append_device_status(
            text,
            prefix,
            title,
        )

    (GUI / output_name).write_text(
        text,
        encoding="utf-8",
    )

    print("[PORTED]", output_name)


# ---------------------------------------------------------------------------
# MAIN OPERATOR OVERVIEW
# ---------------------------------------------------------------------------

w = []

w.append(
    rect(
        "Page",
        0, 0, 1560, 1080,
        WHITE,
    )
)

w.append(
    rect(
        "Header",
        0, 0, 1560, 70,
        NAVY_DARK,
    )
)

w += [
    label(
        "Title",
        24, 14, 850, 32,
        "UNH Genesis - Polarized Target / Cryogenics",
        22,
        True,
        WHITE,
    ),
    label(
        "Subtitle",
        25, 44, 900, 20,
        "Shift-worker process overview",
        11,
        False,
        WHITE,
    ),
]

# Persistent status strip.
card(
    w,
    "Status",
    20, 85, 1520, 95,
    "SYSTEM STATUS",
)

status = [
    (
        "Target",
        "UNH:GEN:CRYO:STATE:production",
        40,
        150,
    ),
    (
        "Material",
        "UNH:GEN:CRYO:STATE:material",
        260,
        140,
    ),
    (
        "Safety",
        "UNH:GEN:CRYO:SAFE:Safe_Permit",
        470,
        105,
    ),
    (
        "Alarms",
        "UNH:GEN:CRYO:ALARM:Alarm_Count",
        650,
        100,
    ),
    (
        "Archive",
        "UNH:GEN:CRYO:ARCH:Archive_Status",
        820,
        105,
    ),
    (
        "Missing PVs",
        "UNH:GEN:CRYO:ALARM:Alarm_Missing",
        1010,
        110,
    ),
]

for idx, (title, pv, x, width) in enumerate(status):
    w.append(
        label(
            f"Status{idx}L",
            x, 134, 95, 22,
            title + ":",
            12,
            True,
        )
    )
    w.append(
        textupdate(
            f"Status{idx}V",
            x + 95, 132, width, 26,
            pv,
            12,
            True,
        )
    )

# Magnet card.
card(
    w,
    "Magnet",
    20, 200, 355, 620,
    "MAGNET SPACE",
)

for i in range(1, 6):
    y = 255 + (i - 1) * 52

    name = f"M{i}"

    extra = (
        "  (coil top)"
        if i == 4
        else ""
    )

    w.append(
        label(
            f"{name}L",
            42, y, 115, 26,
            f"{name}{extra}",
            13,
            i == 4,
        )
    )

    w.append(
        textupdate(
            f"{name}V",
            165, y - 2, 145, 29,
            f"UNH:GEN:CRYO:MAG:{name}_TI",
            14,
            i == 4,
        )
    )

    w.append(
        label(
            f"{name}U",
            315, y, 30, 24,
            "K",
            12,
        )
    )

w += [
    label(
        "FieldL",
        42, 545, 115, 26,
        "Fringe field",
        13,
        True,
    ),
    textupdate(
        "FieldV",
        165, 543, 145, 29,
        "UNH:GEN:CRYO:FIELD:Fringe_FieldT",
        14,
        True,
    ),
    label(
        "FieldU",
        315, 545, 30, 24,
        "T",
        12,
    ),
    label(
        "MagCommL",
        42, 600, 115, 24,
        "Magnet IOC:",
        12,
    ),
    led(
        "MagCommLED",
        165, 600,
        "UNH:GEN:CRYO:MAG:COMM",
    ),
    label(
        "FieldCommL",
        42, 640, 115, 24,
        "Field IOC:",
        12,
    ),
    led(
        "FieldCommLED",
        165, 640,
        "UNH:GEN:CRYO:FIELD:COMM",
    ),
]

# Cryostat overview schematic.
card(
    w,
    "Cryostat",
    395, 200, 690, 620,
    "CRYOSTAT OVERVIEW",
)

# Schematic central cryostat.
w += [
    rect(
        "CryostatBody",
        655, 285, 175, 405,
        BLUE_LIGHT,
        18,
    ),
    rect(
        "TargetCell",
        690, 610, 105, 58,
        WARNING,
        12,
    ),
    label(
        "TargetCellLabel",
        695, 626, 95, 24,
        "TARGET CELL",
        12,
        True,
        TEXT,
        1,
    ),
]

# Fridge channels in two organized columns surrounding physical schematic.
for i in range(1, 12):
    if i <= 6:
        x_label = 425
        x_value = 475
        y = 265 + (i - 1) * 55
    else:
        x_label = 855
        x_value = 905
        y = 265 + (i - 7) * 55

    w += [
        label(
            f"F{i}L",
            x_label, y, 48, 24,
            f"F{i}",
            13,
            True,
        ),
        textupdate(
            f"F{i}V",
            x_value, y - 2, 125, 27,
            f"UNH:GEN:CRYO:FRIDGE:F{i}_TI",
            13,
            True,
        ),
        label(
            f"F{i}U",
            x_value + 130, y, 25, 24,
            "K",
            11,
        ),
    ]

# Pressure summary below the cryostat overview.
pressures = [
    (
        "Cell",
        "UNH:GEN:CRYO:CELL:Cell_PI",
        425,
    ),
    (
        "Pump",
        "UNH:GEN:CRYO:VAC:Pump_PI",
        585,
    ),
    (
        "OVC",
        "UNH:GEN:CRYO:VAC:OVC_PI",
        745,
    ),
    (
        "Ion",
        "UNH:GEN:CRYO:ION:Ion_PI",
        905,
    ),
]

for idx, (title, pv, x) in enumerate(pressures):
    w += [
        label(
            f"Pressure{idx}L",
            x, 715, 80, 22,
            title,
            12,
            True,
        ),
        textupdate(
            f"Pressure{idx}V",
            x, 740, 135, 27,
            pv,
            12,
            True,
        ),
    ]

w += [
    label(
        "PressureUnits",
        425, 776, 450, 20,
        "Pressure readbacks",
        11,
        False,
        MUTED,
    ),
]

# Right operations/status card.
card(
    w,
    "Operations",
    1105, 200, 435, 620,
    "SAFETY / ALARMS / COLD BOARD",
)

ops = [
    (
        "CB permit",
        "UNH:GEN:CRYO:SAFE:CB_Permit",
        True,
    ),
    (
        "Vacuum permit",
        "UNH:GEN:CRYO:SAFE:Vac_Permit",
        True,
    ),
    (
        "Data permit",
        "UNH:GEN:CRYO:SAFE:Data_Permit",
        True,
    ),
    (
        "Overall safe",
        "UNH:GEN:CRYO:SAFE:Safe_Permit",
        True,
    ),
]

for idx, (title, pv, use_led) in enumerate(ops):
    y = 255 + idx * 42
    w.append(
        label(
            f"Permit{idx}L",
            1128, y, 125, 24,
            title + ":",
            12,
            True,
        )
    )

    if use_led:
        w.append(
            led(
                f"Permit{idx}LED",
                1260, y,
                pv,
                22,
            )
        )

    w.append(
        textupdate(
            f"Permit{idx}V",
            1292, y - 1, 105, 26,
            pv,
            12,
            True,
        )
    )

w += [
    label(
        "ReasonL",
        1128, 425, 115, 24,
        "Safety reason:",
        12,
        True,
    ),
    textupdate(
        "ReasonV",
        1128, 450, 385, 30,
        "UNH:GEN:CRYO:SAFE:Safe_Reason",
        11,
        True,
    ),

    label(
        "AlarmCountL",
        1128, 500, 110, 24,
        "Alarms:",
        12,
        True,
    ),
    textupdate(
        "AlarmCountV",
        1240, 498, 80, 27,
        "UNH:GEN:CRYO:ALARM:Alarm_Count",
        13,
        True,
    ),

    label(
        "AlarmWorstL",
        1330, 500, 75, 24,
        "Worst:",
        12,
        True,
    ),
    textupdate(
        "AlarmWorstV",
        1400, 498, 110, 27,
        "UNH:GEN:CRYO:ALARM:Alarm_Worst",
        12,
        True,
    ),

    label(
        "AlarmFirstL",
        1128, 538, 110, 24,
        "First alarm:",
        12,
    ),
    textupdate(
        "AlarmFirstV",
        1240, 536, 270, 27,
        "UNH:GEN:CRYO:ALARM:Alarm_First",
        11,
    ),

    label(
        "ColdBoardHeader",
        1128, 588, 180, 24,
        "Cold board",
        14,
        True,
    ),
]

cb_rows = [
    (
        "Voltage",
        "UNH:GEN:CRYO:COLDBOARD:Coldboard_VI",
        "V",
    ),
    (
        "Current",
        "UNH:GEN:CRYO:COLDBOARD:Coldboard_CI",
        "A",
    ),
    (
        "Power",
        "UNH:GEN:CRYO:COLDBOARD:Coldboard_PI",
        "W",
    ),
    (
        "Output",
        "UNH:GEN:CRYO:COLDBOARD:Coldboard_Mode",
        "",
    ),
]

for idx, (title, pv, unit) in enumerate(cb_rows):
    y = 620 + idx * 35

    w += [
        label(
            f"CB{idx}L",
            1128, y, 80, 22,
            title + ":",
            11,
        ),
        textupdate(
            f"CB{idx}V",
            1210, y - 2, 135, 25,
            pv,
            12,
            True,
        ),
        label(
            f"CB{idx}U",
            1350, y, 35, 22,
            unit,
            11,
        ),
    ]

w += [
]

# Navigation bar.
card(
    w,
    "Navigation",
    20, 845, 1520, 190,
    "OPERATOR NAVIGATION",
)

nav = [
    ("Fridge", "genesis_cryo_fridge.bob"),
    ("Magnet", "genesis_cryo_magnet.bob"),
    ("Field", "genesis_cryo_field.bob"),
    ("Vacuum", "genesis_cryo_vacuum.bob"),
    ("Cold Board", "genesis_cryo_coldboard.bob"),
    ("Safety / State", "genesis_cryo_safety.bob"),
    ("Data / Trends", "genesis_cryo_data.bob"),
    ("Diagnostics", "genesis_cryo_diagnostics.bob"),
]

x = 42
for idx, (title, target) in enumerate(nav):
    width = 165
    w.append(
        open_button(
            f"Nav{idx}",
            x, 905, width,
            title,
            target,
        )
    )
    x += width + 12

w += [
    label(
        "OperatorNote",
        42, 965, 1450, 42,
        (
            "Overview is intentionally read-only. "
            "Open each detail page once, then use its existing Phoebus tab "
            "to switch views and preserve the live trend buffer. "
            "Hardware controls remain subject to Genesis write gates "
            "and target safety interlocks."
        ),
        11,
        False,
        MUTED,
    )
]

(GUI / "genesis_cryo.bob").write_text(
    display(
        "Genesis Cryogenic Target",
        1560,
        1080,
        w,
    ),
    encoding="utf-8",
)

print("[CREATED] genesis_cryo.bob")


# ---------------------------------------------------------------------------
# VACUUM / PRESSURE OPERATOR PAGE
# ---------------------------------------------------------------------------

w = [
    rect(
        "Page",
        0, 0, 1180, 850,
        WHITE,
    ),
    rect(
        "Header",
        0, 0, 1180, 70,
        NAVY_DARK,
    ),
    label(
        "Title",
        24, 18, 750, 30,
        "UNH Genesis - Vacuum / Pressure",
        22,
        True,
        WHITE,
    ),
]

card(
    w,
    "Pressure",
    20, 95, 1140, 260,
    "PRESSURE MONITORING",
)

vac_rows = [
    (
        "Target cell",
        "UNH:GEN:CRYO:CELL:Cell_PI",
        "UNH:GEN:CRYO:CELL:COMM",
        "Torr",
    ),
    (
        "Roots / Pump",
        "UNH:GEN:CRYO:VAC:Pump_PI",
        "UNH:GEN:CRYO:VAC:COMM",
        "mbar",
    ),
    (
        "OVC",
        "UNH:GEN:CRYO:VAC:OVC_PI",
        "UNH:GEN:CRYO:VAC:COMM",
        "mbar",
    ),
    (
        "Ion gauge",
        "UNH:GEN:CRYO:ION:Ion_PI",
        "UNH:GEN:CRYO:ION:COMM",
        "Torr",
    ),
]

for idx, (title, pv, comm, unit) in enumerate(vac_rows):
    y = 150 + idx * 45

    w += [
        label(
            f"Vac{idx}L",
            45, y, 150, 24,
            title,
            13,
            True,
        ),
        textupdate(
            f"Vac{idx}V",
            205, y - 2, 180, 28,
            pv,
            13,
            True,
        ),
        label(
            f"Vac{idx}U",
            395, y, 55, 24,
            unit,
            12,
        ),
        led(
            f"Vac{idx}LED",
            475, y,
            comm,
            22,
        ),
        textupdate(
            f"Vac{idx}COMM",
            510, y - 2, 100, 27,
            comm,
            12,
        ),
    ]

w += [
    open_button(
        "CellExpert",
        670, 150, 210,
        "Cell Gauge Expert",
        "genesis_cryo_cell_expert.bob",
    ),
    open_button(
        "VacExpert",
        670, 195, 210,
        "Pump / OVC Expert",
        "genesis_cryo_vacuum_expert.bob",
    ),
    open_button(
        "IonExpert",
        670, 240, 210,
        "Ion Gauge Expert",
        "genesis_cryo_ion_expert.bob",
    ),
]

card(
    w,
    "VacTrend",
    20, 380, 1140, 380,
    "PRESSURE TREND",
)

w.append(
    data_browser(
        "VacuumTrend",
        40, 430, 1100, 300,
        "genesis_cryo_vacuum.plt",
    )
)

w.append(
    open_button(
        "Back",
        930, 790, 210,
        "< Cryo Overview",
        "genesis_cryo.bob",
    )
)

(GUI / "genesis_cryo_vacuum.bob").write_text(
    display(
        "Genesis Cryo Vacuum",
        1180,
        850,
        w,
    ),
    encoding="utf-8",
)

# Use Farooq's pressure mimic trend as the combined vacuum plot.
source_pressure_plot = FAROOQ / "mimic_pressure.plt"

if source_pressure_plot.is_file():
    text = translate(
        source_pressure_plot.read_text(
            encoding="utf-8"
        )
    )

    (GUI / "genesis_cryo_vacuum.plt").write_text(
        text,
        encoding="utf-8",
    )
else:
    # Fallback to TPG262 trend if the combined plot is unavailable.
    shutil.copy2(
        GUI / "genesis_cryo_vacuum_expert.plt",
        GUI / "genesis_cryo_vacuum.plt",
    )

print("[CREATED] genesis_cryo_vacuum.bob/.plt")


# ---------------------------------------------------------------------------
# SAFETY / ALARM / TARGET STATE OPERATOR PAGE
# ---------------------------------------------------------------------------

w = [
    rect(
        "Page",
        0, 0, 1280, 940,
        WHITE,
    ),
    rect(
        "Header",
        0, 0, 1280, 70,
        NAVY_DARK,
    ),
    label(
        "Title",
        24, 18, 900, 30,
        "UNH Genesis - Target State / Safety / Alarms",
        22,
        True,
        WHITE,
    ),
]

card(
    w,
    "Safety",
    20, 95, 600, 420,
    "SAFETY INTERLOCK",
)

safe_rows = [
    ("Cold-board permit", "CB_Permit"),
    ("Vacuum permit", "Vac_Permit"),
    ("Fresh-data permit", "Data_Permit"),
    ("Overall permit", "Safe_Permit"),
    ("Safety state", "Safe_State"),
    ("Fault latch", "Safe_Fault"),
    ("Trip count", "Safe_Trips"),
    ("Stale inputs", "Safe_Stale"),
    ("Enforcement", "Safe_Enforce"),
    ("Limits", "Safe_Limits"),
]

for idx, (title, suffix) in enumerate(safe_rows):
    y = 150 + idx * 31

    w += [
        label(
            f"Safe{idx}L",
            42, y, 155, 22,
            title + ":",
            11,
            idx < 4,
        ),
        textupdate(
            f"Safe{idx}V",
            202, y - 2, 170, 25,
            f"UNH:GEN:CRYO:SAFE:{suffix}",
            11,
            idx < 4,
        ),
    ]

w += [
    label(
        "ReasonL",
        42, 465, 110, 22,
        "Reason:",
        11,
        True,
    ),
    textupdate(
        "ReasonV",
        110, 462, 475, 27,
        "UNH:GEN:CRYO:SAFE:Safe_Reason",
        11,
        True,
    ),
    write_button(
        "ResetSafe",
        390, 150, 190,
        "Reset Safety Latch",
        "UNH:GEN:CRYO:SAFE:Safe_Reset",
    ),
]

card(
    w,
    "Alarm",
    640, 95, 620, 420,
    "ALARM SUMMARY",
)

alarm_rows = [
    ("Current alarms", "Alarm_Count"),
    ("Major / invalid", "Alarm_Major"),
    ("Worst severity", "Alarm_Worst"),
    ("First worst PV", "Alarm_First"),
    ("Latched severity", "Alarm_Latch"),
    ("Latched PV", "Alarm_LatchPV"),
    ("Latched at", "Alarm_LatchAt"),
    ("PVs scanned", "Alarm_Scanned"),
    ("PVs missing", "Alarm_Missing"),
]

for idx, (title, suffix) in enumerate(alarm_rows):
    y = 150 + idx * 34

    w += [
        label(
            f"Alarm{idx}L",
            662, y, 145, 22,
            title + ":",
            11,
            idx < 3,
        ),
        textupdate(
            f"Alarm{idx}V",
            812, y - 2, 420, 25,
            f"UNH:GEN:CRYO:ALARM:{suffix}",
            11,
            idx < 3,
        ),
    ]

w.append(
    write_button(
        "AckAlarm",
        662, 465, 210,
        "Acknowledge Alarm",
        "UNH:GEN:CRYO:ALARM:Alarm_Ack",
    )
)

card(
    w,
    "State",
    20, 540, 820, 320,
    "TARGET OPERATING STATE",
)

w += [
    label(
        "RequestedL",
        42, 600, 130, 24,
        "Requested state:",
        12,
        True,
    ),
    textupdate(
        "Requested",
        180, 596, 220, 30,
        "UNH:GEN:CRYO:STATE:status",
        12,
        True,
    ),

    label(
        "MaterialL",
        430, 600, 90, 24,
        "Material:",
        12,
        True,
    ),
    textupdate(
        "Material",
        525, 596, 220, 30,
        "UNH:GEN:CRYO:STATE:material",
        12,
        True,
    ),

    label(
        "AutoL",
        42, 650, 130, 24,
        "Auto advance:",
        12,
    ),
    textupdate(
        "Auto",
        180, 646, 120, 32,
        "UNH:GEN:CRYO:STATE:auto",
        12,
        True,
    ),

    label(
        "ProductionL",
        42, 705, 130, 24,
        "Satisfied state:",
        12,
        True,
    ),
    textupdate(
        "Production",
        180, 703, 220, 27,
        "UNH:GEN:CRYO:STATE:production",
        12,
        True,
    ),

    label(
        "ConditionsL",
        430, 705, 110, 24,
        "Conditions:",
        12,
    ),
    textupdate(
        "Conditions",
        545, 703, 115, 27,
        "UNH:GEN:CRYO:STATE:conditions",
        12,
    ),

    label(
        "UnmetL",
        42, 755, 130, 24,
        "First unmet:",
        12,
    ),
    textupdate(
        "Unmet",
        180, 753, 565, 27,
        "UNH:GEN:CRYO:STATE:unmet",
        11,
    ),
]

card(
    w,
    "Snapshot",
    860, 540, 400, 320,
    "SAVE / RESTORE STATUS",
)

w += [
    label(
        "SRLastL",
        882, 600, 90, 24,
        "Last save:",
        12,
    ),
    textupdate(
        "SRLast",
        975, 598, 250, 27,
        "UNH:GEN:CRYO:SAVE:SR_Last",
        11,
    ),
    label(
        "SRStatusL",
        882, 645, 90, 24,
        "Status:",
        12,
    ),
    textupdate(
        "SRStatus",
        975, 643, 250, 27,
        "UNH:GEN:CRYO:SAVE:SR_Status",
        11,
    ),
    write_button(
        "SRSave",
        882, 705, 160,
        "Save Snapshot",
        "UNH:GEN:CRYO:SAVE:SR_Save",
    ),
]

w.append(
    open_button(
        "Back",
        1040, 885, 200,
        "< Cryo Overview",
        "genesis_cryo.bob",
    )
)

(GUI / "genesis_cryo_safety.bob").write_text(
    display(
        "Genesis Cryo Safety",
        1280,
        940,
        w,
    ),
    encoding="utf-8",
)

print("[CREATED] genesis_cryo_safety.bob")


# ---------------------------------------------------------------------------
# DATA / TREND PAGE
# ---------------------------------------------------------------------------

w = [
    rect(
        "Page",
        0, 0, 1280, 900,
        WHITE,
    ),
    rect(
        "Header",
        0, 0, 1280, 70,
        NAVY_DARK,
    ),
    label(
        "Title",
        24, 18, 900, 30,
        "UNH Genesis - Cryo Data / Trends",
        22,
        True,
        WHITE,
    ),
]

card(
    w,
    "Archive",
    20, 95, 1240, 180,
    "ARCHIVER / LOGGER",
)

archive_rows = [
    ("Status", "Archive_Status"),
    ("PV count", "Archive_PV_Count"),
    ("Rows written", "Archive_Writes"),
    ("Enabled", "Archive_Enable"),
]

for idx, (title, suffix) in enumerate(archive_rows):
    x = 45 + idx * 285

    w += [
        label(
            f"Arch{idx}L",
            x, 150, 100, 22,
            title + ":",
            11,
            True,
        ),
        textupdate(
            f"Arch{idx}V",
            x + 100, 148, 145, 27,
            f"UNH:GEN:CRYO:ARCH:{suffix}",
            12,
            True,
        ),
    ]

w += [
    bool_button(
        "ArchiveEnable",
        45, 205, 170,
        "Archive Enable",
        "UNH:GEN:CRYO:ARCH:Archive_Enable",
    ),
    label(
        "ViewerInfo",
        250, 208, 950, 24,
        (
            "Combined historical viewer: "
            "archive/start_archive_viewer.sh "
            "(Genesis Streamlit viewer, default port 8501)"
        ),
        11,
        False,
        MUTED,
    ),
]

card(
    w,
    "Trend",
    20, 300, 1240, 490,
    "QUICK TRENDS",
)

w.append(
    data_browser(
        "FridgeTrend",
        40, 350, 585, 390,
        "genesis_cryo_fridge.plt",
    )
)

w.append(
    data_browser(
        "VacuumTrend",
        650, 350, 585, 390,
        "genesis_cryo_vacuum.plt",
    )
)

w += [
    open_button(
        "MagTrend",
        40, 810, 190,
        "Magnet Detail",
        "genesis_cryo_magnet.bob",
    ),
    open_button(
        "FieldTrend",
        245, 810, 190,
        "Field Detail",
        "genesis_cryo_field.bob",
    ),
    open_button(
        "Back",
        1040, 810, 195,
        "< Cryo Overview",
        "genesis_cryo.bob",
    ),
]

(GUI / "genesis_cryo_data.bob").write_text(
    display(
        "Genesis Cryo Data",
        1280,
        900,
        w,
    ),
    encoding="utf-8",
)

print("[CREATED] genesis_cryo_data.bob")


# ---------------------------------------------------------------------------
# IOC / ENGINEERING DIAGNOSTICS
# ---------------------------------------------------------------------------

w = [
    rect(
        "Page",
        0, 0, 1180, 920,
        WHITE,
    ),
    rect(
        "Header",
        0, 0, 1180, 70,
        NAVY_DARK,
    ),
    label(
        "Title",
        24, 18, 850, 30,
        "UNH Genesis - Cryo Diagnostics",
        22,
        True,
        WHITE,
    ),
]

card(
    w,
    "Physical",
    20, 95, 1140, 455,
    "PHYSICAL IOCs",
)

physical = [
    (
        "Cold Board",
        "UNH:GEN:CRYO:COLDBOARD",
        "UNH:GEN:CRYO:COLDBOARD:"
        "MAN:siglent_coldboard_time",
    ),
    (
        "Cell Gauge",
        "UNH:GEN:CRYO:CELL",
        "UNH:GEN:CRYO:CELL:MAN:mks902b_time",
    ),
    (
        "Pump / OVC",
        "UNH:GEN:CRYO:VAC",
        "UNH:GEN:CRYO:VAC:MAN:tpg262_time",
    ),
    (
        "Ion Gauge",
        "UNH:GEN:CRYO:ION",
        "UNH:GEN:CRYO:ION:MAN:ionivac_time",
    ),
    (
        "Fringe Field",
        "UNH:GEN:CRYO:FIELD",
        "UNH:GEN:CRYO:FIELD:MAN:ls455_time",
    ),
    (
        "Fridge LS224",
        "UNH:GEN:CRYO:FRIDGE",
        "UNH:GEN:CRYO:FRIDGE:MAN:ls224_fridge_time",
    ),
    (
        "Magnet LS224",
        "UNH:GEN:CRYO:MAG",
        "UNH:GEN:CRYO:MAG:MAN:ls224_magnet_time",
    ),
]

headers = [
    ("Device", 42, 165),
    ("COMM", 245, 110),
    ("Writes", 375, 110),
    ("Last OK", 505, 190),
    ("Heartbeat", 715, 190),
    ("Error", 920, 200),
]

for text, x, width in headers:
    w.append(
        label(
            f"Hdr{text}",
            x, 145, width, 22,
            text,
            11,
            True,
        )
    )

for idx, (title, prefix, heartbeat) in enumerate(physical):
    y = 180 + idx * 48

    w += [
        label(
            f"Dev{idx}L",
            42, y, 180, 24,
            title,
            12,
            True,
        ),
        led(
            f"Dev{idx}LED",
            250, y,
            f"{prefix}:COMM",
            22,
        ),
        textupdate(
            f"Dev{idx}COMM",
            280, y - 2, 80, 27,
            f"{prefix}:COMM",
            11,
        ),
        textupdate(
            f"Dev{idx}WRITE",
            375, y - 2, 105, 27,
            f"{prefix}:WRITE_ENABLED",
            11,
        ),
        textupdate(
            f"Dev{idx}LAST",
            505, y - 2, 185, 27,
            f"{prefix}:LAST_OK",
            10,
        ),
        textupdate(
            f"Dev{idx}HB",
            715, y - 2, 185, 27,
            heartbeat,
            10,
        ),
        textupdate(
            f"Dev{idx}ERR",
            920, y - 2, 210, 27,
            f"{prefix}:ERROR",
            10,
        ),
    ]

card(
    w,
    "Logic",
    20, 575, 1140, 250,
    "LOGIC / DATA IOCs",
)

logic = [
    (
        "Alarm Summary",
        "UNH:GEN:CRYO:ALARM:MAN:alarm_summary_time",
    ),
    (
        "Safety Interlock",
        "UNH:GEN:CRYO:SAFE:MAN:safety_interlock_time",
    ),
    (
        "Target State",
        "UNH:GEN:CRYO:STATE:MAN:target_status_time",
    ),
    (
        "Save / Restore",
        "UNH:GEN:CRYO:SAVE:MAN:save_restore_time",
    ),
    (
        "Archiver",
        "UNH:GEN:CRYO:ARCH:MAN:archiver_time",
    ),
]

for idx, (title, heartbeat) in enumerate(logic):
    col_index = idx % 2
    row_index = idx // 2

    x = 42 + col_index * 550
    y = 635 + row_index * 55

    w += [
        label(
            f"Logic{idx}L",
            x, y, 165, 24,
            title,
            12,
            True,
        ),
        label(
            f"Logic{idx}HBL",
            x + 175, y, 35, 24,
            "HB:",
            11,
        ),
        textupdate(
            f"Logic{idx}HB",
            x + 215, y - 2, 285, 27,
            heartbeat,
            10,
        ),
    ]

w.append(
    open_button(
        "Back",
        940, 855, 200,
        "< Cryo Overview",
        "genesis_cryo.bob",
    )
)

(GUI / "genesis_cryo_diagnostics.bob").write_text(
    display(
        "Genesis Cryo Diagnostics",
        1180,
        920,
        w,
    ),
    encoding="utf-8",
)

print("[CREATED] genesis_cryo_diagnostics.bob")


# ---------------------------------------------------------------------------
# Validate generated BOB suite.
# ---------------------------------------------------------------------------


# Normalize connection indicators imported from legacy instrument pages.
#
# *_Conn records are boolIn PVs with No/Yes enum metadata.  The imported
# pages force numeric format 1, which is unnecessary and rendered poorly
# during physical commissioning.  Remove that explicit format and let
# Phoebus use the PV's native Channel Access metadata.
import re as _re

_conn_pattern = _re.compile(
    r'(<pv_name>ca://[^<]+_Conn</pv_name>)\s*<format>1</format>'
)

_conn_changes = 0

for _bob in sorted(GUI.glob("genesis_cryo*.bob")):
    _text = _bob.read_text(encoding="utf-8")

    _updated, _count = _conn_pattern.subn(
        r'\1',
        _text,
    )

    if _count:
        _bob.write_text(
            _updated,
            encoding="utf-8",
        )
        _conn_changes += _count

print(
    f"[NORMALIZED] {_conn_changes} per-channel connection widgets"
)




# GENESIS_FINAL_CRYO_GUI_POSTPROCESS_V1
#
# Final Genesis visual normalization for imported Cryo pages and
# operator-level cryostat overview.
import copy as _copy
import xml.etree.ElementTree as _ET


def _set_xml_text(node, tag, value):
    child = node.find(tag)

    if child is None:
        child = _ET.SubElement(node, tag)

    child.text = str(value)


def _named_widget(root, name):
    for widget in root.findall("widget"):
        if (widget.findtext("name") or "") == name:
            return widget

    return None


# ------------------------------------------------------------------
# Consistent UNH Genesis headers on every imported subsystem screen.
#
# Genesis-native operator pages already contain their own headers.
# Imported Farooq screens are recreated on every generation, so this
# shift is deterministic and does not accumulate.
# ------------------------------------------------------------------

_IMPORTED_HEADERS = {
    "genesis_cryo_fridge.bob":
        "UNH Genesis - Fridge Thermometry",

    "genesis_cryo_magnet.bob":
        "UNH Genesis - Magnet Thermometry",

    "genesis_cryo_cell_expert.bob":
        "UNH Genesis - Target Cell Gauge",

    "genesis_cryo_vacuum_expert.bob":
        "UNH Genesis - Pump / OVC Expert",

    "genesis_cryo_ion_expert.bob":
        "UNH Genesis - Ion Gauge",

    "genesis_cryo_field.bob":
        "UNH Genesis - Fringe Field",

    "genesis_cryo_coldboard.bob":
        "UNH Genesis - Cold Board",
}

_HEADER_HEIGHT = 64

for _filename, _title in _IMPORTED_HEADERS.items():
    _path = GUI / _filename
    _tree = _ET.parse(_path)
    _root = _tree.getroot()

    _already_has_header = any(
        (
            widget.findtext("text")
            or ""
        ).startswith("UNH Genesis -")
        for widget in _root.findall("widget")
    )

    if _already_has_header:
        continue

    # Shift existing top-level content down.
    for _widget in _root.findall("widget"):
        _y = _widget.find("y")

        if _y is None:
            continue

        try:
            _y.text = str(
                int(float(_y.text))
                + _HEADER_HEIGHT
            )
        except (TypeError, ValueError):
            pass

    # Increase display height by the same amount.
    _height = _root.find("height")

    if _height is not None:
        try:
            _height.text = str(
                int(float(_height.text))
                + _HEADER_HEIGHT
            )
        except (TypeError, ValueError):
            pass

    _width = int(
        float(
            _root.findtext("width")
            or 1180
        )
    )

    _header_bg = _ET.fromstring(
        rect(
            "UNH_Header",
            0,
            0,
            _width,
            _HEADER_HEIGHT,
            NAVY_DARK,
        )
    )

    _header_title = _ET.fromstring(
        label(
            "UNH_Header_Title",
            18,
            15,
            _width - 36,
            32,
            _title,
            20,
            True,
            WHITE,
        )
    )

    _root.append(_header_bg)
    _root.append(_header_title)

    _ET.indent(
        _tree,
        space="  ",
    )

    _tree.write(
        _path,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"[HEADER] {_filename}: {_title}"
    )


# ------------------------------------------------------------------
# Final main-page cryostat schematic.
#
# The old central rectangle is intentionally replaced by a compact
# operator mimic of the physical stack:
#
#   magnet cryostat / LHe space
#       -> 5 T coil region
#       -> 1 K refrigerator insert
#       -> target stick
#       -> target cell in the magnetic sweet spot
#
# Existing thermometry and pressure readbacks surrounding this region
# remain untouched.
# ------------------------------------------------------------------

_main_path = GUI / "genesis_cryo.bob"
_main_tree = _ET.parse(_main_path)
_main_root = _main_tree.getroot()

_body_template = _named_widget(
    _main_root,
    "CryostatBody",
)

_cell_template = _named_widget(
    _main_root,
    "TargetCell",
)

_label_template = _named_widget(
    _main_root,
    "TargetCellLabel",
)

if (
    _body_template is None
    or _cell_template is None
    or _label_template is None
):
    raise RuntimeError(
        "Could not locate original CryostatBody / "
        "TargetCell schematic templates"
    )


def _clone_widget(
    template,
    name,
    x,
    y,
    width,
    height,
):
    widget = _copy.deepcopy(template)

    _set_xml_text(
        widget,
        "name",
        name,
    )

    _set_xml_text(
        widget,
        "x",
        x,
    )

    _set_xml_text(
        widget,
        "y",
        y,
    )

    _set_xml_text(
        widget,
        "width",
        width,
    )

    _set_xml_text(
        widget,
        "height",
        height,
    )

    return widget


def _clone_label(
    name,
    text,
    x,
    y,
    width,
    height=22,
):
    widget = _clone_widget(
        _label_template,
        name,
        x,
        y,
        width,
        height,
    )

    _set_xml_text(
        widget,
        "text",
        text,
    )

    return widget


# Remove both the previous simple mimic and any earlier iteration of
# this final schematic so the operation remains idempotent.
_schematic_names = {
    "CryostatBody",
    "TargetCell",
    "TargetCellLabel",

    "MagnetCryostat",
    "MagnetCoilLeft",
    "MagnetCoilRight",
    "FridgeInsert",
    "TargetStick",

    "SchematicCryostatLabel",
    "SchematicMagnetLabel",
    "SchematicFridgeLabel",
    "SchematicStickLabel",
    "SchematicCellLabel",
}

for _widget in list(
    _main_root.findall("widget")
):
    if (
        _widget.findtext("name")
        or ""
    ) in _schematic_names:
        _main_root.remove(
            _widget
        )


# Outer magnet cryostat / LHe region.
_outer = _clone_widget(
    _body_template,
    "MagnetCryostat",
    650,
    280,
    185,
    410,
)

# Two visual coil packs surrounding the central bore.
_coil_left = _clone_widget(
    _cell_template,
    "MagnetCoilLeft",
    662,
    405,
    24,
    165,
)

_coil_right = _clone_widget(
    _cell_template,
    "MagnetCoilRight",
    799,
    405,
    24,
    165,
)

# Refrigerator insert: white internal vertical volume.
_fridge = _ET.fromstring(
    rect(
        "FridgeInsert",
        704,
        325,
        77,
        278,
        WHITE,
    )
)

# Target stick: narrow central member entering from above.
_stick = _ET.fromstring(
    rect(
        "TargetStick",
        737,
        300,
        12,
        325,
        NAVY_DARK,
    )
)

# Target cell: positioned in the lower magnet/fridge sweet spot.
_target_cell = _clone_widget(
    _cell_template,
    "TargetCell",
    701,
    595,
    84,
    62,
)

_shapes = (
    _outer,
    _coil_left,
    _coil_right,
    _fridge,
    _stick,
    _target_cell,
)

for _widget in _shapes:
    _main_root.append(
        _widget
    )


_labels = (
    _clone_label(
        "SchematicCryostatLabel",
        "MAGNET CRYOSTAT",
        660,
        288,
        165,
    ),

    _clone_label(
        "SchematicStickLabel",
        "TARGET STICK",
        688,
        340,
        145,
    ),

    _clone_label(
        "SchematicFridgeLabel",
        "1 K FRIDGE",
        699,
        455,
        90,
    ),

    _clone_label(
        "SchematicMagnetLabel",
        "5 T MAGNET",
        690,
        535,
        105,
    ),

    _clone_label(
        "SchematicCellLabel",
        "TARGET CELL",
        696,
        614,
        95,
    ),
)

for _widget in _labels:
    _main_root.append(
        _widget
    )


_ET.indent(
    _main_tree,
    space="  ",
)

_main_tree.write(
    _main_path,
    encoding="utf-8",
    xml_declaration=True,
)

print(
    "[SCHEMATIC] installed final cryostat / magnet / "
    "fridge / target-stick overview"
)


# Generated XML must remain source-control clean.
# Template interpolation intentionally leaves some whitespace-only lines;
# normalize all generated BOB files once before final validation.
for _bob in sorted(GUI.glob("genesis_cryo*.bob")):
    _lines = _bob.read_text(
        encoding="utf-8"
    ).splitlines()

    _bob.write_text(
        "\n".join(
            _line.rstrip()
            for _line in _lines
        )
        + "\n",
        encoding="utf-8",
    )

print("[NORMALIZED] generated Cryo BOB whitespace")


expected_bob = {
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
    "genesis_cryo_diagnostics.bob",
}

for name in sorted(expected_bob):
    path = GUI / name

    if not path.is_file():
        raise SystemExit(
            f"missing generated GUI: {path}"
        )

    ET.parse(path)

print(
    "[PASS] all generated Cryo BOB files parse as XML"
)

for path in sorted(
    GUI.glob("genesis_cryo*.bob")
):
    root = ET.parse(path).getroot()

    for pv_node in root.iter("pv_name"):
        pv = (pv_node.text or "").strip()

        if not pv:
            continue

        raw = (
            pv[5:]
            if pv.startswith("ca://")
            else pv
        )

        if not raw.startswith(
            "UNH:GEN:CRYO:"
        ):
            raise SystemExit(
                f"non-Cryo PV in {path.name}: {pv}"
            )

    text = path.read_text(
        encoding="utf-8"
    )

    forbidden = (
        "UNH:GEN:SSRF",
        "UNH:GEN:MW",
        "Burn_",
        "Gen_Freq",
        "Trig_",
        "NMR_Connected",
        "SSRF_Connected",
        "rf_chain.bob",
        "burn.bob",
    )

    found = [
        token
        for token in forbidden
        if token in text
    ]

    if found:
        raise SystemExit(
            f"foreign subsystem content in "
            f"{path.name}: {found}"
        )

# Main operator screen must be read-only.
main = ET.parse(
    GUI / "genesis_cryo.bob"
).getroot()

write_actions = main.findall(
    ".//action[@type='write_pv']"
)

if write_actions:
    raise SystemExit(
        "main Cryo overview contains write actions"
    )

print(
    "[PASS] main operator overview has zero write actions"
)

# Every BOB navigation target generated here must exist.
for path in sorted(
    GUI.glob("genesis_cryo*.bob")
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

        if (
            target.startswith(
                "genesis_cryo"
            )
            and not (
                GUI / target
            ).exists()
        ):
            raise SystemExit(
                f"broken navigation: "
                f"{path.name} -> {target}"
            )

print(
    "[PASS] Cryo BOB navigation targets exist"
)

print(
    "[DONE] Genesis Cryo GUI suite generated"
)
