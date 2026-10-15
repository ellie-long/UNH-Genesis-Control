"""PVs the read-only probe records for each leg.

Built from the live configuration, never hand-maintained: the cryo
archiver's ``explicit_pvs`` (what the live system promises to archive), each
launched IOC's standard health PVs and heartbeat, and the logic IOCs'
operator-visible state.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import REPO_ROOT
from .profile import GeneratedStack

HEALTH = ("COMM", "ERROR", "LAST_EVENT", "LAST_OK", "WRITE_ENABLED")

CRYO_LOGIC = {
    "UNH:GEN:CRYO:SAFE": ("CB_Permit", "Vac_Permit", "Data_Permit", "Safe_Permit", "Safe_State",
                          "Safe_Fault", "Safe_Reason", "Safe_Trips", "Safe_Stale", "Safe_Enforce",
                          "Safe_Limits"),
    "UNH:GEN:CRYO:STATE": ("status", "material", "auto", "production", "conditions", "unmet",
                           "state_applied"),
    "UNH:GEN:CRYO:ALARM": ("Alarm_Count", "Alarm_Major", "Alarm_Worst", "Alarm_First", "Alarm_Latch",
                           "Alarm_LatchPV", "Alarm_Scanned", "Alarm_Missing"),
    "UNH:GEN:CRYO:SAVE": ("SR_Count", "SR_Failed", "SR_Last", "SR_Status"),
    "UNH:GEN:CRYO:ARCH": ("Archive_Status", "Archive_PV_Count", "Archive_Writes", "Archive_Enable"),
}

DEVICE_ROLES = {"ls224", "mks902b", "tpg262", "ionivac", "ls455", "scpi_supply"}

FITTER_PREFIX = "UNH:GEN:SSRF:FIT"
#: online_fitter.epics_run read-backs and operator commands (names from epics_run.py)
FITTER_PVS = ("FITTER_UP", "PUBLISHER_CONNECTED", "CALIBRATED", "UPDATE_BUSY", "LAST_OK", "WARNING",
              "MODE_CMD", "MODE_RBV", "MATERIAL_RBV", "UPDATE_CONSTANTS_CMD", "FIT_TYPE", "NPTS",
              "P_AREA", "P_FIT", "Q_EQ", "Q_FIT", "R", "PHASE_DEG", "RED_CHISQ", "CC", "SCALE")


def archiver_explicit_pvs(root: Path = REPO_ROOT) -> list[str]:
    path = root / "configs/target_physical/archiver/settings.yaml"
    with open(path, encoding="utf-8") as fh:
        return list(yaml.safe_load(fh)["archiver"].get("explicit_pvs", []))


def for_stack(stack: GeneratedStack, root: Path = REPO_ROOT, legs: set | None = None) -> list[str]:
    pvs: set[str] = set()
    if legs and "nmr" in legs:
        pvs.update(f"{FITTER_PREFIX}:{n}" for n in FITTER_PVS)
    stack_legs = {s.leg for s in stack.iocs}
    for spec in stack.iocs:
        pvs.add(f"{spec.prefix}:MAN:{spec.ioc}_time")
        if spec.emulator or spec.simulator:
            pvs.update(f"{spec.prefix}:{h}" for h in HEALTH)
    if "cryo" in stack_legs:
        pvs.update(archiver_explicit_pvs(root))
        for prefix, names in CRYO_LOGIC.items():
            pvs.update(f"{prefix}:{n}" for n in names)
    return sorted(pvs)


def device_channel_pvs(spec, settings: dict) -> dict[str, tuple[str, str]]:
    """PV -> (instrument, channel) for the emulator-fed reading PVs."""
    kind = (spec.emulator or {}).get("kind")
    chans = [c for c in settings.get("channels", []) if "None" not in str(c)]
    suffix = {"ls224": "_TI", "mks902b": "_PI", "tpg262": "_PI", "ionivac": "_PI",
              "ls455": "_Field"}.get(kind)
    if suffix is None:
        return {}
    return {f"{spec.prefix}:{ch}{suffix}": (spec.name, ch) for ch in chans}
