"""Live pyqtgraph display for the online NMR fitter.

Three stacked panels (mirroring the McClellan results viewer, but live):
  * raw NMR line straight from the LabVIEW DAQ;
  * the cubic-subtracted line with the total fit and the per-nucleus I+ / I-
    components overlaid;
  * a strip chart of P & Q vs time -- P_area & Q_eq (area method) and P_fit & Q_fit
    (fit method) -- with an optional rolling average and the single-spin-temperature
    Q_eq(P) = 2 - sqrt(4 - 3P^2) reference.

Controls: material selector, the fitting-mode buttons
(Baseline / Equilibrium / ssRF-holeburn / Frequency-hopping / No NMR), and
'Update Constants' (a full open fit over the recent equilibrium sweeps that refreshes
all lineshape constants + the area calibration CC, then reverts to rapid fitting).

The GUI runs in the Qt main thread and drains ``state.result_queue`` on a timer; the
server thread fills it. ``FitEngine``'s internal lock serializes the two.
"""
import collections
import datetime as _dt
import json
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("online_fitter.gui")
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import engine as E

pg.setConfigOptions(antialias=True, background="#101418", foreground="#c8d0d8")

# Mode button label -> engine mode constant.
_MODE_BUTTONS = [
    ("Baseline", E.MODE_BASELINE),
    ("Equilibrium spin-up", E.MODE_EQUILIBRIUM),
    ("ssRF / holeburn", E.MODE_SSRF),
    ("Frequency-hopping", E.MODE_FREQHOP),
    ("No NMR", E.MODE_NO_NMR),
]

# Strip-chart series: key -> (label, color).
_SERIES = [
    ("P_area", "P (area)", "#4da3ff"),
    ("P_fit", "P (fit)", "#7ee0ff"),
    ("Q_eq", "Q_eq (area)", "#ffb14d"),
    ("Q_fit", "Q (fit)", "#ff6f6f"),
]


class NMRWindow(QtWidgets.QMainWindow):
    def __init__(self, state, max_points=50000, roll_window=15, startup_note=None):
        super().__init__()
        self.state = state
        self.engine = state.engine
        self.max_points = max_points
        self.roll_window = roll_window
        self._startup_note = startup_note   # shown in the warnings box so the running code/mode is visible
        self._t0 = None
        self._n = 0
        self._uc_thread = None       # Update Constants worker thread + its result holder
        self._uc_result = None
        self._uc_t0 = None
        self._uc_token = None        # identity of the current run (stale results are discarded)
        self._uc_cancel = None       # threading.Event to abort the current run's swap
        self._uc_running = False
        self._last_warning = None    # last hole-burnt stale-reference warning shown (de-dupe)
        self._last_pq = {}           # latest P/Q values (for the Copy button)
        self._last_live = {}         # latest per-line r / phase / chi2 (for the Copy button)
        self._const_text_cache = None  # last constants-panel text (avoid resetting the user's selection)
        self._last_recv_wall = None    # wall-clock of the last sweep appended (staleness/live indicator)
        self._last_ts = None           # data timestamp (LabVIEW ts) of the most recent sweep
        self._left_view = 0            # left plot: 0=Raw NMR, 1=Baseline-subtracted (live), 2=Averaged + Fit
        self._last_item = None         # most recent drained sweep (to seed the live left views on toggle)
        self._uc_data_ts = None        # data ts when the constants were last fit / imported (for export)
        self._uc_when = None           # wall-clock string when the constants were last fit / imported
        # per-machine folder for exported/imported fit constants (inside online_fitter/ so the one
        # package folder carries everything; git-tracked dir, contents git-ignored)
        self._const_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "constants")
        try:
            os.makedirs(self._const_dir, exist_ok=True)
        except Exception:
            self._const_dir = ""
        self._stale_after = 20.0       # seconds with no sweep before the indicator turns red
        self._strip_minutes = 0.0      # trailing time window for the strip x-axis (0 = show all)
        # strip-chart history as RING BUFFERS: at max_points, appending drops the OLDEST point
        # automatically (deque(maxlen)), so the strip streams forever -- it never stops taking new
        # points once full.
        self._t = collections.deque(maxlen=max_points)
        self._hist = {k: collections.deque(maxlen=max_points) for k, _, _ in _SERIES}
        # trailing rolling average computed AT EACH POINT with the window active then -- changing the
        # window affects new points only; past values are frozen (never recomputed).
        self._roll_hist = {k: collections.deque(maxlen=max_points) for k, _, _ in _SERIES}
        # small trailing windows feeding the per-point rolling average (rebuilt when the user
        # changes the window size); kept separate so the O(window) mean stays cheap at 50k points.
        self._recent = {k: collections.deque(maxlen=max(1, int(roll_window))) for k, _, _ in _SERIES}
        self.setWindowTitle("Online NMR Fitter  --  Cooldown_Experimental_2026")
        self.resize(1150, 900)
        self._build_ui()
        self._sync_mode_buttons()
        self._update_component_legend()  # hide I+/I- secondary in the legend for single-bond (ND3)
        self._update_constants_panel()   # show the initial (preset) constants right away
        self.resize(1400, 850)           # wider default: side panel + two spectra + strip

        # Report which code/DPI mode is actually running, straight into the warnings box (visible even
        # when launched from LabVIEW) plus the current screen/scale -- so "which copy is live?" is
        # answerable without a cmd prompt.
        if self._startup_note:
            self._append_warning(self._startup_note)
        self._append_warning(f"display: {self._screen_info()}")

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(50)             # 20 Hz UI refresh

        # Heartbeat: log GUI-alive + sweep count + seconds-since-last-sweep every 60 s. If an overnight
        # run stops, the log shows WHICH failed: if heartbeats stop, the GUI froze; if they continue but
        # n_received stops climbing, LabVIEW/the feed stopped (also see the server's connection log).
        self._hb_prev_n = -1
        self.hb_timer = QtCore.QTimer(self)
        self.hb_timer.timeout.connect(self._log_heartbeat)
        self.hb_timer.start(60000)
        log.info("GUI started (heartbeat every 60s)")

    # -- UI construction -----------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 6)

        v.addLayout(self._build_controls())
        v.addLayout(self._build_view_controls())   # 2nd row: per-plot X/Y limit boxes + Reset View

        glw = pg.GraphicsLayoutWidget()
        # fill all available space and rescale with the window (the GraphicsLayout re-lays out
        # its plots on every resize; row/col stretch factors keep the proportions)
        glw.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.glw = glw

        # Left-plot view toggle, floated in the LEFT plot's top-left corner (far-left of the title).
        # Parented to the graphics widget's viewport so it overlays the plot; same style as the
        # Export Constants button.
        self.btn_left_view = QtWidgets.QPushButton("⟲ Raw NMR", glw.viewport())
        self.btn_left_view.setToolTip("Cycle the LEFT plot: Raw NMR -> live Baseline-subtracted -> "
                                      "Averaged + Fit (the data & fit from the last Update Constants).")
        self.btn_left_view.setStyleSheet(
            "QPushButton { background:#2a3138; color:#dfe6ec; border:1px solid #3a444e; "
            "border-radius:3px; padding:2px 8px; } "
            "QPushButton:hover { background:#38434e; } "
            "QPushButton:pressed { background:#20262c; }")
        self.btn_left_view.clicked.connect(self._on_left_view)
        self.btn_left_view.adjustSize()
        self.btn_left_view.move(6, 4)
        self.btn_left_view.raise_()

        body = QtWidgets.QHBoxLayout()           # [ readout panel | plots ]
        body.addWidget(self._build_side_panel())
        body.addWidget(glw, 1)
        v.addLayout(body, 1)

        # -- top row: two spectra in a NESTED layout so they stay EQUAL width. (A colspan strip
        # below shares the outer columns and pulls them unequal, which is what made the fit panel
        # narrower; a nested layout for just the two spectra avoids that coupling.)
        top = glw.addLayout(row=0, col=0)
        self.p_raw = top.addPlot(row=0, col=0, title="Raw NMR")
        self.p_raw.showGrid(x=True, y=True, alpha=0.15)
        self.p_raw.setLabel("bottom", "Frequency", units="MHz")
        self.p_raw.getAxis("left").setWidth(56)   # fixed axis width: viewbox never jitters as ticks change
        self.curve_raw = self.p_raw.plot(pen=pg.mkPen("#8899aa", width=1))
        # extra curves for the toggled left views (averaged fit-input data + its total fit). Hidden
        # until the "Left:" button selects them; the raw curve is hidden while they show.
        self.curve_uc_avg = self.p_raw.plot(pen=None, symbol="o", symbolSize=2.5,
                                            symbolBrush="#c8d0d8", symbolPen=None)
        self.curve_uc_fit = self.p_raw.plot(pen=pg.mkPen("#e05be0", width=2))
        # per-nucleus components for the Averaged+Fit view (same colors as the Processed panel).
        self.curve_uc_cPp = self.p_raw.plot(pen=pg.mkPen("#4da3ff", width=1.3))
        self.curve_uc_cPm = self.p_raw.plot(pen=pg.mkPen("#ff6f6f", width=1.3))
        self.curve_uc_cSp = self.p_raw.plot(pen=pg.mkPen("#4da3ff", width=1, style=QtCore.Qt.DashLine))
        self.curve_uc_cSm = self.p_raw.plot(pen=pg.mkPen("#ff6f6f", width=1, style=QtCore.Qt.DashLine))
        self._uc_curves = (self.curve_uc_avg, self.curve_uc_fit, self.curve_uc_cPp,
                           self.curve_uc_cPm, self.curve_uc_cSp, self.curve_uc_cSm)
        for _c in self._uc_curves:
            _c.setVisible(False)

        # -- fit panel (right of the raw panel). Title is CONSTANT -- the fit type and any warning go
        # to the side-panel status label so a changing title never resizes this plot's column.
        self.p_fit = top.addPlot(row=0, col=1, title="Processed NMR")
        self.p_fit.showGrid(x=True, y=True, alpha=0.15)
        self.p_fit.setLabel("bottom", "Frequency", units="MHz")
        self.p_fit.getAxis("left").setWidth(56)
        self._fit_legend = self.p_fit.addLegend(offset=(10, 10), labelTextSize="8pt")
        # Colorblind-friendly (red/green-safe): I+ BLUE, I- RED, total fit MAGENTA (no green).
        self.curve_flat = self.p_fit.plot(pen=None, symbol="o", symbolSize=2.5,
                                          symbolBrush="#c8d0d8", symbolPen=None, name="Data")
        self.curve_fit = self.p_fit.plot(pen=pg.mkPen("#e05be0", width=2), name="Total Fit")
        self.curve_cPp = self.p_fit.plot(pen=pg.mkPen("#4da3ff", width=1.3), name="I+ primary")
        self.curve_cPm = self.p_fit.plot(pen=pg.mkPen("#ff6f6f", width=1.3), name="I- primary")
        self.curve_cSp = self.p_fit.plot(pen=pg.mkPen("#4da3ff", width=1, style=QtCore.Qt.DashLine), name="I+ secondary")
        self.curve_cSm = self.p_fit.plot(pen=pg.mkPen("#ff6f6f", width=1, style=QtCore.Qt.DashLine), name="I- secondary")
        self.p_fit.setXLink(self.p_raw)
        # Keep the two spectra EQUAL width: equal stretch, plus an equal column MINIMUM that exceeds
        # the plot titles' widths -- otherwise the different title lengths ("Raw NMR" vs "Processed
        # NMR") drive the two columns to different widths.
        top.layout.setColumnStretchFactor(0, 1)
        top.layout.setColumnStretchFactor(1, 1)
        top.layout.setColumnMinimumWidth(0, 400)
        top.layout.setColumnMinimumWidth(1, 400)

        # -- strip chart (full width, below the two spectra panels).
        # x-axis is the computer's wall-clock date/time each line was grabbed + fit.
        self.p_strip = glw.addPlot(row=1, col=0, title="Polarizations",
                                   axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.p_strip.showGrid(x=True, y=True, alpha=0.15)
        self.p_strip.setDownsampling(auto=True, mode="peak")   # keep the strip fast even near 50k points
        self.p_strip.setClipToView(True)                       # only render the visible time range
        self.p_strip.setLabel("bottom", "Time")
        self.p_strip.setLabel("left", "polarization", units="%")
        # Keep the unit as "%" always. Without this, pyqtgraph auto-adds an SI prefix when the values
        # are tiny (e.g. no signal near 0) and shows the nonsense unit "m%" (milli-percent).
        self.p_strip.getAxis("left").enableAutoSIPrefix(False)
        self.p_strip.addLegend(offset=(10, 10), labelTextSize="8pt")
        self.strip_curves = {}
        self.roll_curves = {}
        for key, label, color in _SERIES:
            dot = pg.mkColor(color); dot.setAlpha(64)       # per-sweep dots 75% transparent (25% opacity)
            self.strip_curves[key] = self.p_strip.plot(
                pen=None, symbol="o", symbolSize=4, symbolBrush=dot, symbolPen=None, name=label)
            self.roll_curves[key] = self.p_strip.plot(pen=pg.mkPen(color, width=2))   # rolling avg fully opaque
        self.p_strip.getAxis("left").setWidth(56)
        glw.ci.layout.setRowStretchFactor(0, 3)             # nested spectra row
        glw.ci.layout.setRowStretchFactor(1, 3)             # strip row

        # Scroll-to-zoom was fiddly; the view is driven by the X/Y limit boxes + Reset View instead.
        # Kill wheel-zoom on all three plots (drag-pan still works if the user wants it).
        for _p in (self.p_raw, self.p_fit, self.p_strip):
            _p.getViewBox().wheelEvent = lambda *a, **k: None

    def _build_controls(self):
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Material:"))
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(E.MATERIAL_NAMES)
        self.material_combo.setCurrentText(self.engine.material)
        self.material_combo.currentTextChanged.connect(self._on_material)
        bar.addWidget(self.material_combo)

        bar.addSpacing(16)
        bar.addWidget(QtWidgets.QLabel("Mode:"))
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self._mode_by_button = {}
        for label, mode in _MODE_BUTTONS:
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _checked, m=mode: self._on_mode(m))
            self.mode_group.addButton(b)
            self._mode_by_button[mode] = b
            bar.addWidget(b)

        bar.addSpacing(16)
        self.btn_update = QtWidgets.QPushButton("Update Constants")
        self.btn_update.setToolTip("Full open fit over recent equilibrium sweeps -> refresh all "
                                   "lineshape constants + area calibration, then resume rapid fitting")
        self.btn_update.clicked.connect(self._on_update_constants)
        bar.addWidget(self.btn_update)

        bar.addSpacing(16)
        bar.addWidget(QtWidgets.QLabel("Larmor ±kHz:"))
        self.larmor_spin = QtWidgets.QDoubleSpinBox()
        self.larmor_spin.setRange(0.0, 200.0)
        self.larmor_spin.setDecimals(1)
        self.larmor_spin.setSingleStep(1.0)
        self.larmor_spin.setValue(self.engine.larmor_khz)
        self.larmor_spin.setToolTip("Half-width of the unperturbed Larmor region used to read r_eq "
                                    "from each hole-burnt line (0 = use the Update Constants r_eq)")
        self.larmor_spin.valueChanged.connect(self.engine.set_larmor_khz)
        bar.addWidget(self.larmor_spin)

        self.roll_check = QtWidgets.QCheckBox("rolling avg")
        self.roll_check.setChecked(True)
        bar.addWidget(self.roll_check)
        self.roll_spin = QtWidgets.QSpinBox()
        self.roll_spin.setRange(1, 999)
        self.roll_spin.setValue(int(self.roll_window))
        self.roll_spin.setSuffix(" pts")
        self.roll_spin.setToolTip("Points in the trailing rolling average. Changing it applies to NEW "
                                  "points only -- past averaged points keep the window they were computed with.")
        self.roll_spin.valueChanged.connect(self._on_roll_window)
        bar.addWidget(self.roll_spin)

        self.autoscale_check = QtWidgets.QCheckBox("auto-scale")
        self.autoscale_check.setChecked(True)
        self.autoscale_check.setToolTip("Auto-range the P/Q strip to fit the data. Uncheck to set the "
                                        "axes manually (right-click the strip → set range, or drag/zoom) "
                                        "without the live updates snapping the view back.")
        self.autoscale_check.toggled.connect(self._on_autoscale)
        bar.addWidget(self.autoscale_check)

        self.btn_clear = QtWidgets.QPushButton("Clear P/Q")
        self.btn_clear.setToolTip("Discard the P/Q strip-chart history (e.g. start of a new run, "
                                  "or before Update Constants).")
        self.btn_clear.clicked.connect(self._clear_strip)
        bar.addWidget(self.btn_clear)

        bar.addStretch(1)
        return bar        # status/warnings go to the window's status bar (clips long text, never resizes)

    def _build_view_controls(self):
        """Second control row: explicit X/Y axis limits for each of the three plots plus a
        'Reset View' that returns every plot to auto-range (full data). Typing a value and
        pressing Enter (or leaving the box) applies it; blank = leave that axis on auto."""
        bar = QtWidgets.QHBoxLayout()
        self._range_edits = {}   # plot key -> {"xmin"/"xmax"/"ymin"/"ymax"/"mins": QLineEdit}

        def edit(placeholder, plotkey):
            e = QtWidgets.QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFixedWidth(48)
            e.setValidator(QtGui.QDoubleValidator())
            e.editingFinished.connect(lambda pk=plotkey: self._apply_range(pk))
            return e

        def group(title, plotkey, edits):
            lbl = QtWidgets.QLabel(title)
            lbl.setStyleSheet("color:#9aa4ae; font-size:9pt;")
            bar.addWidget(lbl)
            self._range_edits[plotkey] = edits
            for key, tag in (("xmin", "X"), ("ymin", "Y"), ("mins", "last")):
                if key not in edits:
                    continue
                t = QtWidgets.QLabel(tag)
                t.setStyleSheet("color:#6a7480; font-size:9pt;")
                bar.addWidget(t)
                bar.addWidget(edits[key])
                if key == "mins":
                    m = QtWidgets.QLabel("min")
                    m.setStyleSheet("color:#6a7480; font-size:9pt;")
                    bar.addWidget(m)
                else:                                    # xmin/ymin are followed by their max box
                    bar.addWidget(edits[key.replace("min", "max")])

        bar.addWidget(QtWidgets.QLabel("Axes:"))
        group("Raw", "raw", {"xmin": edit("min", "raw"), "xmax": edit("max", "raw"),
                             "ymin": edit("min", "raw"), "ymax": edit("max", "raw")})
        bar.addSpacing(10)
        group("Processed", "fit", {"xmin": edit("min", "fit"), "xmax": edit("max", "fit"),
                                   "ymin": edit("min", "fit"), "ymax": edit("max", "fit")})
        bar.addSpacing(10)
        # The strip x-axis is wall-clock time, so instead of raw epoch numbers it takes a trailing
        # window in minutes ("last N min"); Y is polarization %.
        group("Polarizations", "strip", {"ymin": edit("min", "strip"), "ymax": edit("max", "strip"),
                                         "mins": edit("all", "strip")})

        bar.addSpacing(12)
        self.btn_reset_view = QtWidgets.QPushButton("Reset View")
        self.btn_reset_view.setToolTip("Clear all axis limits and return every plot to auto-range "
                                       "(full data range).")
        self.btn_reset_view.clicked.connect(self._reset_view)
        bar.addWidget(self.btn_reset_view)

        self.btn_fix_dpi = QtWidgets.QPushButton("Fix DPI")
        self.btn_fix_dpi.setToolTip("Force the plots to re-render at the current monitor's scale. "
                                    "Click once the window is the size you want, after moving it to "
                                    "another monitor, if the plots look clipped.")
        self.btn_fix_dpi.clicked.connect(self._force_dpi_refresh)
        bar.addWidget(self.btn_fix_dpi)

        bar.addStretch(1)
        # live/stale indicator: green while sweeps arrive, red when they stop (distinguishes a
        # dead LabVIEW/TCP feed from a stuck GUI -- both look like "frozen plots").
        self.conn_label = QtWidgets.QLabel("● waiting for sweeps")
        self.conn_label.setStyleSheet("color:#ffb14d; font-size:9pt; font-weight:bold;")
        bar.addWidget(self.conn_label)
        return bar

    def _num(self, e):
        t = e.text().strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None

    def _apply_range(self, plotkey):
        """Apply the typed limits for one plot. Both bounds of an axis must be present (and max >
        min) for that axis to be set; setting a range disables auto-range on that axis until Reset."""
        ed = self._range_edits[plotkey]
        if plotkey == "strip":
            ymin, ymax = self._num(ed["ymin"]), self._num(ed["ymax"])
            if ymin is not None and ymax is not None and ymax > ymin:
                self.p_strip.setYRange(ymin, ymax, padding=0)
                self.autoscale_check.setChecked(False)
            mins = self._num(ed["mins"])
            self._strip_minutes = mins if (mins and mins > 0) else 0.0
            if self._strip_minutes:
                self.autoscale_check.setChecked(False)   # trailing window drives the x-axis now
            else:
                self.p_strip.enableAutoRange(axis=pg.ViewBox.XAxis)
            return
        plot = self.p_raw if plotkey == "raw" else self.p_fit
        xmin, xmax = self._num(ed["xmin"]), self._num(ed["xmax"])
        if xmin is not None and xmax is not None and xmax > xmin:
            plot.setXRange(xmin, xmax, padding=0)
        ymin, ymax = self._num(ed["ymin"]), self._num(ed["ymax"])
        if ymin is not None and ymax is not None and ymax > ymin:
            plot.setYRange(ymin, ymax, padding=0)

    def _reset_view(self):
        """Clear every axis-limit box and restore all three plots to auto-range (full data)."""
        for edits in self._range_edits.values():
            for e in edits.values():
                e.clear()
        self._strip_minutes = 0.0
        for p in (self.p_raw, self.p_fit, self.p_strip):
            p.enableAutoRange()
        self.autoscale_check.setChecked(True)
        self._set_status("view reset -- all plots auto-ranging")

    def _build_side_panel(self):
        """Left-hand readout panel: big P/Q values (area + fit), CC, and the fit parameters,
        all as selectable text (copy/paste), plus a 'Copy readout' button."""
        panel = QtWidgets.QWidget()
        self._side_panel = panel                      # exposed so docs/make_figures can grab it
        panel.setMinimumWidth(250)
        panel.setMaximumWidth(340)
        panel.setStyleSheet("background:#101418;")   # match the dark plots so the colored values pop
        lay = QtWidgets.QVBoxLayout(panel)
        lay.setContentsMargins(6, 6, 10, 6)

        self.fit_type_label = QtWidgets.QLabel("fit: --")   # current fit type + a ⚠ when a warning fires
        self.fit_type_label.setStyleSheet("color:#9aa4ae; font-size:10pt;")
        self.fit_type_label.setWordWrap(True)
        lay.addWidget(self.fit_type_label)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)
        self.pq_labels = {}
        for i, (key, label, color) in enumerate(
                [("P_area", "P (area)", "#4da3ff"), ("P_fit", "P (fit)", "#7ee0ff"),
                 ("Q_eq", "Q_eq (area)", "#ffb14d"), ("Q_fit", "Q (fit)", "#ff6f6f")]):
            name = QtWidgets.QLabel(label)
            name.setStyleSheet("color:#9aa4ae; font-size:10pt;")
            val = QtWidgets.QLabel("--")
            val.setStyleSheet(f"color:{color}; font-size:26pt; font-weight:bold;")
            val.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.pq_labels[key] = val
            grid.addWidget(name, i, 0)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        # live per-line fit outputs (update every sweep, unlike the frozen constants below)
        live = QtWidgets.QGridLayout()
        live.setHorizontalSpacing(8)
        live.setVerticalSpacing(1)
        self.live_labels = {}
        for i, (key, label) in enumerate([("r", "r"), ("phase", "phase"), ("chi2", "χ²ν")]):
            nm = QtWidgets.QLabel(label)
            nm.setStyleSheet("color:#9aa4ae; font-size:10pt;")
            vl = QtWidgets.QLabel("--")
            vl.setStyleSheet("color:#c8d0d8; font-size:13pt; font-weight:bold;")
            vl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            vl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.live_labels[key] = vl
            live.addWidget(nm, i, 0)
            live.addWidget(vl, i, 1)
        live.setColumnStretch(1, 1)
        lay.addLayout(live)

        self.const_text = QtWidgets.QPlainTextEdit()
        self.const_text.setReadOnly(True)
        mono = QtGui.QFont("Consolas")
        mono.setStyleHint(QtGui.QFont.Monospace)
        mono.setPointSize(9)
        self.const_text.setFont(mono)
        self.const_text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.const_text.setStyleSheet("background:#0b0e11; color:#c8d0d8; border:1px solid #2a3138;")
        lay.addWidget(self.const_text, 1)

        wlabel = QtWidgets.QLabel("warnings / errors")
        wlabel.setStyleSheet("color:#9aa4ae; font-size:9pt;")
        lay.addWidget(wlabel)
        self.warn_text = QtWidgets.QPlainTextEdit()
        self.warn_text.setReadOnly(True)
        self.warn_text.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)   # wrap to the panel width
        self.warn_text.setMaximumBlockCount(300)                               # cap history
        self.warn_text.setMinimumHeight(80)
        self.warn_text.setMaximumHeight(170)
        warn_font = QtGui.QFont("Consolas")
        warn_font.setStyleHint(QtGui.QFont.Monospace)
        warn_font.setPointSize(8)
        self.warn_text.setFont(warn_font)
        self.warn_text.setStyleSheet("background:#160f0a; color:#ffcc88; border:1px solid #3a2f22;")
        lay.addWidget(self.warn_text)

        # The panel's dark background cascades onto child QPushButtons, which then render
        # black-on-black. Give the panel's buttons an explicit visible style.
        btn_style = ("QPushButton { background:#2a3138; color:#dfe6ec; border:1px solid #3a444e; "
                     "border-radius:3px; padding:4px 6px; } "
                     "QPushButton:hover { background:#38434e; } "
                     "QPushButton:pressed { background:#20262c; }")

        self.btn_copy = QtWidgets.QPushButton("Copy readout")
        self.btn_copy.setToolTip("Copy the P/Q values, CC, and fit parameters to the clipboard")
        self.btn_copy.clicked.connect(self._copy_readout)
        self.btn_copy.setStyleSheet(btn_style)
        lay.addWidget(self.btn_copy)

        io_row = QtWidgets.QHBoxLayout()
        self.btn_export = QtWidgets.QPushButton("Export Constants")
        self.btn_export.setToolTip("Save the current fit constants + CC to a file (plus the data "
                                   "date/time and P/Q/r/phase/chi2 as post-analysis reference).")
        self.btn_export.clicked.connect(self._export_constants)
        self.btn_export.setStyleSheet(btn_style)
        io_row.addWidget(self.btn_export)
        self.btn_import = QtWidgets.QPushButton("Import Constants")
        self.btn_import.setToolTip("Load fit constants + CC from a file -- installs them exactly as a "
                                   "fresh Update Constants would (reference values are ignored).")
        self.btn_import.clicked.connect(self._import_constants)
        self.btn_import.setStyleSheet(btn_style)
        io_row.addWidget(self.btn_import)
        lay.addLayout(io_row)
        return panel

    def _screen_info(self):
        """One-line description of the current monitor + scale (for the Fix DPI readout)."""
        try:
            dpr = self.devicePixelRatioF()
            # screenAt(window center) tracks where the window ACTUALLY is now; windowHandle().screen()
            # only reports the screen it was created on (so it kept saying DISPLAY2 after a drag).
            scr = QtWidgets.QApplication.screenAt(self.frameGeometry().center())
            if scr is None:
                wh = self.windowHandle()
                scr = wh.screen() if wh is not None else None
            if scr is not None:
                return (f"screen={scr.name()}  scale(dpr)={dpr:g}x  "
                        f"logicalDPI={scr.logicalDotsPerInch():.0f}  win={self.width()}x{self.height()}")
            return f"scale(dpr)={dpr:g}x  win={self.width()}x{self.height()}"
        except Exception as exc:
            return f"(screen info error: {exc})"

    def _force_dpi_refresh(self):
        """'Fix DPI' button: a GENTLE manual relayout -- recompute the plot grid and repaint, no
        hide/show or resize nudges (those were corrupting the axes). Mostly it just reports the
        current screen/scale into the warnings box so we can see what the window thinks it's on."""
        info = self._screen_info()
        try:
            self.glw.ci.layout.invalidate()
            self.glw.ci.layout.activate()
            self.glw.viewport().update()
        except Exception as exc:
            self._append_warning(f"Fix DPI error: {exc}")
        self._append_warning(f"Fix DPI: {info}")     # readable/copyable in the warnings box
        self._set_status(f"Fix DPI applied -- {info}")

    def _sync_mode_buttons(self):
        cur = self.state.get_mode()
        b = self._mode_by_button.get(cur)
        if b is not None:
            b.setChecked(True)

    # -- control callbacks ---------------------------------------------------
    def _on_material(self, name):
        self.engine.set_material(name)
        self._update_component_legend()              # single vs double -> secondary legend entries
        self._refresh_left_view()                    # set_material cleared the averaged/fit view
        self._set_status(f"material set to {name} (preset constants -- run Update Constants to calibrate)")

    def _on_mode(self, mode):
        self.state.set_mode(mode)
        self._set_status(f"mode: {mode}")

    def _on_left_view(self):
        """Cycle the left plot: Raw NMR -> live Baseline-subtracted -> Averaged + Fit (last UC)."""
        self._left_view = (self._left_view + 1) % 3
        self.btn_left_view.setText("⟲ " + ["Raw NMR", "Baseline-sub", "Averaged + Fit"][self._left_view])
        self.btn_left_view.adjustSize()              # keep the corner button snug as the label changes
        self._refresh_left_view()

    def _refresh_left_view(self):
        """Set up the left plot for the current view. Views 0 (raw) and 1 (baseline-subtracted) are
        LIVE -- refreshed every sweep in _redraw_spectra; here we just toggle visibility and seed
        them from the last sweep. View 2 shows the STATIC averaged data + fit from the last Update
        Constants (the averaging happens only at Update Constants time)."""
        view = self._left_view
        for c in self._uc_curves:                    # hide all extras; re-show per view below
            c.setVisible(False)
        self.curve_raw.setVisible(view == 0)
        it = self._last_item
        if view == 0:
            self.p_raw.setTitle("Raw NMR")
        elif view == 1:
            self.curve_uc_avg.setVisible(True)
            if it is not None and it.get("baseline_sub") is not None:   # seed immediately on toggle
                self.curve_uc_avg.setData(np.asarray(it["freqs"], float),
                                          np.asarray(it["baseline_sub"], float))
            self.p_raw.setTitle("Baseline-subtracted")
        else:                                        # view 2: static averaged data + fit + components
            v = self.engine.update_view()
            if v is None:
                self.p_raw.setTitle("Averaged + Fit for Constants -- run Update Constants first")
                return
            f = np.asarray(v["freqs"], float)
            self.curve_uc_avg.setData(f, np.asarray(v["data"], float))
            self.curve_uc_fit.setData(f, np.asarray(v["fit"], float))
            self.curve_uc_cPp.setData(f, np.asarray(v["cPp"], float))
            self.curve_uc_cPm.setData(f, np.asarray(v["cPm"], float))
            for c in (self.curve_uc_avg, self.curve_uc_fit, self.curve_uc_cPp, self.curve_uc_cPm):
                c.setVisible(True)
            if self.engine.model != "single":        # secondaries only for double-bond materials (not ND3)
                self.curve_uc_cSp.setData(f, np.asarray(v["cSp"], float))
                self.curve_uc_cSm.setData(f, np.asarray(v["cSm"], float))
                self.curve_uc_cSp.setVisible(True)
                self.curve_uc_cSm.setVisible(True)
            self.p_raw.setTitle("Averaged + Fit for Constants")
            rc = v.get("red_chisq")
            self._set_status(f"Left view: averaged data + fit -- P={v['P']:.1f}%  "
                             f"chi2_nu={'--' if rc is None else format(rc, '.2f')}  "
                             f"(avg of {v.get('n_avg') or '?'} eq sweeps)")

    def _update_component_legend(self):
        """Show the I+/I- SECONDARY (O-D bond) entries only for the double-bond materials
        (d-butanol, d-propanediol); hide them for single-bond ND3, which has no secondaries."""
        show = (self.engine.model != "single")
        for curve, name in ((self.curve_cSp, "I+ secondary"), (self.curve_cSm, "I- secondary")):
            curve.setVisible(show)
            try:                                     # drop any existing entry first (avoid duplicates)
                self._fit_legend.removeItem(curve)
            except Exception:
                try:
                    self._fit_legend.removeItem(name)
                except Exception:
                    pass
            if show:
                self._fit_legend.addItem(curve, name)

    def _on_update_constants(self):
        """Start the full open fit -- or, if one is already running, act as STOP.

        The fit runs in a WORKER thread so the GUI stays live (~30-60 s). curve_fit can't be
        interrupted, so Stop is a clean abort: the doomed fit finishes in the background but its
        result is discarded (stale token) and the engine skips the constants swap (cancel event),
        freeing the button so a fresh Update Constants can start immediately."""
        if self._uc_running:
            self._stop_update_constants()
            return
        self._uc_token = tok = object()
        self._uc_cancel = cancel = threading.Event()
        self._uc_running = True
        self._uc_result = None
        self._uc_t0 = _dt.datetime.now()
        self.btn_update.setText("Stop Update Constants")
        self._set_status("Update Constants: running full open fit (may take ~30-60 s) -- click Stop to abort ...")

        def work():
            try:
                self._uc_result = (tok, "ok", self.engine.update_constants(cancel_event=cancel))
            except Exception as exc:                  # noqa: BLE001 -- surfaced in the status bar
                self._uc_result = (tok, "err", exc)
        self._uc_thread = threading.Thread(target=work, name="update-constants", daemon=True)
        self._uc_thread.start()

    def _stop_update_constants(self):
        """Abort the running Update Constants: skip the swap, discard the result, free the button."""
        if self._uc_cancel is not None:
            self._uc_cancel.set()                     # engine will not swap constants when the fit returns
        self._uc_token = None                         # any pending/late result is now stale -> discarded
        self._uc_running = False
        self.btn_update.setText("Update Constants")
        self.btn_update.setEnabled(True)
        self._set_status("Update Constants stopped -- constants unchanged. The aborted fit finishes in the "
                         "background and is discarded. You can start a new one.")

    def _poll_update_constants(self):
        """Called from the drain timer: apply a finished run's result (if it's still the current
        one), or tick the running one's elapsed time. Runs on the GUI thread."""
        r = self._uc_result
        if r is None:
            if self._uc_running and self._uc_thread is not None and self._uc_thread.is_alive():
                el = (_dt.datetime.now() - self._uc_t0).total_seconds()
                self._set_status(f"Update Constants: running full open fit ... {el:.0f}s (click Stop to abort)")
            return
        self._uc_result = None
        tok, kind, payload = r
        if tok is not self._uc_token:                 # stopped or superseded -> silently discard
            return
        self._uc_running = False
        self._uc_token = None
        self._uc_thread = None
        self.btn_update.setText("Update Constants")
        self.btn_update.setEnabled(True)
        if kind == "ok" and isinstance(payload, dict) and payload.get("cancelled"):
            self._set_status("Update Constants cancelled -- constants unchanged.")
        elif kind == "ok":
            uc = payload
            # remember the data time + wall time this calibration was established (for Export Constants)
            self._uc_data_ts = self._last_ts
            self._uc_when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if self._left_view == 2:                 # refresh the averaged+fit view with the new fit
                self._refresh_left_view()
            self._set_status(f"Update Constants OK: P={uc['P']:.2f}%  r={uc['r']:.3f}  "
                             f"CC={uc['CC']:.3g}  chi2_nu={uc['red_chisq']:.2f}  "
                             f"(avg of {uc['n_avg']} eq sweeps)")
        else:
            self._set_status(f"Update Constants FAILED: {payload}")

    def _append_warning(self, msg):
        """Append a timestamped warning/error line to the scrollable warnings box (auto-scrolls
        to the newest; wraps to the panel width; history capped by setMaximumBlockCount)."""
        self.warn_text.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _on_roll_window(self, n):
        """Change the trailing rolling-average window. Only NEW points use the new window; the
        already-computed rolling values stay as they were (computed with the old window)."""
        self.roll_window = int(n)

    def _on_autoscale(self, on):
        """Toggle continuous auto-ranging of the P/Q strip. Off freezes the current view so the
        user can set axes manually (right-click → set range, or drag/zoom) without the live
        updates snapping it back."""
        if on:
            self.p_strip.enableAutoRange()
        else:
            self.p_strip.disableAutoRange()

    def _clear_strip(self):
        """Discard the P/Q strip-chart history (data only; the frozen/auto view state is kept)."""
        self._t = collections.deque(maxlen=self.max_points)
        self._hist = {k: collections.deque(maxlen=self.max_points) for k, _, _ in _SERIES}
        self._roll_hist = {k: collections.deque(maxlen=self.max_points) for k, _, _ in _SERIES}
        self._recent = {k: collections.deque(maxlen=max(1, int(self.roll_window))) for k, _, _ in _SERIES}
        self._t0 = None
        self._n = 0
        for c in list(self.strip_curves.values()) + list(self.roll_curves.values()):
            c.clear()

    def _set_status(self, msg):
        # The status bar clips long text and never resizes the window (unlike a control-bar label).
        st = self.engine.status()
        cc = "-" if st["CC"] is None else f"{st['CC']:.3g}"
        self.statusBar().showMessage(f"{msg}    [src={st['const_source']} CC={cc} "
                                     f"eqbuf={st['n_eq_buffered']} n={self.state.n_received}]")

    # -- readout panel -------------------------------------------------------
    def _update_live(self, res):
        """Update the big P/Q values AND the live per-line fit outputs (r, phase, chi2) -- every sweep."""
        self._last_pq = {k: res.get(k) for k in ("P_area", "P_fit", "Q_eq", "Q_fit")}
        for key, lbl in self.pq_labels.items():
            v = res.get(key)
            lbl.setText("--" if v is None or not np.isfinite(v) else f"{v:.2f} %")
        r, ph, rc = res.get("r"), res.get("phase_deg"), res.get("red_chisq")
        self._last_live = {"r": r, "phase": ph, "chi2": rc}
        self.live_labels["r"].setText("--" if r is None or not np.isfinite(r) else f"{r:.3f}")
        self.live_labels["phase"].setText("--" if ph is None or not np.isfinite(ph) else f"{ph:+.1f}°")
        self.live_labels["chi2"].setText("--" if rc is None or not np.isfinite(rc) else f"{rc:.2f}")

    def _const_block(self, snap):
        """The STABLE calibration text: CC + lineshape constants (from Update Constants). The
        per-line r/phase/chi2 are shown live above, not here -- so this text stays selectable."""
        c = snap["constants"]
        cc = snap["CC"]
        cc_daq = snap.get("CC_DAQ")
        out = [f"material : {snap['material']}  ({snap['model']})",
               f"source   : {snap['source']}",
               f"CC       : {'--' if cc is None else format(cc, '.5g')}",
               f"CC_DAQ   : {'--' if cc_daq is None else format(cc_daq, '.5g')}", ""]
        for k in ("omegD", "omegQ1", "omegQ2", "A1", "eta1", "eta2", "K", "scale"):
            if k not in c:
                continue
            if k == "omegD":
                out.append(f"omegD    = {c[k]:.4f} MHz")
            elif k in ("omegQ1", "omegQ2", "scale"):
                out.append(f"{k:<8} = {c[k]:.6f}")
            else:
                out.append(f"{k:<8} = {c[k]:.5f}")
        return "\n".join(out)

    def _update_constants_panel(self):
        """Refresh the CC + fit-parameters text, but only when it actually changes -- so the
        user's text selection (for copy/paste) isn't reset on every live update."""
        txt = self._const_block(self.engine.constants_snapshot())
        if txt != self._const_text_cache:
            self.const_text.setPlainText(txt)
            self._const_text_cache = txt

    def _copy_readout(self):
        """Copy the current P/Q values + live r/phase/chi2 + CC + constants to the clipboard."""
        pq = self._last_pq or {}
        live = self._last_live or {}
        def s(k):
            v = pq.get(k)
            return "--" if v is None or not np.isfinite(v) else f"{v:.2f} %"
        def lv(k, fmt, suf=""):
            v = live.get(k)
            return "--" if v is None or not np.isfinite(v) else format(v, fmt) + suf
        head = (f"P (area)   = {s('P_area')}\nP (fit)    = {s('P_fit')}\n"
                f"Q_eq(area) = {s('Q_eq')}\nQ (fit)    = {s('Q_fit')}\n"
                f"r          = {lv('r', '.3f')}\nphase      = {lv('phase', '+.1f', ' deg')}\n"
                f"chi2_nu    = {lv('chi2', '.2f')}\n\n")
        QtWidgets.QApplication.clipboard().setText(head + self._const_block(self.engine.constants_snapshot()))
        self._set_status("readout copied to clipboard")

    # -- export / import fit constants ---------------------------------------
    def _export_constants(self):
        """Write the current calibration to a JSON file: the constants + CC needed to reload, plus
        the data date/time and the P/Q/r/phase/chi2 readout as a post-analysis reference."""
        payload = self.engine.export_constants()
        if payload is None:
            self._set_status("Export Constants: nothing calibrated yet -- run Update Constants first.")
            return
        pq = self._last_pq or {}
        live = self._last_live or {}

        def fv(x):
            return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x

        full = {
            "format": "online_fitter_constants",
            "version": 1,
            "exported_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_timestamp": self._uc_data_ts,       # data ts when the constants were fit/imported
            "fitted_at": self._uc_when,               # wall-clock when the constants were fit/imported
            "material": payload["material"],
            "model": payload["model"],
            # post-analysis reference values -- IGNORED on import, for the record only:
            "reference": {
                "P_area": fv(pq.get("P_area")), "P_fit": fv(pq.get("P_fit")),
                "Q_eq_area": fv(pq.get("Q_eq")), "Q_fit": fv(pq.get("Q_fit")),
                "r": fv(live.get("r")), "phase_deg": fv(live.get("phase")),
                "red_chisq": fv(live.get("chi2")), "CC": payload["CC"],
                "CC_DAQ": payload.get("CC_DAQ"),
            },
            # everything import_constants() needs to reload the calibration:
            "constants": payload,
        }
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        default = os.path.join(self._const_dir, f"constants_{payload['material']}_{stamp}.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export fit constants", default,
                                                        "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(full, f, indent=2)
            self._set_status(f"Constants exported -> {path}")
            self._append_warning(f"exported constants to {path}")
        except Exception as exc:                       # noqa: BLE001 -- surfaced to the user
            self._append_warning(f"Export failed: {exc}")
            self._set_status(f"Export failed: {exc}")

    def _import_constants(self):
        """Load a constants file and install it exactly as a fresh Update Constants would."""
        start = self._const_dir or ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import fit constants", start,
                                                        "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                full = json.load(f)
            block = full.get("constants", full)        # accept a wrapped file or a bare constants dict
            summary = self.engine.import_constants(block)
        except Exception as exc:                       # noqa: BLE001 -- bad/incompatible file
            self._append_warning(f"Import failed: {exc}")
            self._set_status(f"Import failed: {exc}")
            return
        # carry the original provenance forward so a later re-export keeps the data date/time
        self._uc_data_ts = full.get("data_timestamp")
        self._uc_when = full.get("fitted_at")
        # re-sync the material selector WITHOUT triggering set_material (which would reset to preset)
        self.material_combo.blockSignals(True)
        self.material_combo.setCurrentText(summary["material"])
        self.material_combo.blockSignals(False)
        self.larmor_spin.setValue(self.engine.larmor_khz)
        self._update_component_legend()              # imported material may be single vs double
        self._refresh_left_view()                    # imported constants rebuilt the averaged/fit view
        self._update_constants_panel()
        r = summary.get("r")
        hb = "with" if summary.get("has_hb_ref") else "no"
        rtxt = f" r={r:.3f}" if r is not None and np.isfinite(r) else ""
        self._set_status(f"Constants imported: material={summary['material']} "
                         f"CC={summary['CC']:.3g}{rtxt} ({hb} hole-burn ref) -- from {path}")
        self._append_warning(f"imported constants from {path}")

    # -- live update loop ----------------------------------------------------
    def _update_conn_indicator(self):
        """Green while sweeps keep arriving, red when they stop. If the plots look frozen, this
        tells you WHICH failure it is: red = no sweeps reaching us (LabVIEW/TCP dropped); green but
        frozen = the GUI itself stalled. It updates on the 20 Hz timer, independent of sweeps."""
        if self._last_recv_wall is None:
            self.conn_label.setText("● waiting for sweeps")
            self.conn_label.setStyleSheet("color:#ffb14d; font-size:9pt; font-weight:bold;")
            return
        dt = time.time() - self._last_recv_wall
        if dt < self._stale_after:
            self.conn_label.setText(f"● live   Δ{dt:0.0f}s   n={self.state.n_received}")
            self.conn_label.setStyleSheet("color:#5fd08a; font-size:9pt; font-weight:bold;")
        else:
            self.conn_label.setText(f"● NO DATA {dt:0.0f}s -- check LabVIEW / TCP")
            self.conn_label.setStyleSheet("color:#ff6f6f; font-size:9pt; font-weight:bold;")

    def _log_heartbeat(self):
        """Periodic liveness log so an overnight stop is diagnosable from logs/online_fitter.log."""
        n = self.state.n_received
        age = None if self._last_recv_wall is None else time.time() - self._last_recv_wall
        agestr = "never" if age is None else f"{age:.0f}s ago"
        delta = n - self._hb_prev_n if self._hb_prev_n >= 0 else n
        self._hb_prev_n = n
        stalled = age is not None and age > self._stale_after
        (log.warning if stalled else log.info)(
            "heartbeat: n_received=%d (+%d since last), last sweep %s, strip_pts=%d%s",
            n, delta, agestr, len(self._t), "  <-- NO NEW DATA" if stalled else "")

    def _drain(self):
        try:
            self._drain_inner()
        except Exception:                            # a bad sweep must never kill the refresh timer
            log.exception("exception in _drain (continuing)")

    def _drain_inner(self):
        self._update_conn_indicator()                # live/stale indicator (runs every timer tick)
        self._poll_update_constants()                # apply a finished Update Constants / tick its timer
        item = None
        for _ in range(200):                         # drain up to N/refresh so we never lag forever
            try:
                item = self.state.result_queue.get_nowait()
            except Exception:
                break
            self._append_history(item)
        if item is not None:
            self._last_ts = item.get("ts")           # data timestamp of the most recent sweep
            self._redraw_spectra(item)               # spectra: every sweep (live fit view)
            self._update_live(item["result"])        # big P/Q + live r/phase/chi2: every sweep
            self._update_constants_panel()           # CC + constants: only rewrites when they change
            self._redraw_strip()                     # strip: every sweep (auto-scale toggle handles manual axes)

    def _append_history(self, item):
        res = item["result"]
        # x = computer wall-clock time this line was grabbed + fit (POSIX seconds; the DateAxisItem
        # renders it as local date/time). Falls back to now() if the server didn't stamp it.
        x = item.get("recv_time")
        if x is None:
            x = time.time()
        self._n += 1
        self._last_recv_wall = time.time()
        self._t.append(x)                             # deque(maxlen): drops the oldest automatically
        w = max(1, int(self.roll_window))
        for key, _, _ in _SERIES:
            val = res.get(key)
            v = float(val) if val is not None and np.isfinite(val) else np.nan
            self._hist[key].append(v)
            # trailing average over the last w points using the CURRENT window; frozen once stored.
            rec = self._recent[key]
            if rec.maxlen != w:                       # window changed -> rebuild from the stored tail
                rec = collections.deque(list(self._hist[key])[-w:], maxlen=w)
                self._recent[key] = rec
            else:
                rec.append(v)
            finite = [u for u in rec if np.isfinite(u)]
            self._roll_hist[key].append(float(np.mean(finite)) if finite else np.nan)

    def _redraw_spectra(self, item):
        res = item["result"]
        self._last_item = item                       # remember for seeding the left views on toggle
        freqs = np.asarray(item["freqs"], float)
        # Left plot: view 0 = live raw, view 1 = live baseline-subtracted, view 2 = static (untouched).
        if self._left_view == 0:
            self.curve_raw.setData(freqs, np.asarray(item["raw"], float))
        elif self._left_view == 1:
            bsub = item.get("baseline_sub")
            if bsub is not None:
                self.curve_uc_avg.setData(freqs, np.asarray(bsub, float))
        flat = res.get("flat")
        if flat is not None:
            self.curve_flat.setData(freqs, np.asarray(flat, float))
        for curve, key in ((self.curve_fit, "fit"), (self.curve_cPp, "cPp"),
                           (self.curve_cPm, "cPm"), (self.curve_cSp, "cSp"), (self.curve_cSm, "cSm")):
            arr = res.get(key)
            if arr is None:
                curve.clear()
            else:
                curve.setData(freqs, np.asarray(arr, float))
        # Fit type + warning go to the side-panel label (NOT the plot title) so the plot column
        # width -- and thus the two top plots' equal sizing -- never changes when a warning fires.
        warn = res.get("warning") or res.get("error")
        ftype = res.get("fit_type", "")
        self.fit_type_label.setText(f"fit: {ftype}" + ("   ⚠" if warn else ""))
        self.fit_type_label.setStyleSheet(
            "color:#ffb14d; font-size:10pt;" if warn else "color:#9aa4ae; font-size:10pt;")
        if warn and warn != self._last_warning:      # append once per distinct message (don't spam every sweep)
            self._append_warning(warn)
            self._last_warning = warn
        elif not warn:
            self._last_warning = None

    def _redraw_strip(self):
        x = np.asarray(self._t, float)
        if self._strip_minutes and x.size:            # trailing time window: follow the newest point
            self.p_strip.setXRange(x[-1] - self._strip_minutes * 60.0, x[-1], padding=0)
        roll = self.roll_check.isChecked()
        for key, _, _ in _SERIES:
            y = np.asarray(self._hist[key], float)
            if not np.isfinite(y).any():         # nothing to plot yet -> keep it empty (avoids all-NaN warnings)
                self.strip_curves[key].clear()
                self.roll_curves[key].clear()
                continue
            self.strip_curves[key].setData(x, y, connect="finite")
            if roll:
                self.roll_curves[key].setData(x, np.asarray(self._roll_hist[key], float), connect="finite")
            else:
                self.roll_curves[key].clear()


