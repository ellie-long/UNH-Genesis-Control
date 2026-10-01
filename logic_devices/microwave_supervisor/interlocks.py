"""Transition-only RF permissive logic."""
import math


def rf_on_permissive(uca_connected, vaunix_connected, measured_voltage,
                     target_voltage, tolerance):
    if not vaunix_connected:
        return False, "Vaunix disconnected"
    if not uca_connected:
        return False, "UCA power supply disconnected"
    if measured_voltage is None:
        return False, "UCA measured voltage unavailable"
    if not all(math.isfinite(float(v)) for v in (measured_voltage, target_voltage, tolerance)):
        return False, "UCA voltage/configuration nonfinite"
    error = abs(float(measured_voltage) - float(target_voltage))
    if error > float(tolerance):
        return False, (f"RF blocked: need {target_voltage:g} +/- "
                       f"{tolerance:g} V")
    return True, "RF ON permitted"
