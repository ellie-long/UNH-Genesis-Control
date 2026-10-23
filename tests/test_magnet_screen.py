"""gui/genesis_magnet.bob (tools/make_magnet_screen.py) and the Panopticon's magnet card.

Every magnet PV on the screens must be one the magnet IOC serves; every
command that moves the magnet asks first (review issue 14), except HOLD.
"""

import importlib
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from cooldown_sim import phoebus
from cooldown_sim.testing import softioc_stub

ROOT = Path(__file__).resolve().parents[1]
BOB = ROOT / "gui" / "genesis_magnet.bob"
PANOPTICON = ROOT / "gui" / "genesis_panopticon.bob"
SETTINGS = ROOT / "configs" / "magnet_physical" / "ips120" / "settings.yaml"
HIST_SETTINGS = ROOT / "configs" / "target_physical" / "trend_history" / "settings.yaml"


def _generator():
    spec = importlib.util.spec_from_file_location("make_magnet_screen", ROOT / "tools" / "make_magnet_screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def magnet_pvs() -> set[str]:
    """The PVs magnet_ioc.device creates (built by its own code, no supply)."""
    settings = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))["ips120"]
    with softioc_stub.installed():
        mod = importlib.import_module("magnet_ioc.device")
        dev = mod.Device("UNH:GEN:MAG", settings)
        return {f"UNH:GEN:MAG:{name}" for name in dev.pvs}


def history_pvs() -> set[str]:
    cfg = yaml.safe_load(HIST_SETTINGS.read_text(encoding="utf-8"))
    with softioc_stub.installed():
        mod = importlib.import_module("logic_devices.trend_history")
        return set(mod.published_pvs(cfg["general"]["prefix"], cfg["trend_history"]))


def test_committed_screen_is_current():
    assert BOB.read_text(encoding="utf-8") == _generator().build(), \
        "regenerate: python tools/make_magnet_screen.py"


def test_magnet_pvs_on_both_screens_are_served():
    served = magnet_pvs()
    for bob in (BOB, PANOPTICON):
        pvs = set(re.findall(r"ca://(UNH:GEN:MAG:[A-Za-z0-9:_]+)", bob.read_text(encoding="utf-8")))
        assert pvs, bob
        assert not pvs - served, (bob.name, sorted(pvs - served))
    trended = set(re.findall(r"ca://(UNH:GEN:HIST:MAGNET:[A-Za-z0-9_]+)", BOB.read_text(encoding="utf-8")))
    assert trended and trended <= history_pvs()
    # the trend group follows the IOC's PVs
    groups = yaml.safe_load(HIST_SETTINGS.read_text(encoding="utf-8"))["trend_history"]["groups"]
    assert {s["pv"] for s in groups["MAGNET"]["sources"].values()} <= served


def test_moves_ask_first_and_hold_never_does():
    root = ET.parse(BOB).getroot()
    buttons = {}
    for w in root.iter("widget"):
        for a in w.iter("action"):
            if a.get("type") == "write_pv":
                buttons[a.findtext("pv_name").removeprefix("ca://UNH:GEN:MAG:")] = w
    assert set(buttons) == {"RAMP", "HOLD", "HEATER_ON_CMD", "HEATER_OFF_CMD", "MATCH", "OUTAGE"}
    for name, w in buttons.items():
        asks = w.findtext("show_confirm_dialog") == "true"
        assert asks == (name != "HOLD"), name
    # the outage button is the big red one (review issue 19)
    out = buttons["OUTAGE"]
    assert int(out.findtext("width")) >= 400 and int(out.findtext("height")) >= 60
    assert out.find("background_color/color").get("red") == "200"


def test_panopticon_card_is_live_and_links_the_magnet_tab():
    text = PANOPTICON.read_text(encoding="utf-8")
    for pv in ("MAGNET_CURRENT", "FIELD", "NMR_D", "HEATER"):
        assert f"ca://UNH:GEN:MAG:{pv}<" in text, pv
    assert "<file>genesis_magnet.bob</file>" in text
    for f in ET.parse(BOB).getroot().iter("file"):
        assert (BOB.parent / f.text).exists(), f.text
    assert phoebus.LIVE_SCREENS["magnet"] == "genesis_magnet.bob"
