#!/usr/bin/env python3
"""Generate cooldown_sim/gui/genesis_sim_control.bob (the Sim Control screen).

Run:  python -m cooldown_sim.gui.make_sim_control

The screen lives in cooldown_sim/gui/, not gui/: the live operator screens
directory is scanned by live regression tests, and nothing sim-specific may
appear among the live displays.  It opens the unchanged live screens through
relative links.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from cooldown_sim.sim_pvs import AUTOPILOT_LEGS, FAULT_INSTRUMENTS

OUT = Path(__file__).with_name("genesis_sim_control.bob")
P = "UNH:GEN:SIM"
W, H = 1400, 940

NAVY = (0, 53, 145)
RED = (190, 20, 20)
CARD = (245, 248, 252)
CARD_HEADER = (220, 231, 243)
WHITE = (255, 255, 255)
TEXT = (28, 38, 48)
MUTED = (85, 96, 108)


def col(rgb):
    r, g, b = rgb
    return f'<color red="{r}" green="{g}" blue="{b}" alpha="255"/>'


def font(size, bold=False):
    return (f'<font><font family="Liberation Sans" style="{"BOLD" if bold else "REGULAR"}" '
            f'size="{size}.0"/></font>')


_n = [0]


def _name(prefix):
    _n[0] += 1
    return f"{prefix}{_n[0]}"


def rect(x, y, w, h, bg, line=None):
    border = f"<line_width>1</line_width><line_color>{col(line)}</line_color>" if line else \
        "<line_width>0</line_width>"
    return (f'<widget type="rectangle" version="2.0.0"><name>{_name("Rect")}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>{border}"
            f"<background_color>{col(bg)}</background_color></widget>")


def label(text, x, y, w, h, size=11, bold=False, fg=TEXT, bg=None, align=0):
    back = f"<background_color>{col(bg)}</background_color><transparent>false</transparent>" if bg else ""
    return (f'<widget type="label" version="2.0.0"><name>{_name("Label")}</name>'
            f"<text>{escape(text)}</text><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"{font(size, bold)}<foreground_color>{col(fg)}</foreground_color>{back}"
            f"<horizontal_alignment>{align}</horizontal_alignment>"
            f"<vertical_alignment>1</vertical_alignment></widget>")


#: char-waveform (longString) PVs must be shown with format STRING (6)
LONG_STRINGS = ("WINDOW", "LAST_EVENT", "RUN_DIR", "NOTE_RBV", "NOTE_CMD")


def update(pv, x, y, w, h, size=11, bold=False, fmt=None, prec=None, units=True):
    if fmt is None and pv.rsplit(":", 1)[-1] in LONG_STRINGS:
        fmt = 6
    extra = f"<format>{fmt}</format>" if fmt is not None else ""
    extra += f"<precision>{prec}</precision>" if prec is not None else ""
    extra += "" if units else "<show_units>false</show_units>"
    return (f'<widget type="textupdate" version="2.0.0"><name>{_name("Upd")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"{font(size, bold)}<background_color>{col(WHITE)}</background_color>"
            f"<foreground_color>{col(TEXT)}</foreground_color>{extra}</widget>")


def led(pv, x, y, size=20):
    return (f'<widget type="led" version="2.0.0"><name>{_name("Led")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{size}</width>"
            f"<height>{size}</height></widget>")


def button(text, pv, value, x, y, w=90, h=30, confirm=None, bg=None):
    conf = (f"<confirm_dialog>true</confirm_dialog><confirm_message>{escape(confirm)}</confirm_message>"
            if confirm else "")
    back = f"<background_color>{col(bg)}</background_color>" if bg else ""
    return (f'<widget type="action_button" version="3.0.0"><name>{_name("Btn")}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f'<actions><action type="write_pv"><pv_name>ca://{pv}</pv_name><value>{escape(str(value))}</value>'
            f"<description>{escape(text)}</description></action></actions>"
            f"<text>{escape(text)}</text>{back}{conf}</widget>")


def open_button(text, path, x, y, w=170, h=30):
    return (f'<widget type="action_button" version="3.0.0"><name>{_name("Open")}</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f'<actions><action type="open_display"><file>{escape(path)}</file><target>tab</target>'
            f"<description>{escape(text)}</description></action></actions>"
            f"<text>{escape(text)}</text></widget>")


def entry(pv, x, y, w, h=28, tooltip=""):
    fmt = "<format>6</format>" if pv.rsplit(":", 1)[-1] in LONG_STRINGS else ""
    return (f'<widget type="textentry" version="3.0.0"><name>{_name("Entry")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<tooltip>{escape(tooltip)}</tooltip><background_color>{col(WHITE)}</background_color>"
            f"{fmt}</widget>")


def combo(pv, x, y, w, h=26):
    return (f'<widget type="combo" version="2.0.0"><name>{_name("Combo")}</name>'
            f"<pv_name>ca://{pv}</pv_name><x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<items_from_pv>true</items_from_pv></widget>")


def card(title, x, y, w, h):
    return rect(x, y, w, h, CARD, CARD_HEADER) + rect(x, y, w, 28, CARD_HEADER) + \
        label(title, x + 10, y + 2, w - 20, 24, 12, True, NAVY)


def history_plot(x, y, w, h):
    def trace(name, ypv, rgb):
        return (f"<trace><name>{name}</name><x_pv>ca://{P}:HIST_MIN</x_pv><y_pv>ca://{ypv}</y_pv>"
                f"<axis>0</axis><trace_type>1</trace_type><line_width>2</line_width>"
                f"<color>{col(rgb)}</color></trace>")
    return (f'<widget type="xyplot" version="3.0.0"><name>TruthVsFit</name>'
            f"<x>{x}</x><y>{y}</y><width>{w}</width><height>{h}</height>"
            f"<title>Deuteron P: model truth vs online fitter</title><show_toolbar>false</show_toolbar>"
            f"<x_axis><title>sim minutes before now</title><autoscale>false</autoscale>"
            f"<minimum>-120</minimum><maximum>0</maximum>"
            f"<log_scale>false</log_scale><show_grid>true</show_grid></x_axis>"
            f"<y_axes><y_axis><title>P (%)</title><autoscale>true</autoscale><minimum>-60</minimum><maximum>60</maximum><log_scale>false</log_scale>"
            f"<show_grid>true</show_grid><on_right>false</on_right></y_axis></y_axes>"
            f"<traces>{trace('truth', P + ':HIST_TRUE_P', NAVY)}{trace('fitter', P + ':HIST_FIT_P', RED)}"
            f"</traces></widget>")


def build() -> str:
    w = []
    w.append(rect(0, 0, W, H, WHITE))
    # banner
    w.append(rect(0, 0, W, 64, RED))
    w.append(label("SIMULATION - NOT LIVE HARDWARE", 20, 6, 760, 52, 26, True, WHITE))
    w.append(label("Genesis simulated cool-down: Sim Control", 800, 8, 580, 24, 13, True, WHITE, align=2))
    w.append(update(f"{P}:RUN_DIR", 800, 34, 580, 22, 9))

    # clock card
    x, y = 20, 80
    w.append(card("Sim clock", x, y, 680, 250))
    w.append(label("Sim time (lab-local)", x + 15, y + 40, 200, 24, 11, False, MUTED))
    w.append(update(f"{P}:SIM_DATETIME", x + 15, y + 64, 330, 40, 20, True))
    w.append(led(f"{P}:RUNNING", x + 360, y + 72, 24))
    w.append(update(f"{P}:RUNNING", x + 392, y + 70, 110, 28, 12, True))
    w.append(label("Speed", x + 515, y + 40, 80, 24, 11, False, MUTED))
    w.append(update(f"{P}:SPEED_RBV", x + 515, y + 64, 140, 40, 18, True))
    w.append(button("Run", f"{P}:RUN_CMD", 1, x + 15, y + 118, 100, 34, bg=(200, 235, 205)))
    w.append(button("Pause", f"{P}:RUN_CMD", 0, x + 125, y + 118, 100, 34, bg=(250, 230, 200)))
    for i, s in enumerate((1, 10, 60, 300, 600)):
        w.append(button(f"{s}x", f"{P}:SPEED_CMD", s, x + 245 + i * 62, y + 118, 56, 34))
    for i, m in enumerate((-60, -10, 10, 60)):
        w.append(button(f"{m:+d} min", f"{P}:JUMP_MIN_CMD", m, x + 15 + i * 98, y + 162, 92, 30))
    w.append(label("Seek to", x + 410, y + 162, 60, 30, 11, False, MUTED))
    w.append(entry(f"{P}:SEEK_CMD", x + 470, y + 163, 195, 28, "YYYY-MM-DD HH:MM, lab-local"))
    w.append(label("Record", x + 15, y + 205, 60, 24, 10, False, MUTED))
    w.append(update(f"{P}:RECORD_START", x + 75, y + 205, 200, 24, 10))
    w.append(label("to", x + 280, y + 205, 25, 24, 10, False, MUTED))
    w.append(update(f"{P}:RECORD_END", x + 305, y + 205, 200, 24, 10))
    w.append(led(f"{P}:ENGINE_OK", x + 520, y + 207, 18))
    w.append(update(f"{P}:ENGINE_OK", x + 545, y + 205, 120, 24, 10))

    # session card
    x, y = 720, 80
    w.append(card("Session", x, y, 660, 250))
    for i, (lab, pv) in enumerate((("Mode", "MODE"), ("Scenario", "SCENARIO"), ("Source", "SOURCE"))):
        w.append(label(lab, x + 15, y + 40 + i * 30, 90, 26, 11, False, MUTED))
        w.append(update(f"{P}:{pv}", x + 105, y + 40 + i * 30, 300, 26, 11, True))
    w.append(label("Window", x + 15, y + 130, 90, 26, 11, False, MUTED))
    w.append(update(f"{P}:WINDOW", x + 105, y + 130, 540, 26, 10))
    w.append(label("Logbook note", x + 15, y + 165, 110, 28, 11, False, MUTED))
    w.append(entry(f"{P}:NOTE_CMD", x + 125, y + 165, 420, 28, "type a note and press Enter: "
                                                                "stamped with sim and wall time"))
    w.append(button("Mark", f"{P}:MARK_CMD", 1, x + 555, y + 165, 90, 28))
    w.append(label("Last", x + 15, y + 200, 60, 26, 11, False, MUTED))
    w.append(update(f"{P}:LAST_EVENT", x + 75, y + 200, 430, 26, 10))
    w.append(button("End session", f"{P}:END_SESSION_CMD", 1, x + 515, y + 198, 130, 30,
                    confirm="End the simulated cool-down? All replay IOCs will stop and the "
                            "after-action report will be written.", bg=(245, 205, 205)))

    # faults card
    x, y = 20, 345
    w.append(card("Instrument faults (emulated hardware)", x, y, 680, 330))
    for i, inst in enumerate(FAULT_INSTRUMENTS):
        yy = y + 38 + i * 35
        key = inst.upper()
        w.append(label(inst, x + 15, yy, 190, 28, 11, True))
        w.append(combo(f"{P}:FAULT_{key}_CMD", x + 210, yy, 170, 28))
        w.append(update(f"{P}:FAULT_{key}_RBV", x + 395, yy, 120, 28, 11, True))
    w.append(label("none / refuse (unplugged) / absent / silent / garbage / nak", x + 15, y + 300,
                   650, 22, 9, False, MUTED))

    # target model card
    x, y = 720, 345
    w.append(card("Target model (truth, closed loop)", x, y, 660, 330))
    w.append(led(f"{P}:MODEL_ACTIVE", x + 15, y + 40, 18))
    w.append(update(f"{P}:MODEL_ACTIVE", x + 40, y + 37, 200, 24, 11, True))
    truth = (("P", "TRUE_P"), ("Q", "TRUE_Q"), ("Q (1 temp)", "TRUE_Q_BOLTZMANN"),
             ("T lattice", "TRUE_T_LATTICE"), ("T spin", "TRUE_T_SPIN"), ("B", "TRUE_B"),
             ("MW drive", "MW_DRIVE"), ("MW freq", "MW_FREQ"), ("Quench", "QUENCH_LEVEL"),
             ("Coldboard", "HEAT_CB"), ("Fitter P", "FIT_P"), ("Fit - truth", "FIT_RESID_P"))
    for i, (lab, pv) in enumerate(truth):
        cx = x + 15 + (i % 3) * 215
        cy = y + 66 + (i // 3) * 28
        w.append(label(lab, cx, cy, 95, 26, 10, False, MUTED))
        w.append(update(f"{P}:{pv}", cx + 95, cy, 110, 26, 11, True, prec=3))
    yy = y + 182
    inputs = (("Coldboard heat (W)", "CB_HEAT_W_CMD"), ("NMR noise (x)", "NMR_NOISE_X_CMD"),
              ("NMR phase (deg)", "NMR_PHASE_DEG_CMD"), ("Line offset (MHz)", "FIELD_OFFSET_MHZ_CMD"),
              ("MW freq (GHz)", "MW_FREQ_GHZ_CMD"), ("MW power (dB)", "MW_POWER_DB_CMD"))
    for i, (lab, pv) in enumerate(inputs):
        cx = x + 15 + (i % 2) * 320
        cy = yy + (i // 2) * 30
        w.append(label(lab, cx, cy, 160, 26, 10, False, MUTED))
        w.append(entry(f"{P}:{pv}", cx + 160, cy, 110, 26))
    yb = y + 278
    w.append(button("MW on", f"{P}:MW_ON_CMD", 1, x + 15, yb, 80, 30))
    w.append(button("MW off", f"{P}:MW_ON_CMD", 0, x + 100, yb, 80, 30))
    w.append(button("Take baseline", f"{P}:TAKE_BASELINE_CMD", 1, x + 190, yb, 130, 30))
    w.append(button("QUENCH", f"{P}:QUENCH_CMD", 1, x + 330, yb, 100, 30,
                    confirm="Inject a quench-like warm-up into the simulated target?", bg=(245, 205, 205)))
    w.append(button("Clear quench", f"{P}:QUENCH_CMD", 0, x + 440, yb, 120, 30))

    # autopilot + navigation card
    x, y = 20, 690
    w.append(card("Autopilot (replays the logged operator actions)", x, y, 680, 230))
    for i, leg in enumerate(AUTOPILOT_LEGS):
        yy = y + 40 + i * 40
        key = leg.upper()
        w.append(label({"mw": "Microwave", "ssrf": "ssRF burns", "fit": "Fitter"}[leg],
                       x + 15, yy, 140, 30, 11, True))
        w.append(button("Human", f"{P}:AUTOPILOT_{key}_CMD", 0, x + 160, yy, 100, 30))
        w.append(button("Autopilot", f"{P}:AUTOPILOT_{key}_CMD", 1, x + 270, yy, 100, 30))
        w.append(led(f"{P}:AUTOPILOT_{key}_RBV", x + 385, yy + 5, 20))
        w.append(update(f"{P}:AUTOPILOT_{key}_RBV", x + 412, yy + 2, 120, 26, 11, True))
    w.append(label("Live operator screens (unchanged):", x + 15, y + 165, 300, 24, 10, False, MUTED))
    for i, (text, bob) in enumerate((("Cryo", "genesis_cryo.bob"), ("Microwave", "genesis_microwave.bob"),
                                     ("ssRF", "genesis_ssrf.bob"), ("NMR fitter", "genesis_ssrf_fitter.bob"))):
        w.append(open_button(text, f"../../gui/{bob}", x + 15 + i * 165, y + 190, 155, 30))

    w.append(history_plot(720, 690, 660, 230))

    body = "\n  ".join(w)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<display version="2.0.0">\n'
            f"  <name>Genesis Sim Control</name>\n  <width>{W}</width>\n  <height>{H}</height>\n"
            f"  <background_color>{col(WHITE)}</background_color>\n  {body}\n</display>\n")


def main():
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
