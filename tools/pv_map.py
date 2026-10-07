"""
Genesis target/Cryo PV map.

Adapted from Farooq's UNH DAQ pv_map.py.

The original deployment used one monolithic settings.yaml and one common
prefix.  Genesis keeps each physical/logic IOC in its own settings directory,
with the IOC manager providing the deployment manifest.  This module expands
that manifest into one offline PV map.

No EPICS import and no network access occur here.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MANAGER = (
    ROOT
    / "configs"
    / "target_physical"
    / "ioc_manager"
    / "settings.yaml"
)


# ---------------------------------------------------------------------------
# Instrument PV contracts retained from Farooq for target-owned devices.
# RF/NMR-owned SMC100A, Agilent, Mini-Circuits and Vaunix devices are
# intentionally absent: their authoritative implementations live elsewhere
# in Genesis.
# ---------------------------------------------------------------------------

SUFFIXES = {
    "target_ioc.instruments.spd1000x": [
        "_VI",
        "_CI",
        "_PI",
        "_VC",
        "_CC",
        "_Mode",
        "_CVCC",
        "_IDN",
        "_Conn",
    ],
    "target_ioc.instruments.mks902b": [
        "_PI",
        "_Status",
        "_IDN",
        "_Conn",
    ],
    "target_ioc.instruments.tpg262": [
        "_PI",
        "_Status",
        "_Conn",
    ],
    "target_ioc.instruments.ionivac": [
        "_PI",
        "_Raw",
        "_Conn",
    ],
    "target_ioc.instruments.ls455": [
        "_Field",
        "_FieldT",
        "_Unit",
        "_IDN",
        "_Conn",
    ],
    "target_ioc.instruments.ls224": [
        "_TI",
        "_RI",
        "_Status",
        "_Conn",
    ],
    "target_ioc.instruments.dp832": [
        "_VI",
        "_CI",
        "_VC",
        "_CC",
        "_Mode",
    ],
    "target_ioc.instruments.ls336": [
        "_TI",
        "_Heater",
        "_SP",
        "_Mode",
        "_Range",
    ],
}


# ---------------------------------------------------------------------------
# Logic IOC PV contracts.
# Burn interlock/sequencer are intentionally not target/Cryo ownership.
# ---------------------------------------------------------------------------

LOGIC_PVS = {
    "logic_devices.target_status": [
        "status",
        "material",
        "auto",
        "production",
        "conditions",
        "unmet",
        "state_applied",
    ],
    "logic_devices.save_restore": [
        "SR_Save",
        "SR_Restore",
        "SR_File",
        "SR_Auto",
        "SR_Restore_Enables",
        "SR_Count",
        "SR_Failed",
        "SR_Last",
        "SR_Status",
    ],
    "logic_devices.archiver": [
        "Archive_Status",
        "Archive_PV_Count",
        "Archive_Writes",
        "Archive_Enable",
        "Archive_Deadband",
        "Archive_Time_Increment",
    ],
    "logic_devices.safety_interlock": [
        "CB_Permit",
        "Vac_Permit",
        "Data_Permit",
        "Safe_Permit",
        "Safe_State",
        "Safe_Fault",
        "Safe_Reason",
        "Safe_Trips",
        "Safe_Stale",
        "Safe_Enforce",
        "Safe_Reset",
        "Safe_Limits",
    ],
    "logic_devices.alarm_summary": [
        "Alarm_Count",
        "Alarm_Major",
        "Alarm_Worst",
        "Alarm_First",
        "Alarm_Latch",
        "Alarm_LatchPV",
        "Alarm_LatchAt",
        "Alarm_Ack",
        "Alarm_Scanned",
        "Alarm_Missing",
    ],
    "logic_devices.pid": [
        "_SP",
        "_Kp",
        "_Ki",
        "_Kd",
        "_OutLo",
        "_OutHi",
        "_Auto",
        "_In",
        "_Out",
        "_Err",
        "_Status",
    ],
}


MOMENTARY = {
    "SR_Save",
    "SR_Restore",
    "Safe_Reset",
    "Alarm_Ack",
}


# ---------------------------------------------------------------------------
# Writable instrument records used by save/restore.
# ---------------------------------------------------------------------------

SETTINGS = {
    "target_ioc.instruments.spd1000x": [
        "_VC",
        "_CC",
    ],
    "target_ioc.instruments.dp832": [
        "_VC",
        "_CC",
    ],
    "target_ioc.instruments.ls336": [
        "_SP",
        "_Range",
    ],
}


ENABLES = {
    "target_ioc.instruments.spd1000x": [
        "_Mode",
    ],
    "target_ioc.instruments.dp832": [
        "_Mode",
    ],
    "target_ioc.instruments.ls336": [
        "_Mode",
    ],
}


def _read_yaml(path):
    with Path(path).open() as stream:
        return yaml.safe_load(stream) or {}


def _resolve_repo_path(path):
    path = Path(path)

    if path.is_absolute():
        return path

    return ROOT / path


def _is_manager(settings):
    return any(
        isinstance(cfg, dict)
        and cfg.get("settings_folder")
        for name, cfg in settings.items()
        if name != "general"
    )


def _expand_manager(manager):
    """Expand a Genesis IOC-manager manifest into Farooq-style IOC entries."""

    deployment = {
        "general": dict(
            manager.get(
                "general",
                {},
            )
        )
    }

    for manager_name, manager_cfg in manager.items():
        if manager_name == "general":
            continue

        if not isinstance(manager_cfg, dict):
            continue

        folder = manager_cfg.get("settings_folder")

        if not folder:
            continue

        settings_path = (
            _resolve_repo_path(folder)
            / "settings.yaml"
        )

        child = _read_yaml(settings_path)

        wanted = manager_cfg.get(
            "ioc",
            manager_name,
        )

        if (
            wanted in child
            and isinstance(child[wanted], dict)
        ):
            child_name = wanted
        else:
            candidates = [
                name
                for name, cfg in child.items()
                if name != "general"
                and isinstance(cfg, dict)
            ]

            if len(candidates) != 1:
                raise ValueError(
                    "Cannot identify IOC entry in "
                    f"{settings_path}: {candidates}"
                )

            child_name = candidates[0]

        cfg = dict(child[child_name])

        cfg["_prefix"] = child["general"]["prefix"]
        cfg["_settings_file"] = str(settings_path)
        cfg["_manager_name"] = manager_name

        heartbeat = manager_cfg.get(
            "heartbeat_pv"
        )

        if heartbeat:
            cfg["_heartbeat_pv"] = heartbeat

        deployment[manager_name] = cfg

    return deployment


def load_settings(path=None):
    """Load a monolithic Farooq file or a distributed Genesis deployment."""

    settings_path = (
        _resolve_repo_path(path)
        if path
        else DEFAULT_MANAGER
    )

    settings = _read_yaml(settings_path)

    if _is_manager(settings):
        return _expand_manager(settings)

    return settings


def _prefix(settings, cfg):
    return cfg.get(
        "_prefix",
        settings["general"]["prefix"],
    )


def logic_iocs(settings):
    return {
        name: cfg
        for name, cfg in settings.items()
        if (
            name != "general"
            and isinstance(cfg, dict)
            and cfg.get("module") in LOGIC_PVS
        )
    }


def instrument_iocs(settings):
    return {
        name: cfg
        for name, cfg in settings.items()
        if (
            name != "general"
            and isinstance(cfg, dict)
            and cfg.get("module") in SUFFIXES
        )
    }


def ioc_pv_names(prefix, cfg, skip=()):
    out = []

    channels = [
        channel
        for channel in cfg.get(
            "channels",
            [],
        )
        if "None" not in str(channel)
    ]

    for channel in channels:
        for suffix in SUFFIXES.get(
            cfg["module"],
            [],
        ):
            if suffix in skip:
                continue

            out.append(
                f"{prefix}:{channel}{suffix}"
            )

    return out


def logic_pv_names(
    settings,
    skip_momentary=False,
):
    out = {}

    for ioc, cfg in logic_iocs(
        settings
    ).items():
        names = list(
            LOGIC_PVS[cfg["module"]]
        )

        if cfg["module"] == "logic_devices.pid":
            loop = cfg.get(
                "name",
                "PID",
            )

            names = [
                f"{loop}{suffix}"
                for suffix in names
            ]

        prefix = _prefix(
            settings,
            cfg,
        )

        for name in names:
            if (
                skip_momentary
                and name in MOMENTARY
            ):
                continue

            out[f"{prefix}:{name}"] = ioc

    return out


def all_pv_names(
    settings,
    skip=(),
    heartbeats=True,
    logic=True,
):
    """Return ``{PV: IOC}`` for the expanded Genesis deployment."""

    names = {}

    for ioc, cfg in instrument_iocs(
        settings
    ).items():
        prefix = _prefix(
            settings,
            cfg,
        )

        for pv in ioc_pv_names(
            prefix,
            cfg,
            skip,
        ):
            names[pv] = ioc

        if heartbeats:
            heartbeat = cfg.get(
                "_heartbeat_pv"
            )

            if heartbeat:
                names[heartbeat] = ioc
            else:
                names[
                    f"{prefix}:MAN:{ioc}_time"
                ] = ioc

    if logic:
        names.update(
            logic_pv_names(
                settings,
                skip_momentary=(
                    "_Fire" in skip
                ),
            )
        )

        if heartbeats:
            for ioc, cfg in logic_iocs(
                settings
            ).items():
                heartbeat = cfg.get(
                    "_heartbeat_pv"
                )

                if heartbeat:
                    names[heartbeat] = ioc
                else:
                    prefix = _prefix(
                        settings,
                        cfg,
                    )

                    names[
                        f"{prefix}:MAN:{ioc}_time"
                    ] = ioc

    return names


def writable_pv_names(
    settings,
    include_enables=True,
):
    """Return writable target-owned instrument PVs."""

    out = {}

    for cfg in instrument_iocs(
        settings
    ).values():
        prefix = _prefix(
            settings,
            cfg,
        )

        channels = [
            channel
            for channel in cfg.get(
                "channels",
                [],
            )
            if "None" not in str(channel)
        ]

        for channel in channels:
            for suffix in SETTINGS.get(
                cfg["module"],
                [],
            ):
                out[
                    f"{prefix}:{channel}{suffix}"
                ] = "setting"

            if include_enables:
                for suffix in ENABLES.get(
                    cfg["module"],
                    [],
                ):
                    out[
                        f"{prefix}:{channel}{suffix}"
                    ] = "enable"

    return out
