"""Fail-closed Genesis NMR fitter input-source policy.

Physical mask 0: LabVIEW, A1/B1.
Physical mask 3: PyNMR, A2/B2.
Both positions connect an NMR acquisition system.

The trusted input adapter, not the incoming packet, supplies
transport_source. This module never controls hardware.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SourceDecision:
    accepted: bool
    selected_source: str | None
    physical_source: str | None
    reason: str


def evaluate_source(
    *,
    selected_source,
    transport_source,
    payload_source,
    nmr_mask,
    nmr_comm,
    route_age_s,
    data_age_s,
    stream_connected,
    max_route_age_s=5.0,
    max_data_age_s=30.0,
):
    def norm(value):
        return value.strip().lower() if isinstance(value, str) else None

    selected = norm(selected_source)
    transport = norm(transport_source)
    payload = norm(payload_source)

    physical = (
        {0: "labview", 3: "pynmr"}.get(nmr_mask)
        if type(nmr_mask) is int else None
    )

    def reject(reason):
        return SourceDecision(False, selected, physical, reason)

    if selected not in ("labview", "pynmr"):
        return reject("No valid source selected")

    if type(nmr_comm) is not int or nmr_comm != 1:
        return reject("Switch communication unavailable")

    if physical is None:
        return reject("Switch route mixed or unknown")

    for age, limit, name in (
        (route_age_s, max_route_age_s, "Switch readback"),
        (data_age_s, max_data_age_s, "NMR measurement"),
    ):
        if (
            type(age) not in (int, float)
            or type(limit) not in (int, float)
            or not math.isfinite(age)
            or not math.isfinite(limit)
            or age < 0
            or limit <= 0
            or age > limit
        ):
            return reject(f"{name} invalid or stale")

    if physical != selected:
        return reject("Physical route disagrees with selection")

    if stream_connected is not True:
        return reject("Selected stream disconnected")

    if transport != selected:
        return reject("Trusted input channel mismatch")

    if payload_source is not None and not isinstance(
        payload_source, str
    ):
        return reject("Invalid packet source identity")

    if payload is None:
        if selected != "labview":
            return reject("PyNMR packet missing source identity")
    elif payload != selected:
        return reject("Packet source identity mismatch")

    return SourceDecision(
        True,
        selected,
        physical,
        "Selected source and verified route agree",
    )
