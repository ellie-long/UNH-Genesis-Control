"""Era alias table: canonical channel name -> data_record header titles.

The first title is the modern (2022+) spelling; later entries are older-DAQ
names, tried in order.  Matching is case- and whitespace-insensitive.

Header history (surveyed across all 15 cool-downs in Optimizied-McClellan-Fits):

* 2020-09, 2020-12 -- ``Time``, ``CCS.F1 (K)``, ``CCS.M1 (K)``, single
  ``Manometer (Torr)``, ``RF Status``/``Mmwaves Frequency (GHz)``/
  ``Mmwaves Power (dBm)``, ``UCA Voltage (V)``, ``Polarization (%)``;
  no ColdCathode, no Fringe Field; ``Holeburn Power`` is in **mV** (not
  aliased -- different unit).
* 2021-09 -- modern thermometry names, ``ColdCathode.Pres (Torr)``,
  ``Fringe Field (g)``, ``Manometer-Hi/Lo``; T1-T3 split as ``0-T1``/``1-T1``.
* 2022-05 onward -- the 91-93 named-column layout (``NMR Number`` present).
* 2024-08 onward -- ``Fridge He Level (")``, ``T1/T2/T3 (K)``.
"""

from __future__ import annotations

#: canonical -> (header titles...)
CHANNELS: dict[str, tuple[str, ...]] = {
    "timestamp": ("Timestamp", "Time"),
    # fridge thermometry (LS224 #1)
    **{f"F{i}": (f"F{i} (K)", f"CCS.F{i} (K)") for i in range(1, 12)},
    # magnet thermometry (LS224 #2)
    **{f"M{i}": (f"M{i} (K)", f"CCS.M{i} (K)") for i in range(1, 6)},
    "T1": ("T1 (K)", "CCX.T1 (K)", "0-T1 (K)"),
    "T2": ("T2 (K)", "CCCS.T2 (K)", "0-T2 (K)"),
    "T3": ("T3 (K)", "CCX.T3 (K)", "0-T3 (K)"),
    # pressures
    "manometer_hi": ("Manometer-Hi (Torr)",),
    "manometer_lo": ("Manometer-Lo (Torr)",),
    "manometer": ("Manometer (Torr)",),                 # 2020 single-range
    "vp_range": ("Vapor Pressure Range",),
    "vp_temp": ("Vapor Pressure Temp (K)", "Vapor Pressure Temperature (K)"),
    "roots_pressure": ("Roots Pressure (Torr)",),
    "cold_cathode": ("ColdCathode.Pres (Torr)",),
    "pirani_v": ("Pirani (Voltage)",),
    "ionivac": ("Ionivac (Torr)",),
    # field / magnet
    "fringe_g": ("Fringe Field (g)",),
    "ps_current": ("PS Current (A)",),
    "magnet_current": ("Magnet Current (A)",),
    "central_field": ("Central Mag Field (T)",),
    # helium levels
    "fridge_he_level": ('Fridge He Level (")', "Fridge He Level"),
    "magnet_lhe_level": ('Magnet LHe Level (")', 'Helium Level (")'),
    # flows
    "separator_flow": ("Seperator Flow (SLM)",),
    "roots_flow_l": ("Roots Flow L (SLM)", "Roots Flow (SLM)"),
    "roots_flow_r": ("Roots Flow R (SLM)",),
    # microwave
    "mw_rf_status": ("MMwaves RF Status", "RF Status"),
    "mw_freq_ghz": ("MMwaves Freq (GHz)", "Mmwaves Frequency (GHz)"),
    "mw_power_db": ("MMwaves Power (dB)", "Mmwaves Power (dBm)"),
    "mw_freq_set1": ("mmWave Freq Setting 1 (GHz)",),
    "uca_v": ("UCA (V)", "UCA Voltage (V)"),
    # ssRF / holeburning
    "hb_freq_mhz": ("Holeburning Frequency (MHz)", "Holeburn Freq (MHz)"),
    "hb_power_dbm": ("Holeburning Power (dBm)",),
    # NMR bookkeeping
    "nmr_centfreq": ("NMR Central Freq (MHz)", "Central Freq (MHz)"),
    "nmr_span": ("NMR Freq Span (MHz)", "Freq Span (MHz)"),
    "nmr_domain": ("NMR Domain",),
    "vme_mode": ("VME Mode",),
    "nmr_status": ("NMR Status",),
    "online_p": ("Area/Polarization", "Polarization (%)"),
    "nmr_number": ("NMR Number",),
    "diode_tune": ("Diode Tune (V)",),
    "phase_tune": ("Phase Tune (V)",),
}

#: channels kept as strings (everything else parses to float)
STRING_CHANNELS = frozenset({"timestamp", "vp_range", "nmr_domain", "vme_mode", "nmr_status"})

#: string tokens the DAQ writes instead of a number
MISSING_TOKENS = frozenset({"", "nan", "null", "---", "off", "over", "under", "inf", "-inf"})

#: The DAQ logs this MMwaves Freq when the source is off / unreadable.
MW_FREQ_OFF_SENTINEL = -257.674453125

#: header titles at which the named columns end and the NMR sweep begins
SWEEP_START_TITLES = ("NMR", "NMR Data")


def normalise(title: str) -> str:
    return " ".join(str(title).strip().lower().split())


def resolve(header: list[str], wanted=None) -> dict[str, int]:
    """Map canonical channel -> column index for the channels present in
    ``header`` (absent channels are simply missing from the result)."""
    norm: dict[str, int] = {}
    for i, h in enumerate(header):
        n = normalise(h)
        if n in (normalise(t) for t in SWEEP_START_TITLES):
            norm.setdefault(n, i)
            break
        norm.setdefault(n, i)
    out = {}
    for canon in (wanted or CHANNELS):
        for title in CHANNELS[canon]:
            idx = norm.get(normalise(title))
            if idx is not None:
                out[canon] = idx
                break
    return out


def parse_number(text: str) -> float:
    """DAQ cell -> float; NaN for placeholders.  Accepts '46.23%' / '-0.00 %'."""
    s = str(text).strip()
    if s.lower() in MISSING_TOKENS:
        return float("nan")
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")
