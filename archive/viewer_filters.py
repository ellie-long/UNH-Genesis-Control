"""Pure filtering helpers for the Genesis EPICS archive viewer.

These helpers do not read hardware and do not modify archived data.
They only organize the PV names already discovered by archive_viewer.py.
"""


SUBSYSTEM_OPTIONS = (
    "All Genesis",
    "Microwave",
    "ssRF",
    "Other",
)


MICROWAVE_GROUP_OPTIONS = (
    "All Microwave",
    "UCA / PS",
    "Vaunix / SRC",
    "Supervisor / SUP",
    "Robot",
)


MICROWAVE_PRESETS = {
    "Manual selection": (),

    # Keep preset traces on compatible scales where practical.
    "Microwave - Frequency": (
        "UNH_GEN_MW_SUP_OutputFrequencyRBV",
        "UNH_GEN_MW_SRC_MW_OutFI",
    ),

    "Microwave - RF / permissive state": (
        "UNH_GEN_MW_SUP_RFEnable",
        "UNH_GEN_MW_SUP_RFPermissive",
        "UNH_GEN_MW_SUP_SystemConnected",
        "UNH_GEN_MW_SRC_MW_RF",
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

    return "Other"


def microwave_group_for_pv(pv_name):
    """Classify one Microwave PV into its Phase-I subsystem device group."""
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


def filter_pvs(
    available_pvs,
    subsystem="All Genesis",
    microwave_group="All Microwave",
):
    """Filter an existing viewer PV list while preserving its order."""
    result = []

    for pv in available_pvs:
        pv_subsystem = subsystem_for_pv(pv)

        if (
            subsystem != "All Genesis"
            and pv_subsystem != subsystem
        ):
            continue

        if subsystem == "Microwave":
            if microwave_group != "All Microwave":
                if microwave_group_for_pv(pv) != microwave_group:
                    continue

        result.append(pv)

    return result


def resolve_microwave_preset(
    available_pvs,
    preset_name,
):
    """Resolve a quick-view preset against PVs that actually exist."""
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

        if pv is not None:
            resolved.append(pv)

    # Existing archive viewer supports at most four combined traces.
    return resolved[:4]
