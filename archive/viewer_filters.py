"""Pure filtering helpers for the Genesis EPICS archive viewer.

These helpers do not read hardware and do not modify archived data.
They only organize PV names already discovered by archive_viewer.py.
"""


SUBSYSTEM_OPTIONS = (
    "All Genesis",
    "Microwave",
    "ssRF",
    "Cryo",
    "Other",
)


MICROWAVE_GROUP_OPTIONS = (
    "All Microwave",
    "UCA / PS",
    "Vaunix / SRC",
    "Supervisor / SUP",
    "Robot",
)


CRYO_GROUP_OPTIONS = (
    "All Cryo",
    "Fridge",
    "Magnet",
    "Vacuum",
    "Cell",
    "Ion Gauge",
    "Fringe Field",
    "Cold Board",
    "Safety",
    "Alarms",
    "Target State",
    "Save / Restore",
    "Archiver",
)


MICROWAVE_PRESETS = {
    "Manual selection": (),

    "Microwave - Frequency": (
        "UNH_GEN_MW_SUP_OutputFrequencyRBV",
        "UNH_GEN_MW_SRC_MW_OutFI",
    ),

    "Microwave - RF / permissive state": (
        "UNH_GEN_MW_SUP_RFEnable",
        "UNH_GEN_MW_SUP_RFPermissive",
        "UNH_GEN_MW_SUP_SystemConnected",
        "UNH_GEN_MW_SRC_MW_RF_STATE",
    ),

    "Microwave - Source frequency / power": (
        "UNH_GEN_MW_SRC_MW_FI",
        "UNH_GEN_MW_SRC_MW_PWRI",
    ),

    "Microwave - UCA voltage / current": (
        "UNH_GEN_MW_PS_VMEAS",
        "UNH_GEN_MW_PS_IMEAS",
    ),

    "Microwave - Robot position": (
        "UNH_GEN_MW_ROBOT_X_CM",
        "UNH_GEN_MW_ROBOT_Y_CM",
    ),

    "Microwave - IOC heartbeats": (
        "UNH_GEN_MW_PS_MAN_spd1305x_time",
        "UNH_GEN_MW_SRC_MAN_vaunix_lms_time",
        "UNH_GEN_MW_SUP_MAN_microwave_supervisor_time",
        "UNH_GEN_MW_ROBOT_MAN_pcl602_time",
    ),
}


CRYO_PRESETS = {
    "Manual selection": (),

    "Cryo - Fridge F1-F4": (
        "UNH_GEN_CRYO_FRIDGE_F1_TI",
        "UNH_GEN_CRYO_FRIDGE_F2_TI",
        "UNH_GEN_CRYO_FRIDGE_F3_TI",
        "UNH_GEN_CRYO_FRIDGE_F4_TI",
    ),

    "Cryo - Fridge F5-F8": (
        "UNH_GEN_CRYO_FRIDGE_F5_TI",
        "UNH_GEN_CRYO_FRIDGE_F6_TI",
        "UNH_GEN_CRYO_FRIDGE_F7_TI",
        "UNH_GEN_CRYO_FRIDGE_F8_TI",
    ),

    "Cryo - Fridge F9-F11": (
        "UNH_GEN_CRYO_FRIDGE_F9_TI",
        "UNH_GEN_CRYO_FRIDGE_F10_TI",
        "UNH_GEN_CRYO_FRIDGE_F11_TI",
    ),

    "Cryo - Magnet M1-M4": (
        "UNH_GEN_CRYO_MAG_M1_TI",
        "UNH_GEN_CRYO_MAG_M2_TI",
        "UNH_GEN_CRYO_MAG_M3_TI",
        "UNH_GEN_CRYO_MAG_M4_TI",
    ),

    "Cryo - Magnet M5": (
        "UNH_GEN_CRYO_MAG_M5_TI",
    ),

    "Cryo - Vacuum pressures": (
        "UNH_GEN_CRYO_CELL_Cell_PI",
        "UNH_GEN_CRYO_VAC_Pump_PI",
        "UNH_GEN_CRYO_VAC_OVC_PI",
        "UNH_GEN_CRYO_ION_Ion_PI",
    ),

    "Cryo - Cold board": (
        "UNH_GEN_CRYO_COLDBOARD_Coldboard_VI",
        "UNH_GEN_CRYO_COLDBOARD_Coldboard_CI",
        "UNH_GEN_CRYO_COLDBOARD_Coldboard_PI",
        "UNH_GEN_CRYO_COLDBOARD_Coldboard_Mode",
    ),

    "Cryo - Safety permits": (
        "UNH_GEN_CRYO_SAFE_CB_Permit",
        "UNH_GEN_CRYO_SAFE_Vac_Permit",
        "UNH_GEN_CRYO_SAFE_Data_Permit",
        "UNH_GEN_CRYO_SAFE_Safe_Permit",
    ),

    "Cryo - Safety state": (
        "UNH_GEN_CRYO_SAFE_Safe_State",
        "UNH_GEN_CRYO_SAFE_Safe_Fault",
        "UNH_GEN_CRYO_SAFE_Safe_Stale",
        "UNH_GEN_CRYO_SAFE_Safe_Trips",
    ),

    "Cryo - Alarm summary": (
        "UNH_GEN_CRYO_ALARM_Alarm_Count",
        "UNH_GEN_CRYO_ALARM_Alarm_Major",
        "UNH_GEN_CRYO_ALARM_Alarm_Worst",
        "UNH_GEN_CRYO_ALARM_Alarm_Missing",
    ),

    "Cryo - Target state": (
        "UNH_GEN_CRYO_STATE_production",
        "UNH_GEN_CRYO_STATE_conditions",
        "UNH_GEN_CRYO_STATE_status",
        "UNH_GEN_CRYO_STATE_material",
    ),

    "Cryo - Fringe field": (
        "UNH_GEN_CRYO_FIELD_Fringe_FieldT",
        "UNH_GEN_CRYO_FIELD_Fringe_Field",
    ),
}


def normalized_archive_key(pv_name):
    """Return the archive filename-style key for a viewer PV name."""
    return (
        str(pv_name)
        .replace(":", "_")
        .replace("/", "_")
    )


def subsystem_for_pv(pv_name):
    """Classify one archived PV without changing its displayed name."""
    key = normalized_archive_key(pv_name)

    if key.startswith("UNH_GEN_MW_"):
        return "Microwave"

    if key.startswith("UNH_GEN_SSRF_"):
        return "ssRF"

    if key.startswith("UNH_GEN_CRYO_"):
        return "Cryo"

    return "Other"


def microwave_group_for_pv(pv_name):
    """Classify one Microwave PV."""
    key = normalized_archive_key(pv_name)

    if key.startswith("UNH_GEN_MW_PS_"):
        return "UCA / PS"

    if key.startswith("UNH_GEN_MW_SRC_"):
        return "Vaunix / SRC"

    if key.startswith("UNH_GEN_MW_SUP_"):
        return "Supervisor / SUP"

    if key.startswith("UNH_GEN_MW_ROBOT_"):
        return "Robot"

    return None


def cryo_group_for_pv(pv_name):
    """Classify one Cryo PV by Genesis target IOC ownership."""
    key = normalized_archive_key(pv_name)

    groups = (
        ("UNH_GEN_CRYO_FRIDGE_", "Fridge"),
        ("UNH_GEN_CRYO_MAG_", "Magnet"),
        ("UNH_GEN_CRYO_VAC_", "Vacuum"),
        ("UNH_GEN_CRYO_CELL_", "Cell"),
        ("UNH_GEN_CRYO_ION_", "Ion Gauge"),
        ("UNH_GEN_CRYO_FIELD_", "Fringe Field"),
        ("UNH_GEN_CRYO_COLDBOARD_", "Cold Board"),
        ("UNH_GEN_CRYO_SAFE_", "Safety"),
        ("UNH_GEN_CRYO_ALARM_", "Alarms"),
        ("UNH_GEN_CRYO_STATE_", "Target State"),
        ("UNH_GEN_CRYO_SAVE_", "Save / Restore"),
        ("UNH_GEN_CRYO_ARCH_", "Archiver"),
    )

    for prefix, label in groups:
        if key.startswith(prefix):
            return label

    return None


def filter_pvs(
    available_pvs,
    subsystem="All Genesis",
    microwave_group="All Microwave",
    cryo_group="All Cryo",
):
    """Filter an existing viewer PV list while preserving order."""
    result = []

    for pv in available_pvs:
        pv_subsystem = subsystem_for_pv(pv)

        if (
            subsystem != "All Genesis"
            and pv_subsystem != subsystem
        ):
            continue

        if (
            subsystem == "Microwave"
            and microwave_group != "All Microwave"
            and microwave_group_for_pv(pv) != microwave_group
        ):
            continue

        if (
            subsystem == "Cryo"
            and cryo_group != "All Cryo"
            and cryo_group_for_pv(pv) != cryo_group
        ):
            continue

        result.append(pv)

    return result


def _resolve_preset(
    available_pvs,
    requested,
):
    if not requested:
        return []

    by_key = {
        normalized_archive_key(pv): pv
        for pv in available_pvs
    }

    resolved = []

    for key in requested:
        pv = by_key.get(key)

        if pv is not None:
            resolved.append(pv)

    return resolved[:4]


def resolve_microwave_preset(
    available_pvs,
    preset_name,
):
    """Resolve one Microwave preset against files that actually exist."""
    requested = MICROWAVE_PRESETS.get(
        preset_name,
        (),
    )

    if not requested:
        return []

    by_key = {
        normalized_archive_key(pv): pv
        for pv in available_pvs
    }

    resolved = []

    for key in requested:
        pv = by_key.get(key)

        if (
            key == "UNH_GEN_MW_SRC_MW_RF_STATE"
            and pv is None
        ):
            pv = by_key.get(
                "UNH_GEN_MW_SRC_MW_RF_RBV",
                by_key.get(
                    "UNH_GEN_MW_SRC_MW_RF"
                ),
            )

        if pv is not None:
            resolved.append(pv)

    return resolved[:4]


def resolve_cryo_preset(
    available_pvs,
    preset_name,
):
    """Resolve one Cryo quick-view preset."""
    return _resolve_preset(
        available_pvs,
        CRYO_PRESETS.get(
            preset_name,
            (),
        ),
    )
