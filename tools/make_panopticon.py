#!/usr/bin/env python3
"""Generate gui/genesis_panopticon.bob, the Genesis "Panopticon" overview.

Run:  python tools/make_panopticon.py

One wide, read-only screen modelled on the LabVIEW DAQ's overview: Magnet,
Fridge, Shells and Lab space trends on the left, the target insert drawn in
the centre with every thermometer's reading at its sensor's position, and
NMR, holeburning (ssRF), superconducting magnet, microwaves, EPR and slow
sweep on the right.

Only PVs that Genesis serves appear on the screen (checked by
tests/cooldown_sim/test_cooldown_sim_panopticon.py).  Instruments Genesis
does not read yet are drawn as grey placeholders labelled "not in Genesis
yet", with no PV, in the place the DAQ shows them, so they can be wired
later: each one is a widget named ``Placeholder_<key>``.

Like the main Cryo screen, the overview is read-only: no PV writes, only
links to the subsystem screens, where the controls are.

Trend windows follow the DAQ screen (magnet thermometry 15 min, fridge
thermometry 40 min, pressures 1 h, polarization 3 h, shells vacuum 2 days).
The trends are XY plots of the trend-history IOC's arrays
(logic_devices/trend_history.py, prefix UNH:GEN:HIST), not strip charts: a
strip chart only plots what arrives while the screen is open (Phoebus can
back-fill one only from an archive data source it knows, and the Genesis CSV
archive is not one), so it started blank on every open or reload.  The
history IOC keeps 6 h (shells vacuum 2 days) and back-fills from the CSV
archive at start-up, so the plots are full the moment the screen opens; zoom
out (toolbar) to see more than the DAQ window.  The x axis is time relative
to now in minutes (hours for the shells vacuum), 0 = the newest sample; it is
wall time, so under the simulator at speed N a window spans N times as much
simulated time.

Pressures are all in Torr.  The TPG 262 reads in whatever unit its front
panel is set to (Pump_Unit: mbar, Torr or Pa); the trend-history IOC follows
that unit and converts, so the Roots gauges' read-backs here are its
PRESS:Pump_Torr_NOW / OVC_Torr_NOW, not the raw Pump_PI / OVC_PI.
"""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "gui" / "genesis_panopticon.bob"
#: One screen at 100% zoom: the display area of a maximized Phoebus window on
#: a 1920x1080 monitor (below the menu, toolbar, tab and zoom rows, above the
#: status bar) is about 1910 x 920.
W, H = 1900, 915

CRYO = "UNH:GEN:CRYO"
HIST = "UNH:GEN:HIST"
FIT = "UNH:GEN:SSRF:FIT"
SSRF = "UNH:GEN:SSRF"
MW = "UNH:GEN:MW"
MAG = "UNH:GEN:MAG"

# Genesis palette (tools/make_cryo_screens.py)
NAVY = (0, 53, 145)
NAVY_DARK = (0, 36, 92)
CARD = (245, 248, 252)
CARD_HEADER = (220, 231, 243)
WHITE = (255, 255, 255)
TEXT = (28, 38, 48)
MUTED = (85, 96, 108)
RED = (200, 30, 30)
AMBER = (225, 135, 0)
GREEN_LIGHT = (220, 243, 222)
# placeholders: instruments not read by Genesis yet
GREY_FILL = (230, 230, 230)
GREY_LINE = (165, 165, 165)
GREY_TEXT = (120, 120, 120)
# the insert drawing
DEWAR = (204, 226, 246)
STEEL = (64, 70, 80)
INSERT = (246, 248, 251)
BAFFLE = (240, 140, 30)
FRIDGE = (250, 218, 241)
FRIDGE_LINE = (200, 0, 150)
CUP = (215, 192, 245)
CUP_LINE = (110, 50, 180)
COIL = (250, 205, 40)
COIL_LINE = (170, 130, 0)

NOT_YET = "not in Genesis yet"

#: Trace colour of every thermometer: its readout box in the insert drawing
#: has the same colour.  T1 and T3 (target cup, served by the magnet LS224)
#: are drawn with the fridge, where they sit.
FRIDGE_T = {
    "F1": (178, 34, 34), "F2": (230, 120, 0), "F3": (205, 170, 0), "F4": (0, 128, 0),
    "F5": (0, 70, 200), "F6": (220, 0, 160), "F7": (120, 40, 200), "F8": (90, 90, 90),
    "F9": (140, 70, 20), "F10": (0, 160, 170), "F11": (0, 90, 60),
    "T1": (200, 0, 70), "T3": (40, 40, 140),
}
MAGNET_T = {"M1": (200, 30, 30), "M2": (0, 90, 200), "M3": (0, 140, 60), "M4": (210, 120, 0),
            "M5": (110, 50, 170)}


def thermometer_pv(sensor: str) -> str:
    sub = "FRIDGE" if sensor.startswith("F") else "MAG"
    return f"{CRYO}:{sub}:{sensor}_TI"


def col(rgb):
    r, g, b = rgb
    return f'<color red="{r}" green="{g}" blue="{b}" alpha="255"/>'


def font(size, bold=False, italic=False):
    style = "BOLD_ITALIC" if bold and italic else "BOLD" if bold else "ITALIC" if italic else "REGULAR"
    return f'<font><font family="Liberation Sans" style="{style}" size="{size}.0"/></font>'


def ink(rgb):
    """Readable text colour on a coloured background."""
    r, g, b = rgb
    return TEXT if 0.299 * r + 0.587 * g + 0.114 * b > 150 else WHITE


_n = [0]


def _name(prefix):
    _n[0] += 1
    return f"{prefix}{_n[0]}"


def pvref(pv):
    """PV name as written on the screen: Channel Access, or a formula PV ("=...")."""
    return pv if pv.startswith("=") else f"ca://{pv}"


def total(*pvs):
    """Formula PV: the sum of several read-backs (e.g. Roots L + R flow)."""
    return "=" + "+".join(f"`ca://{pv}`" for pv in pvs)


FLOW_SEP = "UNH:GEN:CRYO:FLOW:SEP:SepFlow_SLM"
FLOW_L = "UNH:GEN:CRYO:FLOW:LEFT:LPump_SLM"
FLOW_R = "UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM"
MAGNET_LEVEL = "UNH:GEN:CRYO:MAG:LHE:Magnet_LI"
FRIDGE_LEVEL = "UNH:GEN:CRYO:FRIDGE:LHE:Fridge_LI"
LHE_BLUE = (0, 110, 210)
FLOW_COLOURS = {"Separator": (0, 110, 210), "Roots L": (230, 110, 0), "Roots R": (120, 40, 200)}


def rect(x, y, w, h, bg, line=None, line_width=1, corner=0, name=None, transparent=False, tooltip=None):
    border = (f"<line_width>{line_width}</line_width><line_color>{col(line)}</line_color>" if line
              else "<line_width>0</line_width>")
    corners = f"<corner_width>{corner}</corner_width><corner_height>{corner}</corner_height>" if corner else ""
    fill = "<transparent>true</transparent>" if transparent else f"<background_color>{col(bg)}</background_color>"
    tip = f"<tooltip>{escape(tooltip)}</tooltip>" if tooltip else ""
    return (f'<widget type="rectangle" version="2.0.0"><name>{name or _name("Rect")}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>{border}{corners}{fill}{tip}</widget>")


def polygon(points, bg, line, line_width=2, name=None):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    pts = "".join(f'<point x="{px - x0}.0" y="{py - y0}.0"/>' for px, py in points)
    return (f'<widget type="polygon" version="2.0.0"><name>{name or _name("Polygon")}</name>'
            f"<x>{x0}</x><y>{y0}</y><width>{max(xs) - x0 + 1}</width><height>{max(ys) - y0 + 1}</height>"
            f"<points>{pts}</points><line_width>{line_width}</line_width><line_color>{col(line)}</line_color>"
            f"<background_color>{col(bg)}</background_color></widget>")


def dashed(x, y, w, rgb, name=None):
    """Horizontal dashed line."""
    return (f'<widget type="polyline" version="2.0.0"><name>{name or _name("Line")}</name>'
            f"<x>{x}</x><y>{y - 1}</y><width>{w}</width><height>3</height>"
            f'<points><point x="0.0" y="1.0"/><point x="{w}.0" y="1.0"/></points>'
            f"<line_width>2</line_width><line_color>{col(rgb)}</line_color><line_style>1</line_style></widget>")


def label(text, x, y, w, h, size=10, bold=False, fg=TEXT, bg=None, align=0, italic=False,
          tooltip=None, name=None, valign=1, wrap=False):
    back = (f"<background_color>{col(bg)}</background_color><transparent>false</transparent>" if bg
            else "<transparent>true</transparent>")
    tip = f"<tooltip>{escape(tooltip)}</tooltip>" if tooltip else ""
    wrapping = "<wrap_words>true</wrap_words>" if wrap else ""
    return (f'<widget type="label" version="2.0.0"><name>{name or _name("Label")}</name>'
            f"<text>{escape(text)}</text><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"{font(size, bold, italic)}<foreground_color>{col(fg)}</foreground_color>{back}"
            f"<horizontal_alignment>{align}</horizontal_alignment>"
            f"<vertical_alignment>{valign}</vertical_alignment>{wrapping}{tip}</widget>")


def update(pv, x, y, w, h, size=11, bold=False, fmt=None, prec=None, units=True, bg=WHITE, fg=TEXT,
           align=0, name=None):
    extra = f"<format>{fmt}</format>" if fmt is not None else ""
    extra += f"<precision>{prec}</precision>" if prec is not None else ""
    extra += "" if units else "<show_units>false</show_units>"
    return (f'<widget type="textupdate" version="2.0.0"><name>{name or _name("Upd")}</name>'
            f"<pv_name>{escape(pvref(pv))}</pv_name><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"{font(size, bold)}<background_color>{col(bg)}</background_color>"
            f"<foreground_color>{col(fg)}</foreground_color>"
            f"<horizontal_alignment>{align}</horizontal_alignment>{extra}</widget>")


def tank(pv, x, y, w, h, lo, hi, name=None):
    """A level bar (Phoebus tank) of ``pv`` on a fixed lo..hi scale."""
    return (f'<widget type="tank" version="2.0.0"><name>{name or _name("Tank")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<scale_visible>false</scale_visible><limits_from_pv>false</limits_from_pv>"
            f"<minimum>{float(lo)}</minimum><maximum>{float(hi)}</maximum>"
            f"<fill_color>{col(LHE_BLUE)}</fill_color><empty_color>{col(GREY_FILL)}</empty_color></widget>")


def led(pv, x, y, size=16):
    return (f'<widget type="led" version="2.0.0"><name>{_name("Led")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{size}</width>"
            f"<height>{size}</height></widget>")


def open_button(text, bob, x, y, w=110, h=28, name=None):
    return (f'<widget type="action_button" version="3.0.0"><name>{name or _name("Open")}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f'<actions><action type="open_display"><file>{escape(bob)}</file><target>tab</target>'
            f"<description>{escape(text)}</description></action></actions>"
            f"<text>{escape(text)}</text></widget>")


def card(title, x, y, w, h, key, link=None):
    """A titled card; link = (button text, .bob) adds a button in its header."""
    out = (rect(x, y, w, h, CARD, CARD_HEADER, name=f"Card_{key}")
           + rect(x, y, w, 26, CARD_HEADER, name=f"Card_{key}_Header")
           + label(title, x + 8, y + 1, w - 16, 24, 13, True, NAVY, name=f"Card_{key}_Title"))
    if link:
        text, bob = link
        bw = 20 + 8 * len(text)
        out += open_button(text, bob, x + w - bw - 4, y + 2, bw, 22, name=f"Card_{key}_Link")
    return out


def field(caption, pv, x, y, w, size=11, prec=None, fmt=None, units=True, bold=True, compact=False):
    """Caption above a read-back."""
    ch, vh = (13, 20) if compact else (15, 21)
    return label(caption, x, y, w, ch, 9, False, MUTED) + \
        update(pv, x, y + ch, w, vh, size, bold, fmt=fmt, prec=prec, units=units)


def inline(caption, pv, x, y, wl, wv, prec=None, fmt=None):
    """Caption left of a read-back, one line."""
    return label(caption, x, y, wl, 21, 9, False, MUTED) + \
        update(pv, x + wl, y, wv, 21, 10, True, fmt=fmt, prec=prec)


def placeholder(key, caption, x, y, w, what, compact=False):
    """A read-back Genesis does not have yet: caption above a grey box, no PV."""
    tip = f"{what}: {NOT_YET}"
    ch, vh = (13, 20) if compact else (15, 21)
    return label(caption, x, y, w, ch, 9, False, GREY_TEXT, tooltip=tip) + \
        label(NOT_YET, x, y + ch, w, vh, 9, False, GREY_TEXT, GREY_FILL, align=1, italic=True,
              tooltip=tip, name=f"Placeholder_{key}")


def placeholder_plot(key, title, x, y, w, h, what):
    """A trend or plot of an instrument Genesis does not read yet."""
    tip = f"{what}: {NOT_YET}"
    return (rect(x, y, w, h, GREY_FILL, GREY_LINE, name=f"Placeholder_{key}")
            + label(title, x + 6, y + 6, w - 12, 34, 10, True, GREY_TEXT, align=1, tooltip=tip, wrap=True)
            + label(NOT_YET, x + 6, y + h // 2 - 12, w - 12, 24, 12, False, GREY_TEXT, align=1, italic=True,
                    tooltip=tip))


def hist(group, key):
    """A trend-history array: UNH:GEN:HIST:<group>:<key> (TIME is the group's time base)."""
    return f"{HIST}:{group}:{key}"


#: time unit of each trend-history group (configs/target_physical/trend_history)
HIST_UNITS = {"MAG": "min", "FRIDGE": "min", "PRESS": "min", "POL": "min", "SHELLS": "h",
              "LEVEL": "min", "FLOW": "min", "CART": "min", "MAGNET": "min"}


def history_plot(key, title, x, y, w, h, group, traces, axes, span):
    """An XY plot of trend-history arrays: x = the group's TIME (relative to
    now, negative, in HIST_UNITS[group]), shown from -span to 0.

    traces: (name, history key, rgb, axis); axes: (title, log, minimum, maximum).
    Linear y axes autoscale.  Log axes keep their fixed range: autoscaling a
    log axis breaks (a 1E-323 lower limit) as soon as a trace holds a 0.
    Unfilled history slots are NaN, so nothing is drawn there."""
    small = '<font family="Liberation Sans" style="REGULAR" size="9.0"/>'
    fonts = f"<title_font>{small}</title_font><scale_font>{small}</scale_font>"
    unit = HIST_UNITS[group]
    xa = (f"<x_axis><title>Time ({unit}, 0 = now)</title><autoscale>false</autoscale>"
          f"<log_scale>false</log_scale><minimum>{-span}.0</minimum><maximum>0.0</maximum>"
          f"<show_grid>true</show_grid>{fonts}<visible>true</visible></x_axis>")
    ax = "".join(
        f"<y_axis><title>{escape(t)}</title><autoscale>{str(not log).lower()}</autoscale>"
        f"<log_scale>{str(log).lower()}</log_scale>"
        f"<minimum>{lo}</minimum><maximum>{hi}</maximum><show_grid>true</show_grid>{fonts}"
        f"<visible>true</visible></y_axis>" for t, log, lo, hi in axes)
    tr = "".join(
        f"<trace><name>{escape(n)}</name><x_pv>ca://{hist(group, 'TIME')}</x_pv>"
        f"<y_pv>ca://{hist(group, k)}</y_pv><axis>{a}</axis><trace_type>1</trace_type>"
        f"<color>{col(rgb)}</color><line_width>2</line_width><point_type>0</point_type>"
        f"<point_size>4</point_size><visible>true</visible></trace>" for n, k, rgb, a in traces)
    return (f'<widget type="xyplot" version="3.0.0"><name>Trend_{key}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<title>{escape(title)}</title>"
            f'<title_font><font family="Liberation Sans" style="BOLD" size="10.0"/></title_font>'
            f"<show_toolbar>true</show_toolbar><show_legend>false</show_legend>"
            f"{xa}<y_axes>{ax}</y_axes><traces>{tr}</traces></widget>")


def key(x, y, items, wid):
    """Colour key under a trend (the legend widget is too large for these charts)."""
    return "".join(label(text, x + i * wid, y, wid, 14, 8, True, rgb) for i, (text, rgb) in enumerate(items))


def nmr_plot(x, y, w, h):
    # The processed NMR signal only.  Points, not lines: Phoebus reads the
    # fitter's waveforms at their full length, so the unused tail (zeros)
    # would draw a line back to 0 MHz.
    def trace(name, pv, rgb, size):
        return (f"<trace><name>{name}</name><x_pv>ca://{FIT}:FREQS_MHZ</x_pv><y_pv>ca://{FIT}:{pv}</y_pv>"
                f"<axis>0</axis><trace_type>0</trace_type><color>{col(rgb)}</color>"
                f"<point_type>1</point_type><point_size>{size}</point_size></trace>")
    return (f'<widget type="xyplot" version="3.0.0"><name>NMR_Signal</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<title></title><show_toolbar>true</show_toolbar><show_legend>false</show_legend>"
            f"<x_axis><title>Frequency (MHz)</title><autoscale>false</autoscale><minimum>31.9</minimum>"
            f"<maximum>33.1</maximum><log_scale>false</log_scale><show_grid>true</show_grid></x_axis>"
            f"<y_axes><y_axis><title>Signal (arb.)</title><autoscale>true</autoscale><minimum>0.0</minimum>"
            f"<maximum>1.0</maximum><log_scale>false</log_scale><show_grid>true</show_grid>"
            f"<on_right>false</on_right></y_axis></y_axes>"
            f"<traces>{trace('processed NMR', 'DATA', RED, 3)}</traces></widget>")


# ------------------------------------------------------------------ panels
def header():
    w = [rect(0, 0, W, 44, NAVY_DARK, name="Header"),
         label("UNH Genesis - Panopticon", 14, 2, 340, 26, 18, True, WHITE, name="Title"),
         label("Polarized-target overview (read-only)", 15, 26, 340, 16, 9, False, WHITE)]
    for i, (cap, pv, wv) in enumerate((("Target state", f"{CRYO}:STATE:production", 120),
                                       ("Safety", f"{CRYO}:SAFE:Safe_State", 120),
                                       ("Alarms", f"{CRYO}:ALARM:Alarm_Count", 60),
                                       ("Worst", f"{CRYO}:ALARM:Alarm_Worst", 100),
                                       ("First alarm", f"{CRYO}:ALARM:Alarm_First", 300))):
        x = 380 + sum((130, 150, 70, 110)[:i])
        if cap == "Safety":
            w.append(led(f"{CRYO}:SAFE:Safe_Permit", x, 21, 18))
            x += 22
        w.append(label(cap, x, 2, wv, 16, 9, False, WHITE))
        w.append(update(pv, x, 19, wv, 22, 10, True))
    for i, (text, bob) in enumerate((("Cryo", "genesis_cryo.bob"), ("Microwave", "genesis_microwave.bob"),
                                     ("ssRF", "genesis_ssrf.bob"), ("NMR fitter", "genesis_ssrf_fitter.bob"),
                                     ("Magnet", "genesis_magnet.bob"))):
        w.append(open_button(text, bob, 1340 + i * 111, 8, 104, 28))
    return w


def magnet_space():
    x, y = 4, 48
    w = [card("Magnet Space", x, y, 610, 232, "Magnet", ("Magnet", "genesis_cryo_magnet.bob"))]
    w.append(history_plot("MagnetLevel", "Magnet LHe level (in)", 12, 80, 160, 192, "LEVEL",
                          [("Magnet LHe", "Magnet_LI", LHE_BLUE, 0)], [("in", False, 0, 31)], 40))
    w.append(history_plot("MagnetThermometry", "Magnet thermometry (K)", 178, 80, 330, 176, "MAG",
                          [(s, f"{s}_TI", c, 0) for s, c in MAGNET_T.items()],
                          [("K", False, 0, 10)], 15))
    w.append(key(196, 258, list(MAGNET_T.items()), 60))
    # magnet LHe level bar: 0-31 inch, alarm at 20", magnet top at 16.5"
    top, h = 100, 136
    w.append(label("Level (in)", 522, 80, 88, 18, 10, True, MUTED))
    w.append(tank(MAGNET_LEVEL, 530, top, 28, h, 0, 31, name="MagnetLevelBar"))
    for inch, rgb, text in ((20.0, RED, '20" alarm'), (16.5, AMBER, '16.5" top')):
        yy = top + h - round(inch / 31.0 * h)
        w.append(rect(526, yy, 36, 2, rgb))
        w.append(label(text, 564, yy - 8, 50, 16, 8, True, rgb))
    w.append(field("Magnet LHe level", MAGNET_LEVEL, 518, 240, 92, 11, prec=1, fmt=1))
    return w


#: live entries carry (format, precision): 1 = decimal, 2 = exponential
FRIDGE_READOUTS = (
    ("live", FRIDGE_LEVEL, "Fridge LHe (in)", (1, 1)),
    ("live", f"{CRYO}:VAC:LOW:Mano_VapT", "Vapor-press. T (K)", (1, 3)),
    ("live", f"{CRYO}:CELL:Cell_PI", "Mano. Hi (Torr)", (2, 3)),
    ("live", f"{CRYO}:VAC:LOW:Mano_PI", "Mano. Lo (Torr)", (2, 3)),
    ("live", f"{HIST}:PRESS:Pump_Torr_NOW", "Roots top (Torr)", (2, 3)),
    ("live", f"{HIST}:PRESS:OVC_Torr_NOW", "Roots bottom (Torr)", (2, 3)),
    ("placeholder", "Pirani", "Pirani", "Pirani gauge"),
    ("live", FLOW_SEP, "Separator flow (slm)", (1, 2)),
    ("live", total(FLOW_L, FLOW_R), "Roots total (slm)", (1, 2)),
    ("live", FLOW_L, "Roots L flow (slm)", (1, 2)),
    ("live", FLOW_R, "Roots R flow (slm)", (1, 2)),
)


def fridge_space():
    x, y = 4, 284
    w = [card("Fridge Space", x, y, 610, 410, "Fridge", ("Fridge", "genesis_cryo_fridge.bob"))]
    w.append(history_plot("Flows", "Flows (slm)", 12, 314, 170, 136, "FLOW",
                          [(n, k, FLOW_COLOURS[n], 0) for n, k in (("Separator", "SepFlow_SLM"),
                                                                    ("Roots L", "LPump_SLM"),
                                                                    ("Roots R", "RPump_SLM"))],
                          [("slm", False, 0, 10)], 60))
    w.append(key(12, 451, [("Sep.", FLOW_COLOURS["Separator"]), ("Roots L", FLOW_COLOURS["Roots L"]),
                           ("Roots R", FLOW_COLOURS["Roots R"])], 56))
    # the history IOC converts the TPG 262 from its display unit to Torr (Pump_Torr, OVC_Torr)
    w.append(history_plot("FridgePressures", "Pressures (Torr, log)", 12, 470, 170, 194, "PRESS",
                          [("Mano. Hi", "Cell_PI", (230, 110, 0), 0),
                           ("Mano. Lo", "Mano_PI", (0, 140, 60), 0),
                           ("Roots top", "Pump_Torr", (220, 0, 160), 0),
                           ("Roots bottom", "OVC_Torr", (120, 40, 200), 0)],
                          [("Torr", True, 0.01, 100)], 60))
    w.append(key(12, 666, [("Mano. Hi", (230, 110, 0)), ("Mano. Lo", (0, 140, 60))], 84))
    w.append(key(12, 679, [("Roots top", (220, 0, 160)), ("Roots bot.", (120, 40, 200))], 84))
    w.append(history_plot("FridgeThermometry", "Fridge thermometry (K, log)", 188, 314, 282, 224, "FRIDGE",
                          [(s, f"{s}_TI", c, 0) for s, c in FRIDGE_T.items()],
                          [("K", True, 0.5, 300)], 40))
    w.append(history_plot("FridgeLevel", "Fridge LHe level (in)", 188, 544, 282, 142, "LEVEL",
                          [("Fridge LHe", "Fridge_LI", FRIDGE_LINE, 0)], [("in", False, 0, 10)], 360))
    for i, (kind, ref, caption, what) in enumerate(FRIDGE_READOUTS):
        yy = 314 + i * 34
        if kind == "live":
            fmt, prec = what
            w.append(field(caption, ref, 476, yy, 132, 10, prec=prec, fmt=fmt,
                           units=not ref.startswith("="), compact=True))
        else:
            w.append(placeholder(ref, caption, 476, yy, 132, what, compact=True))
    return w


def shells_space():
    x, y = 4, 698
    w = [card("Shells Space", x, y, 304, 213, "Shells")]
    w.append(history_plot("ShellsVacuum", "Shells (insulating) vacuum, log", 12, 728, 214, 176, "SHELLS",
                          [("IONIVAC", "Ion_PI", (110, 50, 200), 0)],
                          [("Torr", True, 1e-8, 1e-3)], 48))
    w.append(field("IONIVAC", f"{CRYO}:ION:Ion_PI", 232, 728, 72, 10, prec=2, fmt=2))
    w.append(field("Gauge comm", f"{CRYO}:ION:COMM", 232, 772, 72, 9, bold=False))
    return w


def lab_space():
    x, y = 312, 698
    w = [card("Lab Space", x, y, 302, 213, "Lab")]
    w.append(placeholder_plot("LabTrend", "Lab thermocouples (1 day)", 320, 728, 170, 176,
                              "Lab thermocouples"))
    for i, (key, caption) in enumerate((("BackingPumpL", "Backing pump L"), ("BackingPumpR", "Backing pump R"),
                                        ("VMETemp", "VME temp"), ("SeparatorHeater", "Separator heater"))):
        w.append(placeholder(key, caption, 496, 728 + i * 42, 112, f"Lab thermocouple: {caption}"))
    return w


#: Heat exchanger: two stacks of plates on the fridge column, with the
#: separator can in the gap between them.  The as-built counts and spacer
#: lengths: 5 upper plates on 1.94" spacers, 10 lower plates (6 middle + 4
#: lower) on 0.75" spacers, so the lower stack's pitch is about 2.6 times
#: tighter.  (count, y of the first plate, pitch in px)
UPPER_HX = (5, 414, 26)
LOWER_HX = (10, 700, 10)
#: Fridge parts' diameters (as built, in drawing px at the insert's inch
#: scale, 13.2 px/in): upper HX plates 6.375", middle 6.9" (the first 6 of
#: the lower stack), lower 4.84" (its last 4), pumping (main support) tube
#: 1.75", separator can 4.0" across and 7.0" tall (5.75" body + 2 heads),
#: inside the fridge column, around the pumping tube
UPPER_PLATE_W, MIDDLE_PLATE_W, LOWER_PLATE_W, N_MIDDLE = 84, 91, 64, 6
PUMP_TUBE_W = 23
SEPARATOR = (874, 580, 53, 93)           # x, y, width, height

#: Insert drawing: each thermometer's marker sits at the sensor's physical
#: position (x, y) on the fridge insert or magnet, top to bottom: F1 on the
#: pump-out line, F2 at the flange above the upper heat-exchanger stack, F3
#: (left) and F4 (right) just below that stack, F5 just above the separator
#: can and F6 just below it, F8 ABOVE F7 at the top of the lower stack, F9
#: and F10 in it, F11 just below it at the bottom of the fridge column, then
#: T1 and T3 in the target cup (nose); M1-M5 run down the magnet coil.  The
#: readout boxes stand in a column on the right, joined to their markers by
#: leader lines.
CARTOON_MARKERS = {
    # F1 inside the upper insert, halfway from its wall to the pumping tube;
    # F3 under the lowest upper plate; F5 / F6 on the separator's top / bottom
    "F1": (942, 298), "F2": (873, 403), "F3": (870, 527), "F4": (935, 540), "F5": (893, 579),
    "F6": (913, 673), "F8": (913, 702), "F7": (877, 712), "F9": (891, 745), "F10": (927, 765),
    "F11": (873, 800), "T1": (900, 925), "T3": (900, 1000),
    # magnet sensors, right side of the magnet LHe space: M1 on the highest neck baffle,
    # M2 on the lowest neck baffle (top of the belly), M3 on the magnet-support
    # baffle at 16.5", M4 at the solenoid's top, M5 at its bottom
    "M1": (985, 376), "M2": (985, 579), "M3": (985, 801), "M4": (960, 0), "M5": (960, 0),
}

#: Magnet LHe space (inches on the depth scale, 15 px/in, radii about the
#: insert axis x=900): a 12"-wide bore holds the solenoid up to the 16.5"
#: magnet-support baffle; above it the 16"-wide belly up to 31.5", then the
#: 13"-wide neck with 5 baffles whose spacing grows upward (as built: 31.6,
#: 33.5, 36.4, 40.4, 45.4"), drawn to scale up to the top flange at 51.5".  The solenoid: 4" bore, 7" outside, 0.9-16.0" (its top just
#: under the support baffle).
AXIS_X = 900
BORE_HALF, BELLY_HALF, NECK_HALF = 90, 120, 98
BELLY_IN, NECK_IN = (16.5, 31.5), 31.5
NECK_TOP_IN = 51.5                      # the dewar's top flange; the fridge's top plate sits on it
NECK_BAFFLES_IN = (31.6, 33.5, 36.4, 40.4, 45.4)
SOLENOID_IN = (1.7, 16.3)        # 14.6" long, centred on the magnet centre (9.0")
SOLENOID_ID_HALF, SOLENOID_OD_HALF = 30, 52
COPPER = (184, 115, 51)
READOUT_X, READOUT_GAP = 1026, 24      # sensor label at x, box at x + 22; minimum box spacing
LEADER = (50, 50, 50)

# depth scale for the magnet LHe level: 0" at y=1060, 15 px per inch
#: One inch scale for the fridge and the magnet, fixed by two constraints:
#: the dewar's top flange (51.5" above the LHe space's floor) is the
#: insert's lower dark bar, its HX flange (y 408), and the target cup's
#: centre (y 970) is the magnet's centre, 42.5" below the top flange.
FLANGE_Y, CUP_CENTRE_Y = 408, 970
MAGNET_CENTRE_IN = 51.5 - 42.5
PX_PER_INCH = (CUP_CENTRE_Y - FLANGE_Y) / (51.5 - MAGNET_CENTRE_IN)
LEVEL_Y0 = FLANGE_Y + 51.5 * PX_PER_INCH


def level_y(inches):
    """Screen y of a height on the magnet LHe depth scale."""
    return round(LEVEL_Y0 - inches * PX_PER_INCH)


def neck_baffle_ys():
    """The neck baffles' y, bottom to top, at their true heights."""
    return [level_y(b) for b in NECK_BAFFLES_IN]


def leader(points, rgb=LEADER, name=None):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    pts = "".join(f'<point x="{px - x0}.0" y="{py - y0}.0"/>' for px, py in points)
    return (f'<widget type="polyline" version="2.0.0"><name>{name or _name("Leader")}</name>'
            f"<x>{x0}</x><y>{y0}</y><width>{max(xs) - x0 + 1}</width><height>{max(ys) - y0 + 1}</height>"
            f"<points>{pts}</points><line_width>1</line_width><line_color>{col(rgb)}</line_color></widget>")





#: The insert is drawn in its own coordinates (the numbers above and in
#: _insert_drawing) and placed on the screen scaled by INSERT_SCALE about the
#: point INSERT_SRC -> INSERT_DST, keeping all its proportions.
INSERT_SRC = (726, 176)
INSERT_DST = (622, 152)
INSERT_SCALE = 0.82
MARKER = 9                               # marker square on screen (px)


def to_screen(x, y):
    (sx, sy), (dx, dy), s = INSERT_SRC, INSERT_DST, INSERT_SCALE
    return dx + (x - sx) * s, dy + (y - sy) * s


def level_inches(y):
    """Height on the magnet LHe depth scale (inches) of a SCREEN y."""
    src_y = INSERT_SRC[1] + (y - INSERT_DST[1]) / INSERT_SCALE
    return (LEVEL_Y0 - src_y) / PX_PER_INCH


def _place(widget_xml):
    """Move one drawing widget to the screen: shapes scale, text keeps its size."""
    el = ET.fromstring(widget_xml)
    x, y = float(el.findtext("x")), float(el.findtext("y"))
    nx, ny = to_screen(x, y)
    el.find("x").text, el.find("y").text = str(round(nx)), str(round(ny))
    if el.get("type") in ("rectangle", "polygon", "polyline"):
        for tag in ("width", "height"):
            el.find(tag).text = str(max(1, round(float(el.findtext(tag)) * INSERT_SCALE)))
        for pt in el.iter("point"):
            pt.set("x", f"{float(pt.get('x')) * INSERT_SCALE:.1f}")
            pt.set("y", f"{float(pt.get('y')) * INSERT_SCALE:.1f}")
    return ET.tostring(el, encoding="unicode")


def _box(widget_xml):
    el = ET.fromstring(widget_xml)
    x, y = int(el.findtext("x")), int(el.findtext("y"))
    return el.findtext("name"), (x, y, x + int(el.findtext("width")), y + int(el.findtext("height")))


#: markers that sit on (above) or hang from (below) a drawn part
ATTACH = {"M4": ("top", "Coil_R"), "M5": ("bottom", "Coil_R"), "M1": ("above", "Magnet_Baffle_N5"), "F3": ("below", "Baffle_U5"), "F5": ("above", "Shape_Separator"),
          "F6": ("below", "Shape_Separator"), "M2": ("above", "Magnet_Baffle_N1"),
          "M3": ("above", "Magnet_Baffle_Support")}

#: readout columns (screen), right of the insert: fridge and cup sensors,
#: and the magnet sensors set off further right; leader lines turn at ELBOW
F_COLUMN, M_COLUMN, ELBOW = 902, 926, 884
#: thin dark parts a leader line must not run along (it would vanish)
DARK_PARTS = re.compile(r"^(Baffle_|Magnet_Baffle_|Shape_Separator|Shape_PumpOut|Shape_Flange|Shape_Head_Plate)")


def runs_along(y, x0, x1, boxes):
    """Does a horizontal line at y from x0 to x1 run along or inside a thin dark part?"""
    lo, hi = min(x0, x1), max(x0, x1)
    for name, (bx0, by0, bx1, by1) in boxes.items():
        if not DARK_PARTS.match(name) or min(hi, bx1) - max(lo, bx0) <= 3:
            continue
        if by1 - by0 <= 14:
            if by0 - 1 <= y <= by1 + 1:
                return True
        elif abs(y - by0) <= 2 or abs(y - by1) <= 2:
            return True
    return False


def route(mx, my, elbow, ends, boxes, taken, markers=()):
    """Leader from a marker to its value box: across to the elbow, then to the
    box.  The run across is straight when clear, else a short diagonal step
    first to the nearest clear height (upward first: lines are routed top to
    bottom, so this keeps them in the boxes' order).  Clear means: not along a
    thin dark part, not on a run already taken, not through another marker."""
    def clear(y, x0):
        if runs_along(y, x0, elbow, boxes):
            return False
        if any(abs(y - ty) <= 2 and min(elbow, tx1) - max(x0, tx0) > 0 for ty, tx0, tx1 in taken):
            return False
        return not any(by0 - 1 <= y <= by1 + 1 and min(elbow, bx1) - max(x0, bx0) > 0
                       for bx0, by0, bx1, by1 in markers)

    if clear(my, mx):
        taken.append((my, mx, elbow))
        return [(mx, my), (elbow, my)] + ends
    for d in (-1, -2, -3, -4, -5, -6, -7, -8, 1, 2, 3, 4, 5, 6, -9, -10, 7, 8, 9, 10, -12, 12):
        if clear(my + d, mx + 10):
            taken.append((my + d, mx + 10, elbow))
            return [(mx, my), (mx + 10, my + d), (elbow, my + d)] + ends
    raise ValueError(f"no clear leader route from {mx}, {my}")


def insert_cartoon():
    x, y = 618, 48
    w = [card("Target insert", x, y, 380, 863, "Insert")]
    w.append(label("markers: sensor positions; boxes coloured like the traces", 734, 52, 260, 20, 8, False,
                   MUTED, align=2))
    # target selectors
    w.append(field("Target material", f"{CRYO}:STATE:material", 626, 76, 180, 11))
    w.append(field("Fitter material", f"{FIT}:MATERIAL_RBV", 812, 76, 180, 11))
    w.append(field("Dopant (MW supervisor)", f"{MW}:SUP:Dopant", 626, 112, 180, 11))
    w.append(placeholder("TargetStick", "Target stick", 812, 112, 180, "Target stick selection"))
    drawing = [_place(d) for d in _insert_drawing()]
    w.extend(drawing)
    boxes = dict(_box(d) for d in drawing)
    # thermometers: marker, leader line, readout box (screen coordinates)
    half = MARKER / 2
    marks = {}
    for sensor, (mx, my) in CARTOON_MARKERS.items():
        cx, cy = to_screen(mx, my)
        if sensor in ATTACH:
            side, part = ATTACH[sensor]
            cy = {"above": boxes[part][1] - half, "below": boxes[part][3] + half,
                  "top": boxes[part][1], "bottom": boxes[part][3]}[side]
        marks[sensor] = (round(cx), cy)
    # one column of value boxes in the markers' top-to-bottom order; the
    # magnet sensors' boxes are set off to the right of the fridge's
    # route each line's run across (top to bottom), then stack the value
    # boxes in the order of those runs, so the fan-out to the boxes never crosses
    taken, runs = [], {}
    for sensor in sorted(marks, key=lambda s: marks[s][1]):
        cx, cy = marks[sensor]
        others = [(ox - half, oy - half, ox + half, oy + half) for s, (ox, oy) in marks.items() if s != sensor]
        runs[sensor] = route(cx, round(cy), ELBOW, [], boxes, taken, others)
    tops, last = {}, None
    for sensor in sorted(runs, key=lambda s: (runs[s][-1][1], marks[s][1])):
        y_run = runs[sensor][-1][1]
        top = y_run - 10 if last is None else max(y_run - 10, last + READOUT_GAP)
        tops[sensor] = last = top
    for sensor, (cx, cy) in marks.items():
        rgb = FRIDGE_T.get(sensor) or MAGNET_T[sensor]
        top = tops[sensor]
        col = M_COLUMN if sensor.startswith("M") else F_COLUMN
        # every line fans out between the elbow and the fridge column; the
        # magnet sensors' then run on along their own row to their column
        ends = [(F_COLUMN - 2, top + 10)] + ([(col - 2, top + 10)] if col != F_COLUMN else [])
        w.append(label(sensor, col, top, 20, 20, 9, True, TEXT))
        w.append(update(thermometer_pv(sensor), col + 20, top, 50, 20, 10, True, prec=1, bg=rgb,
                        fg=ink(rgb), align=1, name=f"Insert_{sensor}"))
        w.append(leader(runs[sensor] + ends, name=f"Leader_{sensor}"))
        w.append(rect(round(cx - half), round(cy - half), MARKER, MARKER, rgb, (30, 30, 30),
                      name=f"Marker_{sensor}"))
    return w


def lhe_line(axis_x):
    """The magnet LHe surface: a line across the LHe space at the measured
    level.  Its screen y follows Magnet_LI by a rule (y = A - B * inches, the
    depth scale mapped to the screen); hidden outside 0-31 in or without a
    reading."""
    a_screen = INSERT_DST[1] + (LEVEL_Y0 - INSERT_SRC[1]) * INSERT_SCALE
    b_screen = PX_PER_INCH * INSERT_SCALE
    xml = rect(axis_x - BELLY_HALF, level_y(0), 2 * BELLY_HALF, 3, LHE_BLUE, name="Magnet_LHe_Level",
               tooltip="Magnet LHe level (AMI 135), at its height on the depth scale")
    rules = (f'<rules><rule name="LHe height" prop_id="y" out_exp="true">'
             f'<exp bool_exp="true"><expression>{a_screen:.3f} - {b_screen:.4f}*pv0</expression></exp>'
             f"<pv_name>ca://{MAGNET_LEVEL}</pv_name></rule>"
             f'<rule name="LHe shown" prop_id="visible" out_exp="false">'
             f'<exp bool_exp="pv0 &gt;= 0 &amp;&amp; pv0 &lt;= 31"><value>true</value></exp>'
             f"<pv_name>ca://{MAGNET_LEVEL}</pv_name></rule></rules>")
    return xml.replace("</widget>", f"<visible>false</visible>{rules}</widget>")


def _insert_drawing():
    """The insert, magnet space and depth scale in drawing coordinates."""
    w = []
    # magnet LHe space: bore, belly, neck (one outline), and its depth scale
    belly_top, support_y, bottom = level_y(NECK_IN), level_y(BELLY_IN[0]), level_y(0) + 2
    a, b, n = AXIS_X, belly_top, level_y(NECK_TOP_IN)
    # the dewar's outer wall, light, around the whole LHe space
    w.append(rect(a - BELLY_HALF - 6, n, 2 * BELLY_HALF + 12, bottom + 6 - n, DEWAR, (150, 170, 200),
                  name="Magnet_Dewar_Outer", transparent=True))
    w.append(polygon([(a - NECK_HALF, n), (a - NECK_HALF, b), (a - BELLY_HALF, b), (a - BELLY_HALF, support_y),
                      (a - BORE_HALF, support_y), (a - BORE_HALF, bottom), (a + BORE_HALF, bottom),
                      (a + BORE_HALF, support_y), (a + BELLY_HALF, support_y), (a + BELLY_HALF, b),
                      (a + NECK_HALF, b), (a + NECK_HALF, n)], DEWAR, NAVY, 2, name="Magnet_LHe_Space"))
    w.append(label("magnet LHe", a - BELLY_HALF + 3, b + 3, 62, 12, 8, False, NAVY))
    for inch in range(0, 31, 5):
        yy = level_y(inch)
        w.append(rect(762, yy, 10, 1, NAVY))
        if inch != 20:
            w.append(label(str(inch), 740, yy - 6, 20, 12, 8, False, NAVY, align=2))
    w.append(label("LHe (in)", 722, 420, 44, 14, 8, True, LHE_BLUE, align=2))
    w.append(update(MAGNET_LEVEL, 722, 434, 44, 16, 8, True, fmt=1, prec=1, units=False,
                    fg=LHE_BLUE, align=2, name="Magnet_LHe_Reading"))
    w.append(lhe_line(a))
    alarm_y = level_y(20)
    top_y = support_y
    # solenoid (the nose fits in its bore), from 0.9" to just under the 16.5" support baffle
    s_top, s_bot = level_y(SOLENOID_IN[1]), level_y(SOLENOID_IN[0])
    wall = SOLENOID_OD_HALF - SOLENOID_ID_HALF
    w.append(rect(a - SOLENOID_OD_HALF, s_top, wall, s_bot - s_top, COIL, COIL_LINE, name="Coil_L"))
    w.append(rect(a + SOLENOID_ID_HALF, s_top, wall, s_bot - s_top, COIL, COIL_LINE, name="Coil_R"))
    # insert head, above the dewar: feedthroughs, top plate, pump-out line;
    # the HX flange (the lower dark bar) sits on the dewar's top flange
    for fx in (846, 866, 934, 954):
        w.append(rect(fx, 180, 6, 36, (120, 126, 136)))
    w.append(rect(996, 176, 16, 130, STEEL, name="Shape_PumpOut_Riser"))
    w.append(label("pump-out", 950, 176, 46, 12, 8, False, MUTED))
    w.append(rect(820, 216, 160, 12, STEEL, name="Shape_Head_Plate"))
    w.append(rect(828, 228, 144, 170, INSERT, MUTED, name="Shape_Upper_Insert"))
    w.append(rect(972, 290, 40, 16, STEEL, name="Shape_PumpOut_Line"))
    w.append(rect(812, 398, 176, FLANGE_Y - 398, STEEL, name="Shape_Flange"))
    # fridge column: can, liquid, central tube, the two heat-exchanger stacks
    # (copper tubing staggered between the upper plates), and the separator
    w.append(rect(846, 408, 108, 424, INSERT, MUTED, name="Shape_Fridge_Can"))
    n, y0, pitch = LOWER_HX
    w.append(rect(850, y0 - 4, 100, 830 - y0 + 4, FRIDGE, FRIDGE_LINE, name="Shape_Fridge_Liquid"))
    w.append(rect(AXIS_X - PUMP_TUBE_W // 2, 228, PUMP_TUBE_W, 604, STEEL, name="Shape_Pumping_Tube"))
    n, y0, pitch = UPPER_HX
    for i in range(n - 1):
        for tx in ((864, 898, 932) if i % 2 == 0 else (881, 915)):
            w.append(rect(tx, y0 + i * pitch + 4, 3, pitch - 4, COPPER, name=f"Coil_Tube_{i}_{tx}"))
    for stack, (n, y0, pitch) in (("U", UPPER_HX), ("L", LOWER_HX)):
        for i in range(n):
            pw = UPPER_PLATE_W if stack == "U" else (MIDDLE_PLATE_W if i < N_MIDDLE else LOWER_PLATE_W)
            w.append(rect(AXIS_X - pw // 2, y0 + i * pitch, pw, 4 if stack == "U" else 3, BAFFLE,
                          name=f"Baffle_{stack}{i + 1}"))
    sx, sy, sw, sh = SEPARATOR
    w.append(rect(sx, sy, sw, sh, (165, 171, 181), (70, 76, 86), 2, corner=6, name="Shape_Separator"))
    w.append(label("separator", sx - 6, sy + sh // 2 - 8, sw + 12, 15, 8, True, TEXT, align=1))
    w.append(label("fridge", 952, 812, 40, 14, 8, True, FRIDGE_LINE))
    # magnet-space baffles: 5 in the neck (ring from the wall to the insert),
    # and the magnet-support baffle at 16.5"
    for i, by in enumerate(neck_baffle_ys()):
        inner = 72 if by < 398 else 54          # upper insert above the HX flange, fridge can below
        w.append(rect(a - NECK_HALF + 2, by - 3, NECK_HALF - inner - 2, 3, STEEL,
                      name=f"Magnet_Baffle_N{i + 1}_L"))
        w.append(rect(a + inner, by - 3, NECK_HALF - inner - 2, 3, STEEL, name=f"Magnet_Baffle_N{i + 1}"))
    # the support baffle is the same ring as the neck baffles
    w.append(rect(a - NECK_HALF + 2, support_y - 6, NECK_HALF - 56, 5, STEEL, name="Magnet_Baffle_Support_L"))
    w.append(rect(a + 54, support_y - 6, NECK_HALF - 56, 5, STEEL, name="Magnet_Baffle_Support"))
    # nose (tail) and target cup
    w.append(rect(873, 832, 54, 214, INSERT, MUTED, name="Shape_Nose"))
    w.append(rect(879, 900, 42, 140, CUP, CUP_LINE, 2, name="Target_Cup"))
    w.append(label("cup", 879, 1040, 42, 12, 8, True, CUP_LINE, align=1))
    # the alarm and magnet-top lines go over the coils
    w.append(rect(a - BELLY_HALF, alarm_y, 2 * BELLY_HALF, 2, RED, name="Level_Alarm_Line"))
    w.append(label('20" alarm', 726, alarm_y - 6, 46, 12, 8, True, RED))
    w.append(dashed(a - BELLY_HALF, top_y, 2 * BELLY_HALF, AMBER, name="Magnet_Top_Line"))
    w.append(label('16.5"', 726, top_y - 6, 46, 12, 8, True, AMBER))
    return w


def nmr():
    x, y = 1002, 48
    w = [card("NMR", x, y, 640, 520, "NMR")]
    for i, (cap, pv) in enumerate((("Fitter", "FITTER_UP"), ("Publisher", "PUBLISHER_CONNECTED"),
                                   ("Calibrated", "CALIBRATED"))):
        w.append(led(f"{FIT}:{pv}", 1012 + i * 100, 82, 16))
        w.append(label(cap, 1032 + i * 100, 80, 76, 20, 10))
    w.append(label("Mode", 1316, 80, 40, 20, 10, False, MUTED))
    w.append(update(f"{FIT}:MODE_RBV", 1356, 80, 180, 20, 10, True))
    # the polarization numbers in a column on the left, P area (the DAQ's
    # "Polarization", the area method) as the headline
    cx, cw = 1010, 130
    w.append(label("Polarization (area)", cx, 106, cw, 14, 9, True, MUTED))
    w.append(update(f"{FIT}:P_AREA", cx, 120, 108, 38, 20, True, prec=2, units=False, bg=GREEN_LIGHT,
                    align=2, name="NMR_P"))
    w.append(label("%", cx + 110, 120, 20, 38, 16, True))
    for i, (cap, pv) in enumerate((("P (fit)", "P_FIT"), ("Q (fit)", "Q_FIT"), ("Q (equilibrium)", "Q_EQ"))):
        yy = 170 + i * 50
        w.append(label(cap, cx, yy, cw, 14, 9, True, MUTED))
        w.append(update(f"{FIT}:{pv}", cx, yy + 14, 108, 30, 14, True, prec=2, units=False, align=2))
        w.append(label("%", cx + 110, yy + 14, 20, 30, 12, True))
    w.append(field("red. chi2", f"{FIT}:RED_CHISQ", cx, 324, cw, 10, prec=3))
    w.append(field("Last fit", f"{FIT}:LAST_TS", cx, 364, cw, 9))
    # the processed NMR signal above the polarization history, right of the column
    px, pw = 1146, 490
    w.append(nmr_plot(px, 104, pw, 230))
    w.append(history_plot("Polarization", "P area (green), P fit (grey), Q fit (magenta), Q eq (orange)",
                          px, 338, pw, 226, "POL",
                          [("P area", "P_AREA", (0, 140, 40), 0),
                           ("P fit", "P_FIT", (140, 140, 140), 0),
                           ("Q fit", "Q_FIT", (200, 0, 160), 0),
                           ("Q eq", "Q_EQ", (225, 135, 0), 0)],
                          [("%", False, -60, 60)], 180))
    return w


def holeburning():
    x, y = 1646, 48
    w = [card("Holeburning (ssRF)", x, y, 250, 520, "Holeburning")]
    rows = (("HB frequency", f"{SSRF}:GEN:FREQ_RBV", 4), ("HB level", f"{SSRF}:GEN:LEVEL_RBV", 2),
            ("Generator RF", f"{SSRF}:GEN:RF_RBV", None), ("FM", f"{SSRF}:GEN:FM_RBV", None),
            ("Power sweep armed", f"{SSRF}:GEN:SWEEP_ARM_RBV", None),
            ("WaveGen output", f"{SSRF}:WGEN:OUTPUT_RBV", None),
            ("NMR switch", f"{SSRF}:NMR_SW:STATE_RBV", None), ("HB (RF) switch", f"{SSRF}:RF_SW:STATE_RBV", None),
            ("Interlock", f"{SSRF}:ILK:STATE", None), ("Interlock reason", f"{SSRF}:ILK:REASON", None),
            ("Sequencer", f"{SSRF}:SEQ:STATE", None), ("Generator comm", f"{SSRF}:GEN:COMM", None))
    for i, (cap, pv, prec) in enumerate(rows):
        w.append(field(cap, pv, 1654, 76 + i * 36, 234, 10, prec=prec))
    w.append(open_button("ssRF screen", "genesis_ssrf.bob", 1654, 512, 115, 26))
    w.append(open_button("NMR fitter", "genesis_ssrf_fitter.bob", 1773, 512, 115, 26))
    w.append(label("Burns are run from the ssRF screen.", 1654, 540, 234, 22, 9, False, MUTED))
    return w


def sc_magnet():
    x, y = 1002, 572
    w = [card("Superconducting Magnet", x, y, 445, 166, "SCMagnet", ("Magnet", "genesis_magnet.bob"))]
    # the IPS120-10 supply (magnet_ioc): the magnet's own current (the supply's
    # while driven, the persistent current while persistent) and the field from
    # the NMR-measured equation
    w.append(field("Magnet current (A)", f"{MAG}:MAGNET_CURRENT", 1010, 602, 140, 11, prec=4))
    w.append(field("Field (T)", f"{MAG}:FIELD", 1156, 602, 140, 11, prec=5))
    w.append(field("NMR freq. from field (MHz)", f"{MAG}:NMR_D", 1302, 602, 140, 11, prec=4))
    w.append(field("Switch heater", f"{MAG}:HEATER", 1010, 642, 140, 9))
    w.append(field("Fringe field (LS455)", f"{CRYO}:FIELD:Fringe_FieldT", 1156, 642, 140, 11, prec=6))
    w.append(field("Gaussmeter reading", f"{CRYO}:FIELD:Fringe_Field", 1302, 642, 90, 11, prec=4))
    w.append(update(f"{CRYO}:FIELD:Fringe_Unit", 1394, 657, 48, 21, 10))
    w.append(open_button("Fringe field screen", "genesis_cryo_field.bob", 1156, 688, 140, 24))
    return w


def microwaves():
    x, y = 1451, 572
    w = [card("Microwaves", x, y, 445, 166, "Microwaves", ("Microwave screen", "genesis_microwave.bob"))]
    w.append(field("Output frequency", f"{MW}:SUP:OutputFrequencyRBV", 1459, 602, 140, 11, prec=4))
    w.append(field("Source power", f"{MW}:SRC:MW_PWRI", 1605, 602, 140, 11, prec=1))
    w.append(field("UCA voltage", f"{MW}:PS:VMEAS", 1751, 602, 140, 11, prec=3))
    w.append(field("UCA output", f"{MW}:PS:OUTPUT_RBV", 1459, 642, 140, 11))
    w.append(label("RF on", 1605, 642, 80, 15, 9, False, MUTED))
    w.append(led(f"{MW}:SUP:RFActual", 1605, 660, 18))
    w.append(update(f"{MW}:SUP:RFState", 1627, 657, 118, 21, 11, True))
    w.append(label("RF permissive", 1751, 642, 120, 15, 9, False, MUTED))
    w.append(led(f"{MW}:SUP:RFPermissive", 1751, 660, 18))
    w.append(update(f"{MW}:SUP:RFPermissive", 1773, 657, 118, 21, 11, True))
    w.append(placeholder("SIG", "SIG (V)", 1459, 682, 140, "SIG signal"))
    w.append(label("Magic +/-: on the Microwave screen once the constants are certified", 1605, 690, 286, 36, 8,
                   False, GREY_TEXT, GREY_FILL, italic=True, wrap=True, name="Placeholder_Magic",
                   tooltip="Magic +/- microwave frequencies: not on the read-only overview"))
    return w


def epr():
    x, y = 1002, 742
    w = [card("EPR", x, y, 445, 169, "EPR")]
    w.append(placeholder_plot("EPRSignal", "EPR signal vs field", 1010, 772, 280, 132, "EPR lock-in"))
    for i, (key, cap, what) in enumerate((("EPRLockIn", "Lock-in", "EPR lock-in amplifier"),
                                          ("EPRCoils", "EPR coils", "EPR sweep coils"),
                                          ("EPRFieldSweep", "Field sweep", "EPR field sweep"))):
        w.append(placeholder(key, cap, 1298, 772 + i * 44, 140, what))
    return w


def slowsweep():
    x, y = 1451, 742
    w = [card("Slowsweep", x, y, 445, 169, "Slowsweep")]
    w.append(label(f"DAQ slow sweep (step time, frequency step): {NOT_YET}", 1459, 772, 429, 30, 9, False,
                   GREY_TEXT, GREY_FILL, align=1, italic=True, wrap=True, name="Placeholder_SlowSweep",
                   tooltip=f"DAQ slow sweep: {NOT_YET}"))
    w.append(label("Microwave supervisor software sweep:", 1459, 806, 429, 16, 9, True, MUTED))
    w.append(inline("State", f"{MW}:SUP:SweepState", 1459, 826, 70, 120))
    w.append(inline("Progress", f"{MW}:SUP:SweepProgress", 1459, 850, 70, 120, prec=1))
    w.append(inline("Frequency", f"{MW}:SUP:SweepCurrentFrequency", 1459, 874, 70, 120, prec=4))
    w.append(inline("Step", f"{MW}:SUP:SweepStep", 1680, 826, 60, 140, prec=4))
    w.append(inline("Dwell", f"{MW}:SUP:SweepDwell", 1680, 850, 60, 140, prec=1))
    return w


def build() -> str:
    _n[0] = 0
    w = [rect(0, 0, W, H, WHITE, name="Page")]
    for part in (header, magnet_space, fridge_space, shells_space, lab_space, insert_cartoon, nmr,
                 holeburning, sc_magnet, microwaves, epr, slowsweep):
        w.extend(part())
    body = "\n  ".join(w)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<!-- Generated by tools/make_panopticon.py: edit the generator, not this file. -->\n"
            f'<display version="2.0.0">\n'
            f"  <name>Genesis Panopticon</name>\n  <width>{W}</width>\n  <height>{H}</height>\n"
            f"  <background_color>{col(WHITE)}</background_color>\n  {body}\n</display>\n")


def main():
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
