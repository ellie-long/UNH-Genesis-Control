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


def magic_factor(dopant, sign, material=None):
    """The Magic factor (GHz/T) for this dopant, or None if not configured.

    A dopant may carry one pair per target material (``magic: {material:
    {plus: .., minus: ..}}``, matched case-insensitively), since e.g.
    irradiated d-butanol and irradiated ND3 polarize best at different f/B;
    the older flat ``magic_plus_factor`` / ``magic_minus_factor`` keys still
    apply when there is no per-material table."""
    table = dopant.get("magic")
    if table:
        if material is None:
            return None
        want = str(material).strip().lower()
        pair = next((v for k, v in table.items() if str(k).strip().lower() == want), None)
        return None if pair is None else pair.get("plus" if sign == "+" else "minus")
    return dopant.get("magic_plus_factor" if sign == "+" else "magic_minus_factor")


def configured_magic(dopants, dopant_index, sign, central_ghz, material=None):
    try:
        dopant = dopants[int(dopant_index)]
    except (IndexError, TypeError, ValueError):
        raise ValueError("invalid dopant selection") from None
    gamma, factor = dopant.get("gamma_d_over_2pi"), magic_factor(dopant, sign, material)
    if factor is None:
        raise ValueError(f"no Magic {sign} constant for {dopant['name']}"
                         + (f" / {material}" if material else "") + "; see settings.yaml")
    if gamma is None:
        raise ValueError(f"{dopant['name']}: gamma_d_over_2pi not configured (open question "
                         "on CentralFrequency units); see settings.yaml")
    return magic_frequency(central_ghz, gamma, factor)


def dopant_index(names, selected):
    """Index of the dopant named ``selected`` (case-insensitive) in ``names``, or None."""
    want = str(selected).strip().lower()
    for i, name in enumerate(names):
        if str(name).strip().lower() == want:
            return i
    return None
