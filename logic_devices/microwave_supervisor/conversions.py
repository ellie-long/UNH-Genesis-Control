"""Conversions at the boundary between operator and Vaunix frequencies.

The operator window is the normal output range (138-142 GHz) unless Expert
mode is on, when the wider expert range applies (134-144 GHz; Elena Long,
2026-10-24).  Without configured expert limits the expert range equals the
normal one, so Expert mode changes nothing.
"""


class FrequencyConversion:
    def __init__(self, multiplier, output_min_ghz, output_max_ghz,
                 expert_min_ghz=None, expert_max_ghz=None):
        self.multiplier = float(multiplier)
        self.normal_minimum = float(output_min_ghz)
        self.normal_maximum = float(output_max_ghz)
        self.expert_minimum = float(output_min_ghz if expert_min_ghz is None else expert_min_ghz)
        self.expert_maximum = float(output_max_ghz if expert_max_ghz is None else expert_max_ghz)
        if self.multiplier <= 0 or self.normal_minimum >= self.normal_maximum:
            raise ValueError("invalid microwave frequency conversion settings")
        # the expert range must contain the normal one: Expert only widens
        if not (self.expert_minimum <= self.normal_minimum
                and self.normal_maximum <= self.expert_maximum):
            raise ValueError("expert frequency range must contain the normal range")
        self.expert = False

    @property
    def minimum(self):
        return self.expert_minimum if self.expert else self.normal_minimum

    @property
    def maximum(self):
        return self.expert_maximum if self.expert else self.normal_maximum

    def limits_text(self):
        kind = "EXPERT" if self.expert else "Normal"
        return f"{kind} {self.minimum:g}-{self.maximum:g} GHz"

    def validate_output(self, output_ghz):
        value = float(output_ghz)
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"output frequency {value:g} GHz is outside "
                f"[{self.minimum:g}, {self.maximum:g}] GHz")
        return value

    def within_normal(self, output_ghz):
        return self.normal_minimum <= float(output_ghz) <= self.normal_maximum

    def to_vaunix(self, output_ghz):
        return self.validate_output(output_ghz) / self.multiplier

    def from_vaunix(self, vaunix_ghz):
        return float(vaunix_ghz) * self.multiplier
