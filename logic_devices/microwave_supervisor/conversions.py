"""Conversions at the boundary between operator and Vaunix frequencies."""


class FrequencyConversion:
    def __init__(self, multiplier, output_min_ghz, output_max_ghz):
        self.multiplier = float(multiplier)
        self.minimum = float(output_min_ghz)
        self.maximum = float(output_max_ghz)
        if self.multiplier <= 0 or self.minimum >= self.maximum:
            raise ValueError("invalid microwave frequency conversion settings")

    def validate_output(self, output_ghz):
        value = float(output_ghz)
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"output frequency {value:g} GHz is outside "
                f"[{self.minimum:g}, {self.maximum:g}] GHz")
        return value

    def to_vaunix(self, output_ghz):
        return self.validate_output(output_ghz) / self.multiplier

    def from_vaunix(self, vaunix_ghz):
        return float(vaunix_ghz) * self.multiplier
