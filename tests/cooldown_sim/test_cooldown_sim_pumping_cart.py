"""The pumping cart's IONIVAC screen (gui/genesis_pumping_cart.bob).

Ells (2026-10-22): record the cart's IONIVAC, give it its own screen with a
plot, and keep it off the Panopticon.
"""

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from cooldown_sim import phoebus

ROOT = Path(__file__).resolve().parents[2]
BOB = ROOT / "gui" / "genesis_pumping_cart.bob"
PANOPTICON = ROOT / "gui" / "genesis_panopticon.bob"
HIST_SETTINGS = ROOT / "configs" / "target_physical" / "trend_history" / "settings.yaml"
ARCHIVER = ROOT / "configs" / "target_physical" / "archiver" / "settings.yaml"
CART = "UNH:GEN:CRYO:ION:PUMP"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_screen_is_current():
    gen = _load("make_pumping_cart", ROOT / "tools" / "make_pumping_cart.py")
    assert BOB.read_text(encoding="utf-8") == gen.build(), "regenerate: python tools/make_pumping_cart.py"


def _served():
    """The cart IOC's records and health PVs, and the trend-history PVs."""
    import importlib
    from cooldown_sim import pvlist
    from cooldown_sim.testing import softioc_stub
    cfg = yaml.safe_load((ROOT / "configs/target_physical/ionivac_pump/settings.yaml").read_text())
    served = {f"{CART}:{r}" for r in cfg["ionivac_pump"]["records"]}
    served |= {f"{CART}:{h}" for h in pvlist.HEALTH}
    hist = yaml.safe_load(HIST_SETTINGS.read_text(encoding="utf-8"))
    with softioc_stub.installed():
        th = importlib.import_module("logic_devices.trend_history")
        served |= set(th.published_pvs(hist["general"]["prefix"], hist["trend_history"]))
    return served


def test_screen_pvs_are_served_and_it_is_read_only():
    text = BOB.read_text(encoding="utf-8")
    pvs = set(re.findall(r"ca://([A-Za-z0-9:_]+)", text))
    assert {f"{CART}:Ion_PI", f"{CART}:COMM", "UNH:GEN:HIST:CART:Ion_PI", "UNH:GEN:HIST:CART:TIME"} <= pvs
    assert not pvs - _served(), sorted(pvs - _served())
    root = ET.parse(BOB).getroot()
    inputs = {"textentry", "combo", "spinner", "slider", "bool_button", "slide_button", "choice_button"}
    assert not [w for w in root.iter("widget") if w.get("type") in inputs]
    assert {a.get("type") for a in root.iter("action")} == {"open_display"}


def test_cart_has_a_plot_and_is_recorded():
    root = ET.parse(BOB).getroot()
    plots = [w for w in root.iter("widget") if w.get("type") == "xyplot"]
    assert len(plots) == 1
    ys = [t.findtext("y_pv") for t in plots[0].iter("trace")]
    assert ys == ["ca://UNH:GEN:HIST:CART:Ion_PI"]
    hist = yaml.safe_load(HIST_SETTINGS.read_text(encoding="utf-8"))
    src = hist["trend_history"]["groups"]["CART"]["sources"]["Ion_PI"]
    assert src["pv"] == f"{CART}:Ion_PI"
    assert "127.0.0.1:5129" in hist["general"]["epics_addr_list"]          # the cart's live CA port
    archived = yaml.safe_load(ARCHIVER.read_text(encoding="utf-8"))["archiver"]["explicit_pvs"]
    assert f"{CART}:Ion_PI" in archived


def test_cart_is_not_on_the_panopticon():
    assert CART not in PANOPTICON.read_text(encoding="utf-8")
    assert "HIST:CART" not in PANOPTICON.read_text(encoding="utf-8")


def test_screen_opens_from_the_simulator():
    assert phoebus.LIVE_SCREENS["cart"] == "genesis_pumping_cart.bob"
