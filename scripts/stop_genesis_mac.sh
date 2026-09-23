#!/usr/bin/env bash
set -euo pipefail

# ================================================================
# UNH Genesis Phase-I -- macOS shutdown
#
# Safely stops:
#   1. Phoebus
#   2. ssRF SMC100A IOC
#   3. Microwave SPD1305X IOC
#   4. EPICS archiver
#   5. IOC Manager
#
# Device IOCs are stopped only after their active hardware outputs
# are confirmed safe.
#
# GENESIS_FORCE_STOP=1 may be used for development/recovery when
# hardware communication is unavailable and the operator has
# independently confirmed the physical system is safe.
# ================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ENV_NAME="genesis-p1"
FORCE_STOP="${GENESIS_FORCE_STOP:-0}"

echo
echo "============================================================"
echo " UNH Genesis Phase-I -- macOS Shutdown"
echo "============================================================"
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

# ----------------------------------------------------------------
# EPICS Channel Access
# ----------------------------------------------------------------

export EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5066 127.0.0.1:5067 127.0.0.1:5068 127.0.0.1:5070 127.0.0.1:5071 127.0.0.1:5072"
export EPICS_CA_AUTO_ADDR_LIST=NO

# ----------------------------------------------------------------
# GNU screen helper
# ----------------------------------------------------------------

screen_session_exists()
{
    local name="$1"
    local listing

    listing="$(screen -ls 2>&1 || true)"

    grep -E "\.${name}[[:space:]]" \
        <<< "${listing}" >/dev/null
}

# ----------------------------------------------------------------
# Hardware safety verification
# ----------------------------------------------------------------

if [[ "${FORCE_STOP}" == "1" ]]; then
    echo
    echo "WARNING: GENESIS_FORCE_STOP=1"
    echo "Hardware state verification is being bypassed."
    echo
else

    if screen_session_exists "mw_spd1305x"; then
        echo "[CHECK] Microwave SPD1305X safe state"

        python - <<'PY'
import asyncio
import sys
from aioca import caget

COMM = "UNH:GEN:MW:PS:COMM"
OUTPUT = "UNH:GEN:MW:PS:OUTPUT_RBV"


async def main():
    try:
        comm = await caget(COMM, timeout=3)
        output = await caget(OUTPUT, timeout=3)
    except Exception as exc:
        print(f"ERROR: Could not verify Microwave state: {exc}")
        print("Shutdown aborted.")
        sys.exit(1)

    print(f"Microwave COMM       = {comm}")
    print(f"Microwave OUTPUT_RBV = {output}")

    if int(comm) != 1:
        print("ERROR: Microwave communication is not healthy.")
        print("Cannot verify physical output state.")
        sys.exit(1)

    if int(output) != 0:
        print("ERROR: Microwave physical output is ON.")
        print("Turn it OFF before shutting down Genesis.")
        sys.exit(1)

    print("[OK] Microwave output confirmed OFF.")


asyncio.run(main())
PY
    fi

    if screen_session_exists "ssrf_smc100a"; then
        echo "[CHECK] ssRF SMC100A safe state"

        python - <<'PY'
import asyncio
import sys
from aioca import caget

P = "UNH:GEN:SSRF:GEN"


async def main():
    names = {
        "COMM": f"{P}:COMM",
        "RF_RBV": f"{P}:RF_RBV",
        "FM_RBV": f"{P}:FM_RBV",
        "SWEEP_ARM_RBV": f"{P}:SWEEP_ARM_RBV",
    }

    values = {}

    try:
        for name, pv in names.items():
            values[name] = await caget(pv, timeout=3)
    except Exception as exc:
        print(f"ERROR: Could not verify SMC100A state: {exc}")
        print("Shutdown aborted.")
        sys.exit(1)

    for name, value in values.items():
        print(f"SMC100A {name:14s} = {value}")

    if int(values["COMM"]) != 1:
        print("ERROR: SMC100A communication is not healthy.")
        print("Cannot verify physical RF state.")
        sys.exit(1)

    unsafe = [
        name
        for name in ("RF_RBV", "FM_RBV", "SWEEP_ARM_RBV")
        if int(values[name]) != 0
    ]

    if unsafe:
        print(
            "ERROR: SMC100A is not in a safe shutdown state: "
            + ", ".join(unsafe)
        )
        print("Disable RF, FM, and sweep arm before shutdown.")
        sys.exit(1)

    print("[OK] SMC100A RF/FM/sweep state confirmed safe.")


asyncio.run(main())
PY
    fi

    if screen_session_exists "ssrf_keysight_wavegen"; then
        echo "[CHECK] Keysight WaveGen safe state"

        python - <<'PY'
import asyncio
import sys
from aioca import caget

P = "UNH:GEN:SSRF:WGEN"


async def main():
    try:
        comm = await caget(
            f"{P}:COMM",
            timeout=3,
        )
        output = await caget(
            f"{P}:OUTPUT_RBV",
            timeout=3,
        )

    except Exception as exc:
        print(
            "ERROR: Could not verify WaveGen state: "
            f"{exc}"
        )
        print("Shutdown aborted.")
        sys.exit(1)

    print(f"WaveGen COMM       = {comm}")
    print(f"WaveGen OUTPUT_RBV = {output}")

    if int(comm) != 1:
        print(
            "ERROR: WaveGen communication is not healthy."
        )
        print(
            "Cannot verify physical WaveGen output state."
        )
        sys.exit(1)

    if int(output) != 0:
        print("ERROR: WaveGen physical output is ON.")
        print(
            "Turn it OFF before shutting down Genesis."
        )
        sys.exit(1)

    print("[OK] WaveGen output confirmed OFF.")


asyncio.run(main())
PY
    fi
fi

# ----------------------------------------------------------------
# Close Phoebus
# ----------------------------------------------------------------

if pgrep -f \
    '/Applications/CSS_Phoebus.app/.*product-.*\.jar' \
    >/dev/null 2>&1; then

    echo "[STOP] Phoebus"

    pkill -f \
        '/Applications/CSS_Phoebus.app/.*product-.*\.jar' \
        || true

    sleep 2
else
    echo "[OK] Phoebus already stopped"
fi

# ----------------------------------------------------------------
# Stop managed IOC helper
# ----------------------------------------------------------------

stop_managed_ioc()
{
    local name="$1"

    if screen_session_exists "${name}"; then
        echo "[STOP] ${name}"

        python - "${name}" <<'PY'
import sys
from tools.ioc_cli import load_settings, stop_ioc

name = sys.argv[1]
settings = load_settings()
print(stop_ioc(settings, name))
PY

        sleep 2
    else
        echo "[OK] ${name} already stopped"
    fi
}

# Keep the archiver alive while device IOCs shut down.
#
# RF-producing devices are stopped before switch-monitoring IOCs.
# Stopping the switch IOCs does NOT issue a route command.
stop_managed_ioc "ssrf_smc100a"
stop_managed_ioc "ssrf_keysight_wavegen"
stop_managed_ioc "ssrf_nmr_switch"
stop_managed_ioc "ssrf_rf_switch"
stop_managed_ioc "mw_spd1305x"
stop_managed_ioc "archiver"

# ----------------------------------------------------------------
# Stop IOC Manager last
# ----------------------------------------------------------------

if screen_session_exists "ioc-manager"; then
    echo "[STOP] IOC Manager"
    screen -S ioc-manager -X quit
    sleep 2
else
    echo "[OK] IOC Manager already stopped"
fi

echo
echo "============================================================"
echo " Genesis shutdown complete"
echo "============================================================"
echo

SCREEN_LIST="$(screen -ls 2>&1 || true)"

if grep -E \
    '\.(ioc-manager|mw_spd1305x|ssrf_smc100a|ssrf_keysight_wavegen|ssrf_nmr_switch|ssrf_rf_switch|archiver)[[:space:]]' \
    <<< "${SCREEN_LIST}" >/dev/null; then

    echo "WARNING: Genesis screen sessions remain:"
    echo "${SCREEN_LIST}"
else
    echo "[OK] No Genesis screen sessions remain."
fi

echo