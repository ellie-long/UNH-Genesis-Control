#!/usr/bin/env bash
set -euo pipefail

# ================================================================
# UNH Genesis Phase-I -- macOS startup
#
# Starts:
#   1. IOC Manager
#   2. Microwave SPD1305X IOC
#   3. EPICS archiver
#   4. Enables archiving
#   5. Verifies the live Microwave IOC
#   6. Launches Phoebus with Genesis PVA settings
#
# Default hardware mode is intentionally READ-ONLY.
# ================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ENV_NAME="genesis-p1"

PHOEBUS_BIN="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
PHOEBUS_SETTINGS="${REPO_ROOT}/configs/phoebus/genesis_mac_settings.ini"
MAIN_GUI="${REPO_ROOT}/gui/genesis_microwave.bob"
DEVICE_SETTINGS="${REPO_ROOT}/configs/microwave/spd1305x/settings.yaml"

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
    echo "ERROR: Genesis Microwave GUI not found:"
    echo "  ${MAIN_GUI}"
    exit 1
}

# ----------------------------------------------------------------
# Safety check
# ----------------------------------------------------------------

if ! grep -Eq '^[[:space:]]*write_enabled:[[:space:]]*false[[:space:]]*$' \
    "${DEVICE_SETTINGS}"; then
    echo
    echo "ERROR: Microwave IOC is not configured for read-only startup."
    echo
    echo "Expected:"
    echo "  write_enabled: false"
    echo
    echo "Review ${DEVICE_SETTINGS} before continuing."
    exit 1
fi

if ! grep -Eq '^[[:space:]]*initialize_on_connect:[[:space:]]*false[[:space:]]*$' \
    "${DEVICE_SETTINGS}"; then
    echo
    echo "ERROR: initialize_on_connect is not false."
    echo "Refusing automatic startup."
    exit 1
fi

echo "[OK] Microwave startup mode: READ-ONLY"
echo "[OK] initialize_on_connect: false"

# ----------------------------------------------------------------
# EPICS Channel Access environment for Python tools / archiver
# ----------------------------------------------------------------

export EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5066 127.0.0.1:5067"
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
start_managed_ioc "archiver"

# Allow EPICS records to settle.
sleep 4

# ----------------------------------------------------------------
# Enable archiving and verify the live system
# ----------------------------------------------------------------

python - <<'PY'
import asyncio
from aioca import caget, caput

P = "UNH:GEN:MW:PS"

async def get(pv, timeout=3):
    return await caget(pv, timeout=timeout)

async def main():
    # Enable archiving if necessary.
    enable = await get("UNH:GEN:Archive_Enable")

    if int(enable) == 0:
        print("[START] Enabling EPICS archiver")
        await caput(
            "UNH:GEN:Archive_Enable",
            1,
            wait=True,
            timeout=3,
        )
        await asyncio.sleep(5)

    values = {
        "IOC Manager control":
            await get("UNH:GEN:MAN:mw_spd1305x_control"),
        "Hardware COMM":
            await get(f"{P}:COMM"),
        "Voltage setpoint":
            await get(f"{P}:VSET_RBV"),
        "Current setpoint":
            await get(f"{P}:ISET_RBV"),
        "Output":
            await get(f"{P}:OUTPUT_RBV"),
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

    if int(values["Hardware COMM"]) != 1:
        print("WARNING: SPD1305X IOC is running but hardware COMM is not connected.")

    if int(values["Output"]) != 0:
        print("WARNING: Hardware OUTPUT is currently ON.")

    if int(values["Archive status"]) != 1:
        print("WARNING: Archiver is not active.")

    if int(values["Archive PV count"]) != 12:
        print(
            "WARNING: Expected 12 archived Microwave PVs, "
            f"found {values['Archive PV count']}."
        )

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
echo "Phoebus GUI   : gui/genesis_microwave.bob"
echo "Phoebus PVs   : PVAccess"
echo "Archiver      : enabled"
echo
echo "For live hardware writes, follow:"
echo "  docs/MACOS_GENESIS_QUICK_START.md"
echo
