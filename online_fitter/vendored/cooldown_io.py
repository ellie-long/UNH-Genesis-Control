"""Reading and structuring NMR cooldown data files.

Two input shapes are supported:

* **Raw cooldown** (``data_record_*-fixed.csv``): tab-separated, 745 columns of
  instrument metadata + one NMR sweep per row. The columns that matter:
    - col  1 (idx 0)  Timestamp
    - col  2 (idx 1)  Seconds from Midnight
    - col 63 (idx 62) Holeburning Frequency (MHz)
    - col 65 (idx 64) Holeburning Power (dBm)
    - col 71 (idx 70) NMR Central Freq (MHz)
    - col 72 (idx 71) NMR Freq Span (MHz)
    - col 89 (idx 88) NMR Status  (Baseline / Polarization / TE / Tuning / Scanning)
    - col 92 (idx 91) NMR Number  (= number of sweep points, 451)
    - col 93+ (idx 92..) the NMR sweep, terminated by '---'/'Null' sentinels.

* **Flats** (``*SpinUpFlats.csv`` and the caches this tool writes): comma-separated
  with header ``Timestamp,P,Data``; row 2 is the frequency axis, rows 3+ are
  ``timestamp, P, <signal points>`` already baseline + cubic subtracted.

This module is pure (no prompts, no fitting) so it can be unit-tested and reused.
"""

import csv
import datetime as _dt
import os as _os
import re as _re
import warnings as _warnings
from dataclasses import dataclass, field as _dc_field

import numpy as np

# Data files are split at MIDNIGHT (one calendar day each), but experimental runs
# are not -- a spin-up, baseline set, or burn session routinely straddles midnight,
# so a line's matching baseline or its high-P context can live in the adjacent day's
# file (see docs/nmr_operations.tex). The "Seconds from Midnight" column therefore
# resets at 00:00 and cannot order data across the boundary. We compute an ABSOLUTE,
# continuous time axis from the timestamp string so adjacent files can be stitched.
_EPOCH = _dt.datetime(2000, 1, 1)
_TS_FMT = "%m/%d/%Y %I:%M:%S %p"


def _abs_seconds(ts):
    """Absolute seconds (from a fixed epoch) for one timestamp string; NaN if unparseable."""
    try:
        return (_dt.datetime.strptime(ts.strip(), _TS_FMT) - _EPOCH).total_seconds()
    except (ValueError, AttributeError):
        return np.nan

# Raw-cooldown columns are resolved by HEADER TITLE, not position: column order can
# change between data files but the titles are stable (see docs/nmr_operations.tex).
# Each entry is (accepted titles, Dec-2025 fallback index). The first title is canonical; the
# rest are older-DAQ aliases (2020-2021 used different names), tried in order.
_COLUMNS = {
    "timestamp":  (("Timestamp", "Time"), 0),
    "seconds":    (("Seconds from Midnight", "Timestamp (s)"), 1),
    "field_t":    (("Central Mag Field (T)",), 5),  # NaN in field/current => magnet off
    "current_a":  (("Magnet Current (A)",), 50),
    "hb_freq":    (("Holeburning Frequency (MHz)", "Holeburn Freq (MHz)"), 62),
    "hb_power":   (("Holeburning Power (dBm)", "Holeburn Power (dBm)"), 64),  # 2020 'Holeburn Power (mV)'
                                                    # is a different unit -- left unaliased (NaN) on purpose
    "centfreq":   (("NMR Central Freq (MHz)", "Central Freq (MHz)"), 70),    # WINDOW centre (not Larmor)
    "span":       (("NMR Freq Span (MHz)", "Freq Span (MHz)"), 71),
    "phase_tune": (("Phase Tune (V)",), 85),
    "if_atten":   (("IF Attenuation (dB)",), 86),   # part of the tune signature (scale segment)
    "vme_mode":   (("VME Mode",), 87),              # NMR amplifier mode; SCALE constant within a mode.
                                                    # (2020 DAQ logs no amplifier mode -> blank; main_fit
                                                    # treats blank as Operating, the per-epoch CC absorbs gain)
    "nmr_domain": (("NMR Domain",), None),          # "Real" / "Imaginary". DAQ QUIRK: when the operator
                                                    # switches to Imaginary the actual sweep span DOUBLES
                                                    # (the dispersive is ~2x wider) but the output keeps
                                                    # recording the OLD real span -- so for Imaginary rows
                                                    # the TRUE span = 2 x the recorded NMR Freq Span. We
                                                    # correct it at read time (read_cooldown).
    "status":     (("NMR Status",), 88),
    "online_p":   (("Area/Polarization", "Polarization (%)"), 89),  # live area-method P% ('46.23%');
                                                    # 2020 DAQ named it 'Polarization (%)'
    "nmr_number": (("NMR Number",), 91),            # sweep length; sweep starts the next col (modern)
    "sweep_col":  (("NMR", "NMR Data"), None),      # older DAQ: sweep STARTS here (no NMR Number -> infer width)
}

SENTINELS = {"", "---", "Null", "NaN"}


def resolve_columns(header):
    """Map each canonical column name to its index in ``header`` by title (case- and
    whitespace-insensitive, trying each alias), falling back to the Dec-2025 index if no title
    matches. ``sweep_start`` is the column after NMR Number (modern); when NMR Number is absent
    (older DAQ) the sweep starts at the NMR/NMR Data column and ``infer_width`` is set so the
    width is read from the row, not the (missing) NMR Number cell."""
    norm = {str(h).strip().lower(): i for i, h in enumerate(header)}

    def find(titles, default):
        for t in titles:
            if t.strip().lower() in norm:
                return norm[t.strip().lower()], True
        return default, False

    cols, found = {}, {}
    for k, (titles, default) in _COLUMNS.items():
        cols[k], found[k] = find(titles, default)
    older = not found["nmr_number"]                 # 2020-2021 DAQ: no NMR Number column
    if older:
        # the Dec-2025 fallback indices are meaningless in the older layout (and would land in
        # the appended sweep), so any column not matched by title is treated as ABSENT (None).
        for k in cols:
            if not found[k]:
                cols[k] = None
        cols["sweep_start"] = cols["sweep_col"] if cols["sweep_col"] is not None else 92
        cols["infer_width"] = True
    else:                                           # modern: width from the NMR Number column
        cols["sweep_start"], cols["infer_width"] = cols["nmr_number"] + 1, False
    return cols


def _infer_width(row, start):
    """Sweep width when there is no NMR Number column: the count of contiguous numeric cells from
    ``start`` to the first blank/sentinel/non-numeric (older DAQ pads the row after the sweep)."""
    n = 0
    for x in row[start:]:
        x = x.strip()
        if x in SENTINELS:
            break
        try:
            float(x)
        except (TypeError, ValueError):
            break
        n += 1
    return n


@dataclass
class Cooldown:
    """A parsed raw cooldown: parallel arrays, one entry per data row."""
    timestamps: list                      # original timestamp strings
    seconds: np.ndarray                   # seconds from midnight
    status: list                          # NMR Status strings
    centfreq: np.ndarray
    span: np.ndarray
    hb_freq: np.ndarray
    hb_power: np.ndarray
    phase_tune: np.ndarray                # "Phase Tune (V)" -- phase-voltage setting
    sweeps: np.ndarray                    # shape (nrows, width)
    width: int
    field_t: np.ndarray = None            # "Central Mag Field (T)"; NaN => magnet off
    current_a: np.ndarray = None          # "Magnet Current (A)"; NaN => magnet off
    online_p: np.ndarray = None           # "Area/Polarization" live area-method P (%)
    if_atten: np.ndarray = None           # "IF Attenuation (dB)"; part of the tune signature
    vme_mode: list = None                 # "VME Mode" strings; scale is fixed within a mode
    nmr_domain: list = None               # "NMR Domain": "Real" / "Imaginary" (see span DAQ-quirk below)
    source: str = ""
    skipped: dict = _dc_field(default_factory=dict)   # rows dropped at read time, by reason
                                          # (e.g. 'width-mismatch': per-row NMR Number != the file's
                                          # established sweep width -- see read_cooldown / E1-B1)

    def freq_axis(self, i):
        """Frequency axis for row ``i`` (uses that row's centfreq/span)."""
        return self.centfreq[i] + self.span[i] * (np.arange(self.width) / self.width - 0.5)


def _crop_cooldown(cd):
    """If ``COOLDOWN_CROP=<MHz>`` is set (per wide-window cool-downs, e.g. ND3 at 0.65, or the d-prop
    runs whose 0.8 / 1.6 windows carry heavy EDGE artifacts), trim every sweep to the central CROP
    MHz of the analyzable (span ~= CROP/keep) window. The NMR bins + freq span are computed FIRST
    (already 2x-corrected for Imaginary upstream); this then leaves off the bins that fall outside
    the narrower cut window (same central freq, new span). The dominant analyzable span ``asp`` (e.g.
    0.8) is cropped to CROP (e.g. 0.65); its IMAGINARY 2x partner ``2*asp`` (e.g. 1.6) is cropped by
    the SAME central-bin fraction to 2*CROP (e.g. 1.30), so the real<->imaginary 2x relationship is
    preserved. Lines whose span can't represent the crop (narrower windows, e.g. 0.4) are set to NaN
    (no-signal). One uniform width with a correct freq axis (span scaled by keep/W, centfreq
    unchanged for a central trim) -> reference, cascade, and main_fit all see consistent cropped
    data. No-op when unset. Applied once at read time, so it propagates through read_cooldowns."""
    env = _os.environ.get("COOLDOWN_CROP")
    if not env or cd.width <= 0 or len(cd.timestamps) == 0:
        return cd
    crop = float(env)
    sp = np.asarray(cd.span, dtype=float)
    from collections import Counter
    spans = Counter(round(float(sp[i]), 3) for i, s in enumerate(cd.status)
                    if s == "Polarization" and np.isfinite(sp[i]) and sp[i] >= crop - 0.02)
    if not spans:
        return cd
    asp = spans.most_common(1)[0][0]                       # the analyzable span (e.g. 0.8 for ND3)
    W = cd.width
    keep = min(W, int(round(W * crop / asp)))
    if keep >= W:
        return cd
    lo = (W - keep) // 2
    cd.sweeps = cd.sweeps[:, lo:lo + keep].copy()
    at_real = np.isfinite(sp) & (np.abs(sp - asp) < 0.02)          # real analyzable window (e.g. 0.8)
    at_imag = np.isfinite(sp) & (np.abs(sp - 2.0 * asp) < 0.02)    # imaginary 2x partner (e.g. 1.6)
    keepln = at_real | at_imag                            # both crop by the SAME central-bin fraction
    cd.sweeps[~keepln] = np.nan                            # narrower/other windows can't represent the crop
    newsp = sp.copy()
    newsp[at_real] = asp * keep / W                        # real -> CROP        (e.g. 0.65)
    newsp[at_imag] = 2.0 * asp * keep / W                 # imaginary -> 2*CROP (e.g. 1.30), centfreq unchanged
    newsp[~keepln] = np.nan
    cd.span = newsp
    cd.width = keep
    return cd


DEBAND_BAND_TOL = 0.02    # only rescale a row whose |b-1| exceeds this (a genuine band); rows within
                          #   tol of central are left EXACTLY unchanged, so clean data is byte-identical
DEBAND_WIN = 61           # rolling-median window (rows) for the central (k=0) Q-curve level
DEBAND_MODE_TOL = 0.02    # k=0 neighbourhood: only rows with |std/mode - 1| below this feed the rolling
                          #   central, so a long band STRETCH (which sits at |b-1| >= ~0.045 for the
                          #   closest n=20,k=1 band) can never capture the central estimate (E1-B2)


def _mode_level(vals, lo=0.5, hi=1.5, nbins=201):
    """Dominant MODE of a positive amplitude distribution: histogram peak of vals/median over
    [lo, hi] (0.5% bins), refined as the median of the rows in the peak bin's neighbourhood.
    The k=0 (unbanded) rows are the most common level in every observed banded run, so the
    mode is the k=0 Q-curve amplitude even when band STRETCHES are long (unlike a rolling
    median, which tracks INTO any stretch longer than ~half its window -- E1-B2)."""
    v = np.asarray(vals, float)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0:
        return np.nan
    med = float(np.median(v))
    if med <= 0:
        return np.nan
    rel = v / med
    hist, edges = np.histogram(rel, bins=np.linspace(lo, hi, nbins))
    k = int(np.argmax(hist))
    sel = (rel >= edges[k] - 0.01) & (rel <= edges[k + 1] + 0.01)
    return med * float(np.median(rel[sel])) if sel.any() else med


def deband_sweeps(cd):
    r"""Undo the old-DAQ averaging-count artifact (see \code{docs/averaging\_artifact\_technote}): a
    mis-counted divisor stored sum/(n+k) instead of sum/n, scaling the WHOLE sweep by b=n/(n+k) per row
    -> parallel bands in P. The band is read off a POLARIZATION-INDEPENDENT amplitude -- the std of the
    WING bins (pure Q-curve background, no deuteron signal) -- against a robust central (the k=0 level).

    Central estimator (MODE-ANCHORED, E1-B2): per tune segment the k=0 level is first identified as
    the dominant MODE of the wing-std distribution (histogram peak; k=0 is the most common band), and
    the rolling median (window DEBAND_WIN) is computed ONLY over rows within DEBAND_MODE_TOL of that
    mode, interpolated across the excluded rows. Slow Q-curve drift is still tracked (the near-mode
    rows carry it), but a CONTIGUOUS BAND STRETCH -- 40-60 same-band rows, longer than half the median
    window -- can no longer capture the central. (The previous plain rolling median tracked INTO any
    stretch longer than ~DEBAND_WIN/2, leaving such stretches uncorrected; a claim that the median was
    'unbiased by band stretches' was demonstrated false and is retracted. Regression:
    fitter/test_cooldown_io.py ports the 15/40/60-row stress case.) Each banded sweep is then rescaled
    by central/std (= 1/b) BEFORE any baseline subtraction. No n/k integer snap (n varies through a
    run, even at fixed dt).

    LIMITATIONS: drift larger than DEBAND_MODE_TOL of the mode within ONE tune segment would read
    as banding (real tune changes start a new segment, so per-segment drift is small); a band
    factor outside (0.4, 2.5) or beyond _mode_level's [0.5, 1.5] histogram window is left
    uncorrected (observed bands are n/(n+k) near 1).

    OPT-IN via ``COOLDOWN\_DEBAND`` (truthy to enable; default OFF -> returns ``cd`` untouched, so modern
    cool-downs incl. the recent-3 are byte-identical). Auto-detect was rejected: a modern cool-down's
    wing std varies for legitimate reasons (many windows, no-NMR rows) so "off-central spread" cannot
    distinguish banding from normal variation (2026-06 reads a higher off-band fraction than the banded
    2020-09). As a second guard, only rows with |b-1| > \code{DEBAND\_BAND\_TOL} are rescaled, so even a
    mistaken enable on clean data leaves it (near-)identical."""
    env = _os.environ.get("COOLDOWN_DEBAND")
    if not env or env == "0":                                # OPT-IN: default off -> untouched
        return cd
    sw = cd.sweeps
    if sw is None or sw.size == 0 or cd.width < 50 or len(sw) < 2 * DEBAND_WIN:
        return cd
    # band amplitude = std of the Q-curve WING bins: the band scales the whole sweep (so the wing too)
    # but the wing is PURE BACKGROUND, not polarization. The wing is kept OUTSIDE the deuteron signal,
    # no closer to the line centre than 6*omegaQ2 (the full Pake half-width) -- keyed by material and the
    # per-row span -- so no signal leaks in. Narrow windows where the signal fills the span fall back to
    # a minimal outermost wing.
    import deuteron_model as _dm
    _mat = _dm.MATERIALS.get(_os.environ.get("COOLDOWN_MATERIAL") or
                             "irradiated d-butanol (Dec 2025, 13th/15th)", {})
    wq2 = _mat.get("omegQ2") or _mat.get("omegQ1") or 0.026     # MHz: full Pake half-width = 6*wq2
    W = cd.width
    amp = np.full(len(sw), np.nan)
    spans = np.round(np.asarray(cd.span, float), 3)
    for s_val in np.unique(spans[np.isfinite(spans)]):
        if s_val <= 0:
            continue
        rm = spans == s_val
        nwf = 0.5 - 6.0 * wq2 / s_val                          # wing fraction: edge inward to 6*wq2 from centre
        nw = int(round(nwf * W)) if nwf > 0 else 0
        nw = min(nw, W // 2 - 1) if nw >= 8 else max(8, int(round(0.06 * W)))   # fallback: minimal outer wing
        wings = np.concatenate([sw[rm, :nw], sw[rm, W - nw:]], axis=1)
        amp[rm] = np.nanstd(wings, axis=1)
    valid = np.isfinite(amp) & (amp > 0)
    if valid.sum() < 2 * DEBAND_WIN:
        return cd
    # tune-segment ids (the Q-curve level is constant within a segment: centfreq, span, VME mode)
    cf = np.round(np.asarray(cd.centfreq, float), 3)
    sp = np.round(np.asarray(cd.span, float), 3)
    vme = cd.vme_mode if cd.vme_mode is not None else [""] * len(sw)
    key = list(zip(cf, sp, [str(v) for v in vme]))
    seg = np.zeros(len(sw), int)
    for i in range(1, len(sw)):
        seg[i] = seg[i - 1] + (key[i] != key[i - 1])
    try:
        from scipy.ndimage import median_filter
    except Exception:
        return cd
    central = np.full(len(sw), np.nan)
    for s in np.unique(seg):
        idx = np.where(seg == s)[0]
        av = amp[idx]
        fin = np.isfinite(av)
        if not fin.any():
            continue
        # MODE-ANCHORED central (E1-B2): identify the k=0 level as the histogram mode, feed the
        # rolling median ONLY with rows near the mode, and interpolate the central across the
        # excluded (banded) rows -- so a contiguous band stretch longer than half the window can
        # never capture the estimate, while slow Q-curve drift is still tracked by the k=0 rows.
        mode = _mode_level(av)
        near = fin & np.isfinite(mode) & (np.abs(av / mode - 1.0) < DEBAND_MODE_TOL) \
            if np.isfinite(mode) and mode > 0 else fin
        if near.sum() < 3:                                    # degenerate segment: flat central
            central[idx] = mode if np.isfinite(mode) and mode > 0 else np.nanmedian(av)
            continue
        nv = av[near]
        w = min(DEBAND_WIN, len(nv) - (len(nv) + 1) % 2)      # odd window <= near-mode row count
        cn = median_filter(nv, size=max(1, w), mode="nearest")
        central[idx] = np.interp(np.arange(len(av)), np.where(near)[0], cn)
    b = amp / central
    # only rescale GENUINE bands: |b-1| > tol, b physical. Near-central rows untouched (factor exactly 1).
    ok = valid & np.isfinite(central) & (central > 0)
    oob = ok & ((b <= 0.4) | (b >= 2.5))
    if oob.any():                                     # W9: never silently skip an unphysical factor
        _warnings.warn(f"deband: {int(oob.sum())} row(s) with band factor outside (0.4, 2.5) left "
                       "uncorrected (see LIMITATIONS in the deband_sweeps docstring)")
    band = ok & (b > 0.4) & (b < 2.5) & (np.abs(b - 1.0) > DEBAND_BAND_TOL)
    factor = np.where(band, central / np.where(amp > 0, amp, 1.0), 1.0)
    cd.sweeps = sw * factor[:, None]
    return cd


def _to_float(s, default=np.nan):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _to_pct(s, default=np.nan):
    """Parse a percentage cell like '46.23%' -> 46.23 (the online Area/Polarization)."""
    try:
        return float(str(s).strip().rstrip("%").strip())
    except (TypeError, ValueError):
        return default


MAX_GLITCH_RUN = 300       # fill a glitched run only if it is at most this many lines long.
                           # Set above the longest real dropout seen (2026-06: a whole ssRF
                           # session blanks centfreq for up to ~211 lines, all bracketed by the
                           # same value); a longer run stays 'glitch' as a backstop.


def _fill_bracketed_num(values, max_run=MAX_GLITCH_RUN):
    """Fill NaN runs in a numeric column when the values bracketing the run are EQUAL and the
    run is at most ``max_run`` lines. Fast-DAQ acquisition (e.g. 1 s/row in 2026-06) drops an
    otherwise-constant field on a few rows (NMR centfreq/span comes out blank on a normal NMR
    row -> a NaN freq axis -> a spurious 'glitch'); a short dropout bracketed by the same
    value before and after is that value. A genuine change (before != after), an edge run, or
    a long run (> max_run lines) is left NaN (stays 'glitch')."""
    a = np.asarray(values, float).copy()
    n = len(a); i = 0
    while i < n:
        if np.isnan(a[i]):
            j = i
            while j < n and np.isnan(a[j]):
                j += 1
            before = a[i - 1] if i > 0 else np.nan
            after = a[j] if j < n else np.nan
            if (j - i) <= max_run and np.isfinite(before) and np.isfinite(after) and before == after:
                a[i:j] = before
            i = j
        else:
            i += 1
    return a


def _status_glitch(s):
    """A status cell that is empty or reads as a number (e.g. '0.0') is a fast-DAQ glitch,
    not a real NMR Status word."""
    s = str(s).strip()
    if not s:
        return True
    try:
        float(s); return True
    except ValueError:
        return False


def _fill_bracketed_status(status, max_run=MAX_GLITCH_RUN):
    """Same bracketed-by-equal-neighbours fill (capped at ``max_run`` lines) for the NMR
    Status column (glitched cells read as a number / blank)."""
    out = list(status); n = len(out); i = 0
    while i < n:
        if _status_glitch(out[i]):
            j = i
            while j < n and _status_glitch(out[j]):
                j += 1
            before = out[i - 1] if i > 0 else None
            after = out[j] if j < n else None
            if (j - i) <= max_run and before is not None and after is not None and before == after:
                for k in range(i, j):
                    out[k] = before
            i = j
        else:
            i += 1
    return out


def detect_input_type(path):
    """Return 'flats' or 'raw' by sniffing the first line."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        first = fh.readline()
    if "\t" in first:
        return "raw"
    head = first.strip().strip('"').lower()
    if head.startswith("timestamp") and "data" in first.lower():
        return "flats"
    # Fall back on delimiter: comma-only -> flats, else raw.
    return "flats" if "," in first else "raw"


def read_cooldown(path, _crop=True):
    """Parse a raw cooldown file into a :class:`Cooldown`.

    ``_crop=False`` skips the COOLDOWN_CROP central-trim (used by read_cooldowns so the
    crop is applied ONCE to the stitched session -- per-file cropping can pick different
    widths when a setup day has no analyzable-span lines, which then fails the stitch)."""
    timestamps, seconds, status = [], [], []
    centfreq, span, hb_freq, hb_power, phase_tune = [], [], [], [], []
    field_t, current_a, online_p, vme_mode, if_atten, nmr_domain = [], [], [], [], [], []
    sweeps = []
    width = None
    skipped = {}                             # rows dropped at read time, by reason
    _mismatch_widths = {}                    # width-mismatch detail: {row width: count} (for the warning)

    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        first = fh.readline(); fh.seek(0)        # Excel re-save can switch the delimiter TAB -> comma
        delim = "\t" if first.count("\t") >= first.count(",") else ","
        reader = csv.reader(fh, delimiter=delim)
        header = next(reader, None)              # header row
        c = resolve_columns(header or [])
        cstart, cnum, infer = c["sweep_start"], c["nmr_number"], c["infer_width"]
        need = max(x for x in (cstart, c["status"], c["centfreq"], c["span"]) if x is not None)

        def cell(row, key, default=np.nan):
            k = c[key]
            return row[k] if (k is not None and k < len(row)) else default   # k None => column absent

        def _row_width(row):
            if len(row) <= need:
                return 0
            w = _infer_width(row, cstart) if infer else int(_to_float(row[cnum], 0))
            return w if w > 0 else 0

        # Establish the file's sweep width from the first REAL sweep (PASS 1). A single-point
        # w==1 "sweep" is the DAQ's no-data placeholder (NMR Number=1, all-zero), written while
        # tuning and after a mid-file DAQ crash/reboot. When such rows LEAD the file they must
        # NOT pin the width to 1 and then reject every real 451-point sweep after them -- this
        # is what happened on 2026-06-15 (1448 leading Tuning placeholders hid 2645 real sweeps).
        # Take the first width > 1; fall back to the first positive width only if the file is ALL
        # placeholders. For every well-formed file the first data row is already a real sweep, so
        # `width` here equals the old "first valid row wins" value exactly -> kept rows, their
        # order, and the mismatch count stay byte-identical (verified across all cool-downs).
        fallback = None
        for row in reader:
            w = _row_width(row)
            if w <= 0:
                continue
            if fallback is None:
                fallback = w
            if w > 1:
                width = w
                break
        if width is None:
            width = fallback
        fh.seek(0)
        reader = csv.reader(fh, delimiter=delim)
        next(reader, None)                                # re-skip the header after the rewind

        for row in reader:                                # PASS 2: keep rows at the established width
            if len(row) <= need:
                continue
            w = _infer_width(row, cstart) if infer else int(_to_float(row[cnum], 0))
            if w <= 0:
                continue
            if w != width:
                # MIXED SWEEP WIDTHS within one file (E1-B1). Previously silent data corruption:
                # a WIDER row was truncated to the first `width` points and mis-gridded (its full
                # recorded span mapped onto too few bins -> every bin frequency wrong), while a
                # NARROWER row fell through to the NaN check and was silently dropped. Mixed widths
                # are an error condition (read_cooldowns RAISES on a cross-FILE mismatch); within a
                # file we skip the row and COUNT it, with one warning naming the file (below). The
                # leading placeholders deferred in PASS 1 (w=1 vs a real width) are skipped here too.
                skipped["width-mismatch"] = skipped.get("width-mismatch", 0) + 1
                _mismatch_widths[w] = _mismatch_widths.get(w, 0) + 1
                continue
            sweep = row[cstart:cstart + width]
            if len(sweep) < width:
                continue
            vals = np.array([_to_float(x, np.nan) for x in sweep], dtype=float)
            if np.isnan(vals).any():             # incomplete/sentinel-truncated sweep
                continue
            timestamps.append(cell(row, "timestamp", ""))
            seconds.append(_to_float(cell(row, "seconds")))
            status.append(cell(row, "status", ""))
            centfreq.append(_to_float(cell(row, "centfreq")))
            span.append(_to_float(cell(row, "span")))
            hb_freq.append(_to_float(cell(row, "hb_freq")))
            hb_power.append(_to_float(cell(row, "hb_power")))
            phase_tune.append(_to_float(cell(row, "phase_tune")))
            field_t.append(_to_float(cell(row, "field_t")))
            current_a.append(_to_float(cell(row, "current_a")))
            online_p.append(_to_pct(cell(row, "online_p")))
            vme_mode.append(str(cell(row, "vme_mode", "")).strip())
            if_atten.append(_to_float(cell(row, "if_atten")))
            nmr_domain.append(str(cell(row, "nmr_domain", "")).strip())
            sweeps.append(vals)

    if skipped.get("width-mismatch"):
        _warnings.warn(
            f"{path}: skipped {skipped['width-mismatch']} row(s) whose sweep width differs from the "
            f"file's established width {width} (row widths {_mismatch_widths}); mixed widths within "
            f"one file cannot share a frequency grid (cf. the cross-file raise in read_cooldowns)")

    # Absolute, continuous time axis from the timestamps (so adjacent day files can
    # be stitched across midnight). Fall back to the file's midnight + the raw
    # "Seconds from Midnight" column for any unparseable timestamp.
    raw = np.array(seconds)
    abs_secs = np.array([_abs_seconds(t) for t in timestamps])
    if np.isnan(abs_secs).any():
        valid = np.where(np.isfinite(abs_secs) & np.isfinite(raw))[0]
        if len(valid):
            midnight = abs_secs[valid[0]] - raw[valid[0]]    # file's 00:00 epoch
            abs_secs = np.where(np.isfinite(abs_secs), abs_secs, midnight + raw)

    # Fast-DAQ dropout repair: a glitched centfreq/span/status bracketed by EQUAL neighbours
    # is that value (see _fill_bracketed_num). Recovers normal NMR rows that would otherwise
    # be mis-tagged 'glitch' (NaN freq axis) on high-rate 2026-06 data.
    centfreq = _fill_bracketed_num(centfreq)
    span = _fill_bracketed_num(span)
    status = _fill_bracketed_status(status)
    vme_mode = _fill_bracketed_status(vme_mode)   # '0'/'0.0'/blank VME-Mode glitch -> neighbour mode
    if_atten = _fill_bracketed_num(if_atten)      # intermittent IF Atten (a scale term) -> neighbour
    nmr_domain = _fill_bracketed_status(nmr_domain)
    # DAQ QUIRK (see COLS["nmr_domain"]): in Imaginary mode the actual span is 2x the RECORDED span.
    # Correct it here so the freq axis, epoch key, and baseline window all use the TRUE span. Real rows
    # (and any cool-down without an NMR Domain column -> blank) are unchanged, so real runs are
    # byte-identical.
    _imag = np.array([str(d).strip().lower().startswith("imag") for d in nmr_domain])
    span = np.where(_imag, 2.0 * np.asarray(span, float), np.asarray(span, float))

    cd = Cooldown(
        timestamps=timestamps,
        seconds=abs_secs,
        status=status,
        centfreq=np.array(centfreq),
        span=np.array(span),
        hb_freq=np.array(hb_freq),
        hb_power=np.array(hb_power),
        phase_tune=np.array(phase_tune),
        sweeps=np.array(sweeps) if sweeps else np.zeros((0, width or 0)),
        width=width or 0,
        field_t=np.array(field_t),
        current_a=np.array(current_a),
        online_p=np.array(online_p),
        if_atten=np.array(if_atten),
        vme_mode=vme_mode,
        nmr_domain=nmr_domain,
        source=path,
        skipped=skipped,
    )
    # Field from magnet current when the field-in-Tesla column is ABSENT (the old 2020-2021 DAQ logs
    # only "Magnet Current (A)"): B = 0.1027405*I + 3.497875e-5 (T). This lets omegD prediction, the
    # magnet-off gate, and _on_res work on those runs (else every line is a spurious no-NMR). Recent
    # runs log field_t, so this is a no-op for them -> byte-identical. Same formula as magnet_up_sessions.
    _ft = np.asarray(cd.field_t, dtype=float)
    if not np.isfinite(_ft).any():
        _ia = np.asarray(cd.current_a, dtype=float)
        if np.isfinite(_ia).any():
            cd.field_t = 0.1027405 * _ia + 3.497875e-5
    cd = deband_sweeps(cd)                                 # opt-in (COOLDOWN_DEBAND); no-op otherwise.
    return _crop_cooldown(cd) if _crop else cd             # de-band on the full sweep BEFORE any crop


def adjacent_files(path, before=1, after=1):
    """Day files around ``path`` in its directory, in date order, for stitching a run
    that crosses midnight. Matches the ``...YYYY-MM-DD...`` date in the file name and
    returns the contiguous neighbours (calendar-adjacent days) that exist, including
    ``path`` itself. Falls back to just ``[path]`` if the name has no parseable date."""
    d = _os.path.dirname(_os.path.abspath(path))
    name = _os.path.basename(path)
    m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", name)
    if not m:
        return [path]
    day0 = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    prefix, suffix = name[:m.start()], name[m.end():]
    out = []
    for k in range(-before, after + 1):
        day = day0 + _dt.timedelta(days=k)
        cand = _os.path.join(d, f"{prefix}{day.isoformat()}{suffix}")
        if _os.path.exists(cand):
            out.append(cand)
    return out or [path]


def magnet_up_sessions(day_keys, path_of, up_t=1.0):
    """Group consecutive day keys into magnet-up SESSIONS. The magnet staying ramped (central field
    > ``up_t`` Tesla, or computed from current) across a day boundary means physics ran continuously
    past midnight -> ONE session; a magnet ramp-down between two days is the real epoch boundary
    (they stopped, then resumed). Returns ``list[list[day_key]]`` in order. ``path_of(day_key)``
    gives the data_record path. Falls back to one session per day wherever the field/current is
    unavailable (e.g. the oldest cool-downs). (Reads each day once for its boundary field; could be
    a lightweight field-only read later.)"""
    keys = list(day_keys)
    if len(keys) <= 1:
        return [[k] for k in keys]
    bf = {}                                          # day -> (first finite field, last finite field)
    for d in keys:
        try:
            cd = read_cooldown(path_of(d))
            ft = np.asarray(cd.field_t, dtype=float)
            if not np.isfinite(ft).any():            # no field column -> try field from current
                ia = np.asarray(cd.current_a, dtype=float)
                ft = 0.1027405 * ia + 3.497875e-5 if np.isfinite(ia).any() else ft
            fin = ft[np.isfinite(ft)]
            bf[d] = (float(fin[0]), float(fin[-1])) if fin.size else (None, None)
        except Exception:
            bf[d] = (None, None)
    out = [[keys[0]]]
    for prev, cur in zip(keys, keys[1:]):
        up = (bf[prev][1] is not None and bf[prev][1] > up_t      # magnet up at end of N
              and bf[cur][0] is not None and bf[cur][0] > up_t)   # AND at start of N+1
        (out[-1].append(cur) if up else out.append([cur]))
    return out


def read_cooldowns(paths):
    """Parse and STITCH several day files into one continuous :class:`Cooldown`.

    Files are concatenated in absolute-time order (the seconds axis is epoch-based,
    so it is continuous across midnight). A baseline taken before midnight in one
    file is then available to a line after midnight in the next, and a run that
    spans the boundary is detected as a single run. All files must share the sweep
    width. Pass one path for the single-file case."""
    if isinstance(paths, str):
        paths = [paths]
    cds = [read_cooldown(p, _crop=False) for p in paths]   # crop ONCE on the stitched session (below)
    cds = [c for c in cds if len(c.timestamps) > 0]
    if not cds:
        return read_cooldown(paths[0])
    if len(cds) == 1:
        return _crop_cooldown(cds[0])
    widths = {c.width for c in cds}
    if len(widths) > 1:
        raise ValueError(f"cannot stitch files with different sweep widths: {widths}")
    order = np.argsort([c.seconds[0] for c in cds])
    cds = [cds[k] for k in order]
    cat = lambda attr: np.concatenate([getattr(c, attr) for c in cds])
    stitched = Cooldown(
        timestamps=[t for c in cds for t in c.timestamps],
        seconds=cat("seconds"),
        status=[s for c in cds for s in c.status],
        centfreq=cat("centfreq"), span=cat("span"),
        hb_freq=cat("hb_freq"), hb_power=cat("hb_power"),
        phase_tune=cat("phase_tune"),
        sweeps=np.concatenate([c.sweeps for c in cds], axis=0),
        width=cds[0].width,
        field_t=cat("field_t"), current_a=cat("current_a"), online_p=cat("online_p"),
        if_atten=cat("if_atten"),
        vme_mode=[m for c in cds for m in (c.vme_mode or [])],
        nmr_domain=[d for c in cds for d in (c.nmr_domain or [])],
        source=" + ".join(c.source for c in cds),
        skipped={k: sum((c.skipped or {}).get(k, 0) for c in cds)
                 for k in {k for c in cds for k in (c.skipped or {})}},
    )
    return _crop_cooldown(stitched)    # crop ONCE on the whole session (uniform width)


@dataclass
class Run:
    """A contiguous block of rows sharing one NMR Status."""
    status: str
    start: int          # inclusive row index
    stop: int           # exclusive row index
    index: int = 0      # position among runs of the same status

    @property
    def count(self):
        return self.stop - self.start


def detect_runs(cooldown, statuses=None):
    """Group consecutive rows of equal status into :class:`Run` blocks.

    If ``statuses`` is given, only runs whose status is in that set are returned.
    Each returned run is also numbered within its own status (``index``).
    """
    runs = []
    status = cooldown.status
    i = 0
    n = len(status)
    while i < n:
        j = i
        while j < n and status[j] == status[i]:
            j += 1
        runs.append(Run(status=status[i], start=i, stop=j))
        i = j
    # number runs within each status
    counters = {}
    for r in runs:
        counters[r.status] = counters.get(r.status, 0)
        r.index = counters[r.status]
        counters[r.status] += 1
    if statuses is not None:
        runs = [r for r in runs if r.status in statuses]
    return runs


def average_baseline(cooldown, run, trim=1):
    """Average the sweeps in a Baseline run, dropping ``trim`` from each edge.

    The first and last sweeps of a baseline run are contaminated by the field
    switchover, so they are excluded (Plan / experimental note).
    """
    a = run.start + trim
    b = run.stop - trim
    if b <= a:                       # run too short to trim; use it all
        a, b = run.start, run.stop
    return cooldown.sweeps[a:b].mean(axis=0)


def average_sweeps(cooldown, indices):
    """Mean sweep over the given row indices."""
    return cooldown.sweeps[list(indices)].mean(axis=0)


def read_flats(path):
    """Parse a flats file. Returns (freqs, rows) where rows is a list of dicts
    with keys 'timestamp', 'P' (may be None), 'signal' (np.ndarray)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        raw = list(csv.reader(fh))
    # row 0 = header, row 1 = frequency axis, rows 2+ = data
    freqs = np.array([_to_float(x) for x in raw[1] if x not in SENTINELS])
    width = len(freqs)
    rows = []
    for r in raw[2:]:
        if len(r) < 2:
            continue
        timestamp = r[0]
        P = _to_float(r[1], None)
        signal = np.array([_to_float(x, np.nan) for x in r[2:2 + width]], dtype=float)
        if len(signal) < width or np.isnan(signal).any():
            continue
        rows.append({"timestamp": timestamp, "P": P, "signal": signal})
    return freqs, rows
