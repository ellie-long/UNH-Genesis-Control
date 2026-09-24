#!/usr/bin/env python3
"""
Validate the Genesis ssRF simulator-active commissioning profile.

This performs configuration validation only. It never connects to EPICS
and never writes to any device.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROFILE = (
    ROOT
    / "configs"
    / "commissioning"
    / "ssrf_sim_active"
)


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _local_ca_addresses(value):
    addresses = str(value).split()

    if not addresses:
        return False

    return all(
        item.startswith("127.0.0.1:")
        for item in addresses
    )


def validate_profile(profile=DEFAULT_PROFILE):
    profile = Path(profile)
    errors = []

    # ------------------------------------------------------------
    # Writable simulator devices
    # ------------------------------------------------------------

    device_cases = (
        (
            "smc100a",
            "smc100a",
        ),
        (
            "keysight_wavegen",
            "keysight_wavegen",
        ),
        (
            "nmr_switch",
            "minicircuits_switch",
        ),
        (
            "rf_switch",
            "minicircuits_switch",
        ),
    )

    loaded = {}

    for folder, key in device_cases:
        path = (
            profile
            / folder
            / "settings.yaml"
        )

        if not path.is_file():
            errors.append(
                f"missing settings file: {path}"
            )
            continue

        data = load_yaml(path)
        loaded[folder] = data

        settings = data.get(key, {})

        if settings.get(
            "write_enabled"
        ) is not True:
            errors.append(
                f"{folder}: write_enabled "
                "must be true"
            )

        if settings.get(
            "initialize_on_connect"
        ) is not False:
            errors.append(
                f"{folder}: "
                "initialize_on_connect "
                "must be false"
            )

    # SMC must be the localhost simulator.
    if "smc100a" in loaded:
        settings = loaded[
            "smc100a"
        ]["smc100a"]

        if settings.get("ip") != "127.0.0.1":
            errors.append(
                "smc100a: non-local IP rejected"
            )

        if int(
            settings.get("port", -1)
        ) != 5025:
            errors.append(
                "smc100a: simulator port "
                "must be 5025"
            )

    # WaveGen must be the localhost VISA socket simulator.
    if "keysight_wavegen" in loaded:
        settings = loaded[
            "keysight_wavegen"
        ]["keysight_wavegen"]

        expected_resource = (
            "TCPIP0::127.0.0.1::"
            "5027::SOCKET"
        )

        if str(
            settings.get("resource", "")
        ) != expected_resource:
            errors.append(
                "keysight_wavegen: "
                "non-local simulator resource "
                "rejected"
            )

    # Mini-Circuits simulators.
    switch_cases = (
        (
            "nmr_switch",
            8081,
        ),
        (
            "rf_switch",
            8082,
        ),
    )

    for folder, expected_port in switch_cases:
        if folder not in loaded:
            continue

        settings = loaded[
            folder
        ]["minicircuits_switch"]

        if settings.get("ip") != "127.0.0.1":
            errors.append(
                f"{folder}: non-local IP rejected"
            )

        if int(
            settings.get("port", -1)
        ) != expected_port:
            errors.append(
                f"{folder}: simulator port "
                f"must be {expected_port}"
            )

    # ------------------------------------------------------------
    # Interlock / sequencer local CA inputs
    # ------------------------------------------------------------

    for folder in (
        "interlock",
        "sequencer",
    ):
        path = (
            profile
            / folder
            / "settings.yaml"
        )

        if not path.is_file():
            errors.append(
                f"missing settings file: {path}"
            )
            continue

        data = load_yaml(path)

        if not _local_ca_addresses(
            data.get(
                "general",
                {},
            ).get(
                "epics_addr_list",
                "",
            )
        ):
            errors.append(
                f"{folder}: non-local "
                "EPICS address rejected"
            )

    # Sequencer execution must be explicitly enabled.
    seq_path = (
        profile
        / "sequencer"
        / "settings.yaml"
    )

    if seq_path.is_file():
        seq = load_yaml(seq_path)

        enabled = (
            seq.get(
                "ssrf_sequencer",
                {},
            ).get(
                "execution_enabled",
                False,
            )
        )

        if enabled is not True:
            errors.append(
                "sequencer: execution_enabled "
                "must be true"
            )

    # ------------------------------------------------------------
    # Dedicated IOC Manager profile
    # ------------------------------------------------------------

    manager_path = (
        profile
        / "ioc_manager"
        / "settings.yaml"
    )

    if not manager_path.is_file():
        errors.append(
            f"missing settings file: "
            f"{manager_path}"
        )

    else:
        manager = load_yaml(
            manager_path
        )

        if not _local_ca_addresses(
            manager.get(
                "general",
                {},
            ).get(
                "epics_addr_list",
                "",
            )
        ):
            errors.append(
                "ioc_manager: non-local "
                "EPICS address rejected"
            )

        expected = {
            "ssrf_smc100a",
            "ssrf_keysight_wavegen",
            "ssrf_nmr_switch",
            "ssrf_rf_switch",
            "ssrf_interlock",
            "ssrf_sequencer",
        }

        actual = (
            set(manager)
            - {"general"}
        )

        if actual != expected:
            errors.append(
                "ioc_manager: unexpected "
                "IOC set"
            )

        expected_root = (
            "configs/commissioning/"
            "ssrf_sim_active/"
        )

        for name in expected & actual:
            entry = manager[name]

            folder = str(
                entry.get(
                    "settings_folder",
                    "",
                )
            )

            if not folder.startswith(
                expected_root
            ):
                errors.append(
                    f"{name}: settings_folder "
                    "is outside simulator profile"
                )

    return errors


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE),
    )

    args = parser.parse_args()

    errors = validate_profile(
        args.profile
    )

    if errors:
        print(
            "[FAIL] simulator-active "
            "profile rejected"
        )

        for error in errors:
            print(
                f"  - {error}"
            )

        raise SystemExit(1)

    print(
        "[OK] simulator-active profile "
        "is localhost-only"
    )
    print(
        "[OK] simulator write gates "
        "are intentional"
    )
    print(
        "[OK] initialize_on_connect "
        "remains false"
    )
    print(
        "[OK] sequencer execution "
        "permission is explicit"
    )


if __name__ == "__main__":
    main()
