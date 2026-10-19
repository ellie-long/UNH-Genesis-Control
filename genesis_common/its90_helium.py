r"""
devices/its90_helium.py - helium vapour pressure -> temperature (ITS-90).

What this is
------------
The fridge's temperature can be read two ways: with a thermometer, or from the
pressure of the helium it is boiling under.  The second is not an estimate -
the saturated vapour pressure of helium IS a temperature scale, defined in the
International Temperature Scale of 1990, and in the superfluid region it is
more trustworthy than any resistor because it needs no calibration at all.

The lab's LabVIEW front panel has done this for years, in the block diagram
feeding "Vapor Pressure Temperature" / "Vap.Pres.T":

    T = A0 + A1*x + A2*x^2 + ... + A8*x^8,     x = (ln(P) - B) / C

with the constants 1.9 and 10.3 wired in beside the A-list.  Those are not
arbitrary: they are C and B of the ITS-90 **4He, 2.1768 K to 5.0 K** equation,
and the nine A values beside them are that equation's coefficients exactly.
So the LabVIEW code is the published scale, and this module is the same thing
with the other half of the scale added.

Two ranges, and why both are here
---------------------------------
ITS-90 defines 4He over two pressure bands that meet at the lambda point:

    1.25 K .. 2.1768 K    B = 5.6   C = 2.9    superfluid He-II
    2.1768 K .. 5.0 K     B = 10.3  C = 1.9    normal He-I     <- the LabVIEW one

They agree at the join to better than a microkelvin (checked in
tests/test_pressure_devices.py), so picking the band by pressure is seamless.
The LabVIEW diagram carries only the upper set; below the lambda pressure -
37.82 Torr, which is where this fridge spends a run - the upper polynomial is
being used outside its band, and the answer there is not a temperature.  Hence
both.

P IS IN PASCALS
---------------
The ITS-90 equations are defined for p in pascals.  The LabVIEW diagram has a
133.322 constant next to the gauge reading for exactly this reason: the gauge
reports Torr.  Getting this wrong does not produce an obvious error - it
produces a plausible wrong temperature, which is worse.  `torr_to_kelvin()`
exists so the conversion happens in one place.

Outside the band: NaN
---------------------
A polynomial outside its fit does not degrade, it diverges.  At 1 Pa the
He-II equation returns 2.03 K and the He-I equation returns 573 K; neither is
a temperature, and both look like numbers.  So anything outside
0.8606 Torr .. 1470.3 Torr (1.25 K .. 5.0 K) returns NaN, with a reason the
caller can publish.

Reference
---------
H. Preston-Thomas, "The International Temperature Scale of 1990 (ITS-90)",
Metrologia 27, 3 (1990), section 3.2 and table 2.  The same numbers appear in
every cryostat manual; the lab's own copy is the LabVIEW diagram above.
"""

import logging
import math

log = logging.getLogger(__name__)

#: Torr -> Pa.  The same constant the LabVIEW diagram wires in.
TORR_TO_PA = 133.322

#: the lambda point, where the two bands meet (Pa, and the same in Torr)
LAMBDA_PA = 5041.8

#: the ends of the ITS-90 scale
P_MIN_PA = 114.7343                                # T = 1.25 K
P_MAX_PA = 196016.5                                # T = 5.00 K

#: ITS-90 4He vapour-pressure equations: (A0..A9, B, C, T_low, T_high)
#: A9 is zero for both 4He bands; it is kept so the list matches the published
#: table term for term and a transcription error is easy to spot.
HE4_SUPERFLUID = {
    "name": "He-II",
    "coeffs": (1.392408, 0.527153, 0.166756, 0.050988, 0.026514,
               0.001975, -0.017976, 0.005409, 0.013259, 0.0),
    "B": 5.6, "C": 2.9, "t_low": 1.25, "t_high": 2.1768,
    # The limits are part of the band, not an afterthought: without them
    # band_for() falls back to the whole scale and the FIRST band in the list
    # answers every pressure - which made 1 atm read 7.66 K instead of 4.22 K.
    "p_min": P_MIN_PA, "p_max": LAMBDA_PA,
}
HE4_NORMAL = {
    "name": "He-I",
    # These nine, with B = 10.3 and C = 1.9, are the constants in the lab's
    # LabVIEW block diagram.
    "coeffs": (3.146631, 1.357655, 0.413923, 0.091159, 0.016349,
               0.001826, -0.004325, -0.004973, 0.0, 0.0),
    "B": 10.3, "C": 1.9, "t_low": 2.1768, "t_high": 5.0,
    "p_min": LAMBDA_PA, "p_max": P_MAX_PA,
}

LAMBDA_TORR = LAMBDA_PA / TORR_TO_PA               # 37.8167 Torr
P_MIN_TORR = P_MIN_PA / TORR_TO_PA                 # 0.8606 Torr
P_MAX_TORR = P_MAX_PA / TORR_TO_PA                 # 1470.3 Torr


#: bands in the order they are tried.  Replaced wholesale by load_bands().
BANDS = [HE4_SUPERFLUID, HE4_NORMAL]

#: where a band file lives when settings.yaml does not name one
DEFAULT_BAND_FILE = "calibration/vapor_pressure_he4.csv"


def load_bands(path):
    """Read extra (or replacement) bands from a CSV; -> the new band list.

    Columns: Name, Pmin_Pa, Pmax_Pa, B, C, A0..A9.  Lines starting with '#'
    are comments, because the provenance of these numbers matters more than
    the numbers look like it does.

    A row REPLACES a built-in band of the same name and is otherwise added.
    This is how the sub-1.25 K branch gets in: ITS-90 simply does not define
    one, so it has to come from the lab's own LabVIEW VI, and that is a data
    edit rather than a code change.
    """
    import csv
    bands = {b["name"]: dict(b) for b in (HE4_SUPERFLUID, HE4_NORMAL)}
    order = [b["name"] for b in (HE4_SUPERFLUID, HE4_NORMAL)]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = (line for line in fh if not line.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            try:
                band = {
                    "name": name,
                    "B": float(row["B"]), "C": float(row["C"]),
                    "p_min": float(row["Pmin_Pa"]), "p_max": float(row["Pmax_Pa"]),
                    "coeffs": tuple(float(row.get(f"A{i}") or 0.0)
                                    for i in range(10)),
                }
            except (KeyError, TypeError, ValueError):
                log.warning("vapour-pressure band %s in %s does not parse "
                            "- skipped", name, path)
                continue
            if not (band["p_max"] > band["p_min"] > 0):
                log.warning("vapour-pressure band %s has an unusable pressure "
                            "range - skipped", name)
                continue
            if name not in bands:
                order.append(name)
            bands[name] = band
    return [bands[n] for n in order]


def use_bands(bands):
    """Install a band list (from load_bands) as the one the module uses."""
    global BANDS
    BANDS = list(bands)
    return BANDS


def _evaluate(equation, pascals):
    """One ITS-90 band, no range checking.  Exposed for the tests."""
    x = (math.log(pascals) - equation["B"]) / equation["C"]
    return sum(a * x ** i for i, a in enumerate(equation["coeffs"]))


def band_for(pascals):
    """Which equation covers this pressure, or None when none does.

    Bands are tried in order, so a band file can put the lab's own low-range
    branch ahead of the ITS-90 ones without changing their limits.
    """
    if not math.isfinite(pascals) or pascals <= 0.0:
        return None
    for band in BANDS:
        low = band.get("p_min", P_MIN_PA)
        high = band.get("p_max", P_MAX_PA)
        if low <= pascals <= high:
            return band
    return None


def pascals_to_kelvin(pascals):
    """-> (kelvin, status).

    status is "" when the number is good, otherwise a short phrase saying why
    there is no temperature.  NaN is always paired with a reason: on a screen,
    a blank field that nobody can explain is its own kind of fault report.
    """
    try:
        pascals = float(pascals)
    except (TypeError, ValueError):
        return float("nan"), "no pressure"
    if not math.isfinite(pascals):
        return float("nan"), "no pressure"
    if pascals <= 0.0:
        return float("nan"), "P <= 0"
    equation = band_for(pascals)
    if equation is None:
        # Name the end that was missed; "out of range" alone sends somebody to
        # the wrong end of the fridge.
        low = min(b.get("p_min", P_MIN_PA) for b in BANDS)
        high = max(b.get("p_max", P_MAX_PA) for b in BANDS)
        # Short enough to survive EPICS' 39-character string records intact:
        # a message whose point is cut off ("...scale (be") is no message.
        # Name the pressure, so it is obvious the gauge is fine and the SCALE
        # is what ran out - on this fridge that is the usual case, ITS-90
        # stopping at 1.25 K while the fridge runs colder.
        if pascals < low:
            return float("nan"), f"P below scale ({low / TORR_TO_PA:.4g} Torr)"
        return float("nan"), f"P above scale ({high / TORR_TO_PA:.4g} Torr)"
    try:
        kelvin = _evaluate(equation, pascals)
    except (ValueError, OverflowError):
        return float("nan"), "conversion failed"
    if not math.isfinite(kelvin):
        return float("nan"), "conversion failed"
    return kelvin, ""


def torr_to_kelvin(torr):
    """The gauges report Torr; ITS-90 wants pascals.  This is that one step."""
    try:
        torr = float(torr)
    except (TypeError, ValueError):
        return float("nan"), "no pressure"
    if not math.isfinite(torr):
        return float("nan"), "no pressure"
    return pascals_to_kelvin(torr * TORR_TO_PA)


def phase_of(torr):
    """A word for the screen: which side of the lambda point the fridge is on.

    The operator's question is rarely "what is x^8" - it is "am I superfluid
    yet".  The lambda point is at 37.82 Torr, so the pressure alone answers it,
    and it answers it even when the temperature is out of range.
    """
    try:
        torr = float(torr)
    except (TypeError, ValueError):
        return "no reading"
    if not math.isfinite(torr) or torr <= 0.0:
        return "no reading"
    if torr < P_MIN_TORR:
        return "below scale"
    if torr <= LAMBDA_TORR:
        return "He-II superfluid"
    if torr <= P_MAX_TORR:
        return "He-I normal"
    return "above scale"


def kelvin_to_torr(kelvin, tolerance=1e-9):
    """The inverse, by bisection - for test fixtures and for setting limits.

    ITS-90 gives T(P) and has no closed-form inverse; the function is monotonic
    across the whole scale, so bisection in log P converges in a few dozen
    steps and needs no derivative.
    """
    try:
        kelvin = float(kelvin)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(kelvin) or not (1.25 <= kelvin <= 5.0):
        return float("nan")
    low, high = P_MIN_PA, P_MAX_PA
    for _ in range(200):
        middle = math.sqrt(low * high)
        value, status = pascals_to_kelvin(middle)
        if status:
            return float("nan")
        if value < kelvin:
            low = middle
        else:
            high = middle
        if high - low < tolerance:
            break
    return math.sqrt(low * high) / TORR_TO_PA
