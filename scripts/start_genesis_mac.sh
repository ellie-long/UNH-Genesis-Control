#!/usr/bin/env bash
set -euo pipefail

# ================================================================
# UNH Genesis Phase-I -- macOS startup
#
# Starts:
#   1. IOC Manager
#   2. Microwave SPD1305X IOC (read-only physical baseline)
#      Vaunix, Microwave Supervisor and PCL602 Robot are NOT
#      automatically started before physical commissioning.
#   3. ssRF SMC100A IOC
#   4. Keysight WaveGen IOC
#   5. NMR switch IOC
#   6. ssRF switch IOC
#   7. EPICS archiver
#   8. Enables archiving
#   9. Verifies the live device IOCs
#  10. Launches Phoebus with Genesis PVA settings
#
# Default hardware mode is intentionally READ-ONLY.
# ================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ENV_NAME="genesis-p1"

PHOEBUS_BIN="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
PHOEBUS_SETTINGS="${REPO_ROOT}/configs/phoebus/genesis_mac_settings.ini"
# Temporary Phase-I common entry screen.
# Microwave subsystem GUI: gui/genesis_microwave.bob
# Replace this with the future integrated Genesis overview GUI.
MAIN_GUI="${REPO_ROOT}/gui/genesis_ssrf.bob"

MW_SETTINGS="${REPO_ROOT}/configs/microwave/spd1305x/settings.yaml"
SMC_SETTINGS="${REPO_ROOT}/configs/ssrf/smc100a/settings.yaml"
WGEN_SETTINGS="${REPO_ROOT}/configs/ssrf/keysight_wavegen/settings.yaml"
NMR_SWITCH_SETTINGS="${REPO_ROOT}/configs/ssrf/nmr_switch/settings.yaml"
RF_SWITCH_SETTINGS="${REPO_ROOT}/configs/ssrf/rf_switch/settings.yaml"

echo
echo "============================================================"
echo " UNH Genesis Phase-I -- macOS Startup"
echo "============================================================"
echo "Repository: ${REPO_ROOT}"
echo

# ----------------------------------------------------------------
# Conda environment
# ----------------------------------------------------------------

if [[ "${CONDA_DEFAULT_ENV:-}" != "${ENV_NAME}" ]]; then
    if command -v conda >/dev/null 2>&1; then
        CONDA_BASE="$(conda info --base)"
        # shellcheck disable=SC1091
        source "${CONDA_BASE}/etc/profile.d/conda.sh"
        conda activate "${ENV_NAME}"
    else
        echo "ERROR: Conda is not available."
        echo "Activate '${ENV_NAME}' manually and rerun this script."
        exit 1
    fi
fi

echo "[OK] Conda environment: ${CONDA_DEFAULT_ENV}"

# ----------------------------------------------------------------
# Required files/programs
# ----------------------------------------------------------------

command -v screen >/dev/null 2>&1 || {
    echo "ERROR: GNU screen is not installed."
    exit 1
}

[[ -f "${PHOEBUS_BIN}" ]] || {
    echo "ERROR: Phoebus launcher not found:"
    echo "  ${PHOEBUS_BIN}"
    exit 1
}

[[ -f "${PHOEBUS_SETTINGS}" ]] || {
    echo "ERROR: Genesis Phoebus settings not found:"
    echo "  ${PHOEBUS_SETTINGS}"
    exit 1
}

[[ -f "${MAIN_GUI}" ]] || {
    echo "ERROR: Genesis Phase-I entry GUI not found:"
    echo "  ${MAIN_GUI}"
    exit 1
}

# ----------------------------------------------------------------
# Safety check
# ----------------------------------------------------------------

# ----------------------------------------------------------------
# Safe automatic-start configuration
# ----------------------------------------------------------------

check_read_only_config()
{
    local label="$1"
    local file="$2"

    [[ -f "${file}" ]] || {
        echo "ERROR: ${label} settings file not found:"
        echo "  ${file}"
        exit 1
    }

    if ! grep -Eq \
        '^[[:space:]]*write_enabled:[[:space:]]*false[[:space:]]*$' \
        "${file}"; then

        echo
        echo "ERROR: ${label} is not configured for read-only startup."
        echo "Expected:"
        echo "  write_enabled: false"
        echo
        echo "Review ${file} before continuing."
        exit 1
    fi

    if ! grep -Eq \
        '^[[:space:]]*initialize_on_connect:[[:space:]]*false[[:space:]]*$' \
        "${file}"; then

        echo
        echo "ERROR: ${label} initialize_on_connect is not false."
        echo "Refusing automatic startup."
        exit 1
    fi

    echo "[OK] ${label}: READ-ONLY"
    echo "[OK] ${label}: initialize_on_connect=false"
}

check_read_only_config \
    "Microwave SPD1305X" \
    "${MW_SETTINGS}"

check_read_only_config \
    "ssRF SMC100A" \
    "${SMC_SETTINGS}"

check_read_only_config \
    "Keysight WaveGen" \
    "${WGEN_SETTINGS}"

check_read_only_config \
    "NMR RF switch" \
    "${NMR_SWITCH_SETTINGS}"

check_read_only_config \
    "ssRF switch" \
    "${RF_SWITCH_SETTINGS}"

# ----------------------------------------------------------------
# EPICS Channel Access environment for Python tools / archiver
# ----------------------------------------------------------------

export EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5066 127.0.0.1:5067 127.0.0.1:5068 127.0.0.1:5069 127.0.0.1:5070 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074 127.0.0.1:5085 127.0.0.1:5087"
export EPICS_CA_AUTO_ADDR_LIST=NO

# ----------------------------------------------------------------
# GNU screen session helper
# ----------------------------------------------------------------

screen_session_exists()
{
    local name="$1"
    local listing

    # screen -ls can return a non-zero status even when sessions exist.
    # Capture its output without allowing pipefail to affect detection.
    listing="$(screen -ls 2>&1 || true)"

    grep -E "\.${name}[[:space:]]" <<< "${listing}" >/dev/null
}

# ----------------------------------------------------------------
# IOC Manager
# ----------------------------------------------------------------

if screen_session_exists "ioc-manager"; then
    echo "[OK] IOC Manager already running"
else
    echo "[START] IOC Manager"
    bash ./start_ioc_manager.sh
    sleep 3
fi

# ----------------------------------------------------------------
# Managed IOC helper
# ----------------------------------------------------------------

start_managed_ioc()
{
    local name="$1"

    if screen_session_exists "${name}"; then
        echo "[OK] ${name} already running"
        return
    fi

    echo "[START] ${name}"

    python - "${name}" <<'PY'
import sys
from tools.ioc_cli import load_settings, start_ioc

name = sys.argv[1]
settings = load_settings()
print(start_ioc(settings, name))
PY

    sleep 4
}

start_managed_ioc "mw_spd1305x"

echo "[SAFE] Microwave normal startup includes SPD1305X only."
echo "[SAFE] Vaunix, Microwave Supervisor and PCL602 Robot remain manual commissioning IOCs."

start_managed_ioc "ssrf_smc100a"
start_managed_ioc "ssrf_keysight_wavegen"
start_managed_ioc "ssrf_nmr_switch"
start_managed_ioc "ssrf_rf_switch"
start_managed_ioc "ssrf_interlock"
start_managed_ioc "ssrf_sequencer"
start_managed_ioc "archiver"

# Allow EPICS records to settle.
sleep 4

# ----------------------------------------------------------------
# Enable archiving and verify the live system
# ----------------------------------------------------------------

python - <<'PY'
import asyncio
from aioca import caget, caput


async def get(pv, timeout=3):
    try:
        return await caget(pv, timeout=timeout)
    except Exception as exc:
        print(f"WARNING: could not read {pv}: {exc}")
        return None


async def main():
    archive_enable = await get("UNH:GEN:Archive_Enable")

    if archive_enable is not None and int(archive_enable) == 0:
        print("[START] Enabling EPICS archiver")

        await caput(
            "UNH:GEN:Archive_Enable",
            1,
            wait=True,
            timeout=3,
        )

        await asyncio.sleep(5)

    values = {
        "Microwave manager":
            await get("UNH:GEN:MAN:mw_spd1305x_control"),

        "SMC100A manager":
            await get("UNH:GEN:MAN:ssrf_smc100a_control"),

        "WaveGen manager":
            await get("UNH:GEN:MAN:ssrf_keysight_wavegen_control"),

        "NMR switch manager":
            await get("UNH:GEN:MAN:ssrf_nmr_switch_control"),

        "ssRF switch manager":
            await get("UNH:GEN:MAN:ssrf_rf_switch_control"),

        "Interlock manager":
            await get("UNH:GEN:MAN:ssrf_interlock_control"),

        "Interlock inputs":
            await get("UNH:GEN:SSRF:ILK:INPUTS_OK"),

        "Interlock OK":
            await get("UNH:GEN:SSRF:ILK:INTERLOCK_OK"),

        "Interlock state":
            await get("UNH:GEN:SSRF:ILK:STATE"),

        "NMR connect permit":
            await get("UNH:GEN:SSRF:ILK:NMR_CONNECT_PERMIT"),

        "ssRF connect permit":
            await get("UNH:GEN:SSRF:ILK:SSRF_CONNECT_PERMIT"),

        "RF enable permit":
            await get("UNH:GEN:SSRF:ILK:RF_ENABLE_PERMIT"),

        "Interlock reason":
            await get("UNH:GEN:SSRF:ILK:REASON"),

        "Sequencer manager":
            await get("UNH:GEN:MAN:ssrf_sequencer_control"),

        "Sequencer parameters":
            await get("UNH:GEN:SSRF:SEQ:PARAMETERS_OK"),

        "Sequencer ready":
            await get("UNH:GEN:SSRF:SEQ:READY"),

        "Sequencer state":
            await get("UNH:GEN:SSRF:SEQ:STATE"),

        "Sequencer reason":
            await get("UNH:GEN:SSRF:SEQ:REASON"),

        "Microwave COMM":
            await get("UNH:GEN:MW:PS:COMM"),

        "Microwave output":
            await get("UNH:GEN:MW:PS:OUTPUT_RBV"),

        "SMC100A COMM":
            await get("UNH:GEN:SSRF:GEN:COMM"),

        "SMC write enabled":
            await get("UNH:GEN:SSRF:GEN:WRITE_ENABLED"),

        "SMC RF":
            await get("UNH:GEN:SSRF:GEN:RF_RBV"),

        "SMC FM":
            await get("UNH:GEN:SSRF:GEN:FM_RBV"),

        "SMC sweep arm":
            await get("UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV"),

        "WaveGen COMM":
            await get("UNH:GEN:SSRF:WGEN:COMM"),

        "WaveGen writes":
            await get("UNH:GEN:SSRF:WGEN:WRITE_ENABLED"),

        "WaveGen output":
            await get("UNH:GEN:SSRF:WGEN:OUTPUT_RBV"),

        "NMR switch COMM":
            await get("UNH:GEN:SSRF:NMR_SW:COMM"),

        "NMR switch writes":
            await get("UNH:GEN:SSRF:NMR_SW:WRITE_ENABLED"),

        "NMR switch mask":
            await get("UNH:GEN:SSRF:NMR_SW:MASK_RBV"),

        "NMR switch state":
            await get("UNH:GEN:SSRF:NMR_SW:STATE_RBV"),

        "ssRF switch COMM":
            await get("UNH:GEN:SSRF:RF_SW:COMM"),

        "ssRF switch writes":
            await get("UNH:GEN:SSRF:RF_SW:WRITE_ENABLED"),

        "ssRF switch mask":
            await get("UNH:GEN:SSRF:RF_SW:MASK_RBV"),

        "ssRF switch state":
            await get("UNH:GEN:SSRF:RF_SW:STATE_RBV"),

        "Archive status":
            await get("UNH:GEN:Archive_Status"),

        "Archive PV count":
            await get("UNH:GEN:Archive_PV_Count"),
    }

    print()
    print("Genesis live status")
    print("-------------------")

    for key, value in values.items():
        print(f"{key:22s}: {value}")

    print()

    if values["Microwave COMM"] is not None \
            and int(values["Microwave COMM"]) != 1:
        print(
            "WARNING: Microwave IOC is running but "
            "hardware COMM is not connected."
        )

    if values["Microwave output"] is not None \
            and int(values["Microwave output"]) != 0:
        print("WARNING: Microwave physical output is ON.")

    if values["SMC100A COMM"] is not None \
            and int(values["SMC100A COMM"]) != 1:
        print(
            "WARNING: SMC100A IOC is running but "
            "instrument COMM is not connected."
        )

    if values["SMC write enabled"] is not None \
            and int(values["SMC write enabled"]) != 0:
        print("WARNING: SMC100A hardware writes are enabled.")

    for key in ("SMC RF", "SMC FM", "SMC sweep arm"):
        value = values[key]
        if value is not None and int(value) != 0:
            print(f"WARNING: {key} is active.")

    if values["WaveGen COMM"] is not None \
            and int(values["WaveGen COMM"]) != 1:
        print(
            "WARNING: WaveGen IOC is running but "
            "instrument COMM is not connected."
        )

    if values["WaveGen writes"] is not None \
            and int(values["WaveGen writes"]) != 0:
        print("WARNING: WaveGen hardware writes are enabled.")

    if values["WaveGen output"] is not None \
            and int(values["WaveGen output"]) != 0:
        print("WARNING: WaveGen physical output is ON.")

    for label in ("NMR", "ssRF"):
        comm = values[f"{label} switch COMM"]
        writes = values[f"{label} switch writes"]
        mask = values[f"{label} switch mask"]

        if comm is not None and int(comm) != 1:
            print(
                f"WARNING: {label} switch IOC is running "
                "but hardware COMM is not connected."
            )

        if writes is not None and int(writes) != 0:
            print(
                f"WARNING: {label} switch hardware writes "
                "are enabled."
            )

        if mask is not None and int(mask) in (1, 2):
            print(
                f"WARNING: {label} switch is in MIXED state "
                f"(mask {mask})."
            )

    if values["Interlock inputs"] is None:
        print("WARNING: ssRF interlock inputs could not be read.")
    elif int(values["Interlock inputs"]) != 1:
        print(
            "WARNING: ssRF interlock does not have valid "
            "confirmed hardware inputs."
        )

    if values["Interlock OK"] is None:
        print("WARNING: ssRF interlock state could not be read.")
    elif int(values["Interlock OK"]) != 1:
        print(
            "WARNING: ssRF INTERLOCK IS BLOCKED: "
            f"{values['Interlock state']} - "
            f"{values['Interlock reason']}"
        )

    if values["Archive status"] is not None \
            and int(values["Archive status"]) != 1:
        print("WARNING: Archiver is not active.")


asyncio.run(main())
PY

# ----------------------------------------------------------------
# Phoebus
# ----------------------------------------------------------------

if pgrep -f \
    '/Applications/CSS_Phoebus.app/.*product-.*\.jar' \
    >/dev/null 2>&1; then

    echo
    echo "Phoebus is already running."
    echo "Close the existing Phoebus instance and rerun this script"
    echo "if it was not started with the Genesis PVA configuration."
    echo
else
    echo "[START] Phoebus"

    : > /tmp/genesis_phoebus.log

    "${PHOEBUS_BIN}" \
        -settings "${PHOEBUS_SETTINGS}" \
        -resource "${MAIN_GUI}" \
        >/tmp/genesis_phoebus.log 2>&1

    sleep 5
fi

echo
echo "============================================================"
echo " Genesis startup complete"
echo "============================================================"
echo
echo "Microwave IOC : read-only"
echo "SMC100A IOC   : read-only"
echo "WaveGen IOC   : read-only"
echo "NMR switch IOC: read-only"
echo "ssRF switch IOC: read-only"
echo "ssRF interlock : managed safety logic"
echo "ssRF sequencer : managed read-only burn readiness"
echo "Phoebus GUI   : gui/genesis_ssrf.bob"
echo "Phoebus PVs   : PVAccess"
echo "Archiver      : enabled"
echo
echo "For live hardware writes, follow:"
echo "  docs/MACOS_GENESIS_QUICK_START.md"
echo
