"""Configuration-driven candidate PV registry for Genesis.

Read-only configuration processing: no EPICS or hardware access.
"""

from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = "archive/master_sources.yaml"


def _load(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()

    if not path.is_relative_to(root):
        raise ValueError(
            f"Source configuration is outside repository: {relative}"
        )

    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    if not isinstance(config, dict):
        raise ValueError(f"Invalid YAML mapping: {path}")

    return config


def build_registry(root=ROOT, sources_file=DEFAULT_SOURCES):
    """Return candidate PV metadata and diagnostics.

    Supported sources:
      explicit_archiver: existing archiver explicit_pvs
      ioc_manager: defined IOC records and heartbeats
      pv_list: additional verified PV names entered in YAML

    Live connectivity and CA/PVA transport remain unverified.
    """
    root = Path(root)
    manifest = _load(root, sources_file)

    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported source-manifest version")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("A nonempty sources list is required")

    registry = {}
    invalid = []

    def add(pv, subsystem, origin, kind):
        if not isinstance(pv, str) or not pv.strip():
            raise ValueError(f"Invalid PV: {pv!r}")

        if pv != pv.strip():
            raise ValueError(f"Unexpected whitespace in PV: {pv!r}")

        if pv in registry:
            raise ValueError(
                f"Duplicate PV ownership: {pv} "
                f"({registry[pv]['subsystem']} / {subsystem})"
            )

        registry[pv] = {
            "subsystem": subsystem,
            "source_config": origin,
            "kind": kind,
            "transport": "unverified",
        }

    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Source entry must be a mapping")

        subsystem = source.get("subsystem")
        kind = source.get("kind")

        if not isinstance(subsystem, str) or not subsystem.strip():
            raise ValueError("Source subsystem name is required")

        if kind == "explicit_archiver":
            relative = source["settings"]
            config = _load(root, relative)

            pvs = config["archiver"].get("explicit_pvs")
            if not isinstance(pvs, list):
                raise ValueError(
                    f"Missing explicit_pvs in {relative}"
                )

            for pv in pvs:
                add(pv, subsystem, relative, "archive_pv")

        elif kind == "ioc_manager":
            manager_path = source["settings"]
            manager = _load(root, manager_path)

            for name, entry in manager.items():
                if name in ("general", "archiver"):
                    continue
                if not isinstance(entry, dict):
                    continue

                folder = entry.get("settings_folder")
                if not folder:
                    raise ValueError(
                        f"Missing settings_folder: {name}"
                    )

                relative = str(
                    Path(folder) / "settings.yaml"
                )
                config = _load(root, relative)

                prefix = config.get(
                    "general", {}
                ).get("prefix")

                ioc_name = entry.get("ioc", name)
                device = config.get(ioc_name)

                if not prefix or not isinstance(device, dict):
                    raise ValueError(
                        f"Invalid IOC configuration: {name}"
                    )

                records = device.get("records", {}) or {}
                if not isinstance(records, dict):
                    raise ValueError(
                        f"Invalid records mapping: {name}"
                    )

                for record in records:
                    add(
                        f"{prefix}:{record}",
                        subsystem,
                        relative,
                        "defined_record",
                    )

                archived = device.get("archive_records")
                if archived is None:
                    archived = list(records)

                for record in archived:
                    if record not in records:
                        invalid.append({
                            "ioc": name,
                            "record": record,
                            "config": relative,
                        })

                heartbeat = entry.get("heartbeat_pv")
                if heartbeat:
                    add(
                        heartbeat,
                        subsystem,
                        manager_path,
                        "heartbeat",
                    )

        elif kind == "pv_list":
            pvs = source.get("pvs")
            if not isinstance(pvs, list) or not pvs:
                raise ValueError(
                    f"Nonempty PV list required: {subsystem}"
                )

            for pv in pvs:
                add(
                    pv,
                    subsystem,
                    str(sources_file),
                    "explicit_pv",
                )

        else:
            raise ValueError(
                f"Unsupported source kind: {kind}"
            )

    counts = dict(Counter(
        data["subsystem"] for data in registry.values()
    ))

    report = {
        "counts": counts,
        "total": len(registry),
        "invalid_archive_references": invalid,
    }

    return dict(sorted(registry.items())), report
