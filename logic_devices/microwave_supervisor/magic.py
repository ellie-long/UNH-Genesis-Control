"""Magic-frequency calculation with deliberately externalized constants."""

import math


def magic_frequency(central_ghz, gamma_d_over_2pi, factor):
    """Return (central / (gamma_d / 2pi)) * dopant_magic_factor.

    Units and values must be supplied consistently by the experiment.  This is
    the literal relationship in the requirement; no physical constants are
    embedded here because none were present in the repository.
    """
    values = tuple(map(float, (central_ghz, gamma_d_over_2pi, factor)))
    if not all(map(math.isfinite, values)) or values[1] == 0:
        raise ValueError("Magic constants must be finite and gamma_d_over_2pi nonzero")
    return values[0] / values[1] * values[2]


def configured_magic(dopants, dopant_index, sign, central_ghz):
    try:
        dopant = dopants[int(dopant_index)]
    except (IndexError, TypeError, ValueError):
        raise ValueError("invalid dopant selection") from None
    key = "magic_plus_factor" if sign == "+" else "magic_minus_factor"
    gamma, factor = dopant.get("gamma_d_over_2pi"), dopant.get(key)
    if gamma is None or factor is None:
        raise ValueError(
            f"{dopant['name']} {sign} constants are not configured; see settings.yaml")
    return magic_frequency(central_ghz, gamma, factor)


def dopant_index(names, selected):
    """Index of the dopant named ``selected`` (case-insensitive) in ``names``, or None."""
    want = str(selected).strip().lower()
    for i, name in enumerate(names):
        if str(name).strip().lower() == want:
            return i
    return None
