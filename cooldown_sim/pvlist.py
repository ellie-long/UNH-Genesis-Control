"""PVs the read-only probe records for each leg.

Built from the live configuration, never hand-maintained: the cryo
archiver's ``explicit_pvs`` (what the live system promises to archive), each
launched IOC's standard health PVs and heartbeat, and the logic IOCs'
operator-visible state.
"""

from __future__ import annotations

import os
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

DEVICE_ROLES = {"ls224", "mks902b", "tpg262", "ionivac", "ls455", "scpi_supply",
                "flowmeter_pi", "flowmeter_diewu", "ami1700", "ami135", "manometer_low"}

FITTER_PREFIX = "UNH:GEN:SSRF:FIT"
#: online_fitter.epics_run read-backs and operator commands (names from epics_run.py)
FITTER_PVS = ("FITTER_UP", "PUBLISHER_CONNECTED", "CALIBRATED", "UPDATE_BUSY", "LAST_OK", "WARNING",
              "MODE_CMD", "MODE_RBV", "MATERIAL_RBV", "UPDATE_CONSTANTS_CMD", "FIT_TYPE", "NPTS",
              "P_AREA", "P_FIT", "Q_EQ", "Q_FIT", "R", "PHASE_DEG", "RED_CHISQ", "CC", "SCALE")


PROTECTED_PVS = ("NMR_PROTECTED", "NMR_SOURCE_RBV", "NMR_MASK_RBV", "NMR_ROUTE_OK",
                 "NMR_STREAM_OK", "NMR_EVENT_READY", "NMR_FIT_ELIGIBLE", "NMR_STATUS")


def archiver_explicit_pvs(root: Path = REPO_ROOT) -> list[str]:
    path = root / "configs/target_physical/archiver/settings.yaml"
    with open(path, encoding="utf-8") as fh:
        return list(yaml.safe_load(fh)["archiver"].get("explicit_pvs", []))


#: Microwave read-backs (physical supervisor, Vaunix IOC, UCA IOC)
MW_PVS = {
    "UNH:GEN:MW:SUP": ("RFEnable", "RFState", "RFActual", "RFValid", "RFPermissive",
                       "InterlockMessage", "OutputFrequency", "OutputFrequencyRBV", "UCAVoltage",
                       "ControlOwned", "SystemConnected", "UCAConnected", "VaunixConnected",
                       "LastError"),
    "UNH:GEN:MW:SRC": ("MW_Connected", "MW_RF_RBV", "MW_RF_VALID", "MW_RF_STATE", "MW_RF_READY",
                       "MW_RF_ERROR", "MW_OutFI", "MW_FI", "MW_PWRI", "MW_PWRC", "MW_Lock",
                       "MW_CONTROL_OWNED", "MW_TestMode", "MW_Simulation"),
    "UNH:GEN:MW:PS": ("COMM", "ERROR", "VMEAS", "IMEAS", "VSET_RBV", "OUTPUT_RBV"),
}


#: ssRF read-backs (sim-active IOCs; the sequencer exists only on POSIX)
SSRF_PVS = {
    "UNH:GEN:SSRF:GEN": ("COMM", "RF_RBV", "LEVEL_RBV", "FREQ_RBV", "FM_RBV", "SWEEP_ARM_RBV"),
    "UNH:GEN:SSRF:WGEN": ("COMM", "OUTPUT_RBV", "WAVEFORM_ID", "WAVEFORM_POINTS", "LAST_EVENT"),
    "UNH:GEN:SSRF:NMR_SW": ("COMM", "MASK_RBV"),
    "UNH:GEN:SSRF:RF_SW": ("COMM", "MASK_RBV"),
    "UNH:GEN:SSRF:ILK": ("STATE", "REASON"),
}
SSRF_SEQ_PVS = ("READY", "STATE", "REASON", "EXECUTION_ARMED", "EXECUTION_STATE", "EXECUTION_REASON",
                "WAVEFORM_STAGE_STATE", "WAVEFORM_STAGE_ID", "MANUAL_STATE")


def for_stack(stack: GeneratedStack, root: Path = REPO_ROOT, legs: set | None = None,
              posix: bool | None = None) -> list[str]:
    pvs: set[str] = set()
    if legs and "nmr" in legs:
        pvs.update(f"{FITTER_PREFIX}:{n}" for n in FITTER_PVS)
        if "ssrf" in legs:      # protected-mode status (served in every mode)
            pvs.update(f"{FITTER_PREFIX}:{n}" for n in PROTECTED_PVS)
    stack_legs = {s.leg for s in stack.iocs}
    is_posix = (os.name != "nt") if posix is None else posix
    if "mw" in stack_legs:
        for prefix, names in MW_PVS.items():
            pvs.update(f"{prefix}:{n}" for n in names)
    if "ssrf" in stack_legs:
        for prefix, names in SSRF_PVS.items():
            pvs.update(f"{prefix}:{n}" for n in names)
        if is_posix:
            pvs.update(f"UNH:GEN:SSRF:SEQ:{n}" for n in SSRF_SEQ_PVS)
    for spec in stack.iocs:
        if spec.posix_only and not is_posix:
            continue                              # not launched on this platform
        pvs.add(f"{spec.prefix}:MAN:{spec.ioc}_time")
        if spec.leg == "cryo" and (spec.emulator or spec.simulator):
            pvs.update(f"{spec.prefix}:{h}" for h in HEALTH)
        if spec.leg == "magnet":                  # magnet_ioc: health + its settings' records
            pvs.update(f"{spec.prefix}:{h}" for h in HEALTH)
            settings = yaml.safe_load((Path(spec.folder) / "settings.yaml").read_text(encoding="utf-8"))
            pvs.update(f"{spec.prefix}:{r}" for r in settings[spec.ioc].get("records") or {})
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
              "ls455": "_Field", "flowmeter_pi": "_SLM", "flowmeter_diewu": "_SLM",
              "ami1700": "_LI", "ami135": "_LI", "manometer_low": "_PI"}.get(kind)
    if suffix is None:
        return {}
    return {f"{spec.prefix}:{ch}{suffix}": (spec.name, ch) for ch in chans}
