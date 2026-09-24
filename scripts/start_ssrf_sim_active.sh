#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

cd "${ROOT}"

PROFILE="${ROOT}/configs/commissioning/ssrf_sim_active"
MANAGER_SETTINGS="${PROFILE}/ioc_manager"

MODE="${1:---check-only}"

if [[ "${MODE}" != "--check-only" && "${MODE}" != "--activate" ]]; then
    echo "Usage:"
    echo "  $0 --check-only"
    echo "  $0 --activate"
    exit 64
fi


echo
echo "============================================================"
echo " Genesis ssRF Simulator Commissioning Handoff"
echo "============================================================"
echo "Mode: ${MODE}"


# ------------------------------------------------------------------
# 1. Static commissioning-profile validation
# ------------------------------------------------------------------

echo
echo "===== COMMISSIONING PROFILE ====="

python tools/validate_ssrf_sim_active.py \
    --profile "${PROFILE}"


# ------------------------------------------------------------------
# 2. Simulator endpoints must already exist and be localhost
# ------------------------------------------------------------------

echo
echo "===== SIMULATOR ENDPOINTS ====="

python - <<'PY'
import socket


ENDPOINTS = (
    ("SMC100A", "127.0.0.1", 5025),
    ("Keysight WaveGen", "127.0.0.1", 5027),
    ("NMR switch", "127.0.0.1", 8081),
    ("ssRF switch", "127.0.0.1", 8082),
)

failed = []

for name, host, port in ENDPOINTS:
    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    sock.settimeout(0.5)

    try:
        sock.connect((host, port))
    except OSError as exc:
        failed.append(
            f"{name}: {host}:{port} ({exc})"
        )
        print(
            f"[FAIL] {name}: {host}:{port}"
        )
    else:
        print(
            f"[OK] {name}: {host}:{port}"
        )
    finally:
        sock.close()

if failed:
    print()
    for item in failed:
        print(item)
    raise SystemExit(2)
PY


# ------------------------------------------------------------------
# 3. Verify currently running NORMAL ssRF deployment is safe
#    before we would ever stop it.
# ------------------------------------------------------------------

echo
echo "===== NORMAL DEPLOYMENT STATE ====="

NORMAL_SSRF_SESSIONS=(
    "ssrf_smc100a"
    "ssrf_keysight_wavegen"
    "ssrf_nmr_switch"
    "ssrf_rf_switch"
    "ssrf_interlock"
    "ssrf_sequencer"
)

SCREEN_LIST="$(
    screen -ls 2>/dev/null || true
)"

NORMAL_RUNNING=0

for name in "${NORMAL_SSRF_SESSIONS[@]}"; do
    if printf '%s\n' "${SCREEN_LIST}" \
        | grep -Eq "[.]${name}[[:space:]]"
    then
        NORMAL_RUNNING=$((NORMAL_RUNNING + 1))
    fi
done

echo "normal ssRF sessions running = ${NORMAL_RUNNING}/6"

if [[ "${NORMAL_RUNNING}" -eq 0 ]]; then

    echo
    echo "[OK] normal ssRF IOC sessions are already stopped"
    echo "[OK] continuing from clean handoff boundary"

elif [[ "${NORMAL_RUNNING}" -ne 6 ]]; then

    echo
    echo "[BLOCKED] partial normal ssRF deployment detected"
    echo
    echo "Running normal ssRF sessions:"

    for name in "${NORMAL_SSRF_SESSIONS[@]}"; do
        if printf '%s\n' "${SCREEN_LIST}" \
            | grep -Eq "[.]${name}[[:space:]]"
        then
            echo "  - ${name}"
        fi
    done

    echo
    echo "Refusing simulator activation from a mixed state."
    exit 65

else

    echo
    echo "===== NORMAL DEPLOYMENT SAFETY SNAPSHOT ====="

    export EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5068 127.0.0.1:5070 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074"
    export EPICS_CA_AUTO_ADDR_LIST=NO

    PYTHONPATH="${ROOT}" python - <<'PYSAFE'
import asyncio
import aioca


PVS = {
    "execution_enabled":
        "UNH:GEN:SSRF:SEQ:EXECUTION_ENABLED",

    "smc_rf":
        "UNH:GEN:SSRF:GEN:RF_RBV",

    "smc_fm":
        "UNH:GEN:SSRF:GEN:FM_RBV",

    "smc_arm":
        "UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV",

    "wavegen_output":
        "UNH:GEN:SSRF:WGEN:OUTPUT_RBV",

    "nmr_mask":
        "UNH:GEN:SSRF:NMR_SW:MASK_RBV",

    "ssrf_mask":
        "UNH:GEN:SSRF:RF_SW:MASK_RBV",
}


async def get(name, pv):
    try:
        return await aioca.caget(
            pv,
            timeout=3.0,
        )
    except Exception as exc:
        raise RuntimeError(
            f"cannot read {name} ({pv}): {exc}"
        ) from exc


async def main():
    values = {}

    for name, pv in PVS.items():
        values[name] = await get(
            name,
            pv,
        )

    for name, value in values.items():
        print(
            f"{name:20s} = {value}"
        )

    failures = []

    if int(
        values["execution_enabled"]
    ) != 0:
        failures.append(
            "normal sequencer execution is enabled"
        )

    if int(values["smc_rf"]) != 0:
        failures.append(
            "SMC RF is ON"
        )

    if int(values["smc_fm"]) != 0:
        failures.append(
            "SMC FM is ON"
        )

    if int(values["smc_arm"]) != 0:
        failures.append(
            "SMC sweep is armed"
        )

    if int(
        values["wavegen_output"]
    ) != 0:
        failures.append(
            "WaveGen output is ON"
        )

    if int(values["nmr_mask"]) not in (0, 3):
        failures.append(
            "NMR switch mask is not a safe "
            "handoff state (expected 0 or 3)"
        )

    if int(values["ssrf_mask"]) != 3:
        failures.append(
            "ssRF switch is not disconnected "
            "(expected mask 3)"
        )

    if failures:
        print()
        print(
            "[BLOCKED] normal deployment "
            "is not handoff-safe"
        )

        for failure in failures:
            print(
                f"  - {failure}"
            )

        raise SystemExit(3)

    print()
    print(
        "[OK] normal deployment is handoff-safe"
    )


asyncio.run(main())
PYSAFE

fi


# ------------------------------------------------------------------
# 4. Identify current sessions
# ------------------------------------------------------------------

echo
echo "===== CURRENT SCREEN SESSIONS ====="

screen -ls || true


# ------------------------------------------------------------------
# 5. Check-only stops here.
# ------------------------------------------------------------------

if [[ "${MODE}" == "--check-only" ]]; then
    echo
    echo "============================================================"
    echo " HANDOFF CHECK PASSED"
    echo "============================================================"
    echo
    echo "No process was stopped."
    echo "No IOC was started."
    echo "No EPICS device write was performed."
    exit 0
fi


# ------------------------------------------------------------------
# Everything below here is reached only with --activate.
# ------------------------------------------------------------------

if [[ "${NORMAL_RUNNING}" -eq 6 ]]; then

echo
echo "===== STOP NORMAL SSRF IOCS ====="

PYTHONPATH="${ROOT}" python - <<'PY'
import asyncio
import aioca


MANAGER = "UNH:GEN:MAN"

# Stop higher-level logic first, then devices.
ORDER = (
    "ssrf_sequencer",
    "ssrf_interlock",
    "ssrf_rf_switch",
    "ssrf_nmr_switch",
    "ssrf_keysight_wavegen",
    "ssrf_smc100a",
)


async def main():
    for name in ORDER:
        pv = f"{MANAGER}:{name}_control"

        print(
            f"[STOP] {name}"
        )

        await aioca.caput(
            pv,
            0,
            wait=True,
            timeout=3.0,
        )

        await asyncio.sleep(0.5)


asyncio.run(main())
PY


echo
echo "===== WAIT FOR NORMAL SSRF SESSIONS TO STOP ====="

NORMAL_SESSIONS=(
    "ssrf_sequencer"
    "ssrf_interlock"
    "ssrf_rf_switch"
    "ssrf_nmr_switch"
    "ssrf_keysight_wavegen"
    "ssrf_smc100a"
)

for attempt in $(seq 1 20); do
    REMAINING=0
    LISTING="$(screen -ls 2>/dev/null || true)"

    for name in "${NORMAL_SESSIONS[@]}"; do
        if printf '%s\n' "${LISTING}" \
            | grep -Eq "[.]${name}[[:space:]]"
        then
            REMAINING=1
        fi
    done

    if [[ "${REMAINING}" -eq 0 ]]; then
        break
    fi

    sleep 0.5
done

LISTING="$(screen -ls 2>/dev/null || true)"

for name in "${NORMAL_SESSIONS[@]}"; do
    if printf '%s\n' "${LISTING}" \
        | grep -Eq "[.]${name}[[:space:]]"
    then
        echo "[FAIL] normal session still running: ${name}"
        exit 4
    fi
done

echo "[OK] normal ssRF IOC sessions stopped"


else

    echo
    echo "===== STOP NORMAL SSRF IOCS ====="
    echo "[OK] normal ssRF IOC sessions already stopped"
    echo "[OK] no manager stop commands required"

fi


echo
echo "===== STOP NORMAL IOC MANAGER ====="

MANAGER_SESSION="$(
    {
        screen -ls 2>/dev/null || true
    } | awk '/[.]ioc-manager[[:space:]]/ {print $1; exit}'
)"

if [[ -n "${MANAGER_SESSION}" ]]; then
    echo "[STOP] ${MANAGER_SESSION}"

    screen \
        -S "${MANAGER_SESSION}" \
        -X quit

    sleep 1
fi

if screen -ls 2>/dev/null \
    | grep -Eq '[.]ioc-manager[[:space:]]'
then
    echo "[FAIL] normal IOC manager still running"
    exit 5
fi

if ps aux \
    | grep '[i]oc_manager.py' \
    >/dev/null
then
    echo "[FAIL] normal ioc_manager.py process still running"
    ps aux | grep '[i]oc_manager.py'
    exit 5
fi

echo "[OK] normal IOC manager stopped"


# ------------------------------------------------------------------
# 6. Stop normal archiver.
#
# The normal archiver uses the same ssRF PV namespace and must not
# record simulator commissioning activity.
# ------------------------------------------------------------------

echo
echo "===== STOP NORMAL ARCHIVER ====="

ARCHIVER_SESSION="$(
    {
        screen -ls 2>/dev/null || true
    } | awk '/[.]archiver[[:space:]]/ {print $1; exit}'
)"

if [[ -n "${ARCHIVER_SESSION}" ]]; then
    echo "[STOP] ${ARCHIVER_SESSION}"

    screen \
        -S "${ARCHIVER_SESSION}" \
        -X quit

    sleep 1
fi

if screen -ls 2>/dev/null \
    | grep -Eq '[.]archiver[[:space:]]'
then
    echo "[FAIL] normal archiver still running"
    exit 6
fi

echo "[OK] normal archiver stopped"


# ------------------------------------------------------------------
# 7. Re-run isolation guard after normal ssRF deployment,
#    manager, and archiver are gone.
# ------------------------------------------------------------------

echo
echo "===== ISOLATION GUARD ====="

./scripts/check_ssrf_sim_active.sh


# ------------------------------------------------------------------
# 7. Start dedicated simulator IOC Manager
# ------------------------------------------------------------------

echo
echo "===== START SIMULATOR IOC MANAGER ====="

mkdir -p \
    logs/commissioning/ssrf_sim_active

screen -dmS \
    ssrf-sim-manager \
    bash

sleep 0.5

MANAGER_COMMAND="cd ${ROOT} && env EPICS_CAS_SERVER_PORT=5064 EPICS_PVA_SERVER_PORT=5075 ${CONDA_PREFIX}/bin/python ${ROOT}/ioc_manager.py -s ${MANAGER_SETTINGS}"

screen \
    -S ssrf-sim-manager \
    -p 0 \
    -X stuff \
    "${MANAGER_COMMAND}"

screen \
    -S ssrf-sim-manager \
    -p 0 \
    -X stuff \
    $'\r'

sleep 2


# ------------------------------------------------------------------
# 8. Start simulator-backed IOCs through dedicated manager
# ------------------------------------------------------------------

echo
echo "===== START SIMULATOR IOCS ====="

export EPICS_CA_ADDR_LIST="127.0.0.1:5064"
export EPICS_CA_AUTO_ADDR_LIST=NO

PYTHONPATH="${ROOT}" python - <<'PY'
import asyncio
import aioca


MANAGER = "UNH:GEN:MAN"

ORDER = (
    "ssrf_smc100a",
    "ssrf_keysight_wavegen",
    "ssrf_nmr_switch",
    "ssrf_rf_switch",
    "ssrf_interlock",
    "ssrf_sequencer",
)


async def wait_manager():
    """Wait for the simulator IOC Manager CA server."""

    pv = (
        "UNH:GEN:MAN:"
        "ssrf_smc100a_control"
    )

    timeout_s = 60.0

    deadline = (
        asyncio.get_running_loop().time()
        + timeout_s
    )

    last_error = None

    while (
        asyncio.get_running_loop().time()
        < deadline
    ):
        try:
            value = await aioca.caget(
                pv,
                timeout=2.0,
            )

            print(
                "[OK] simulator IOC manager "
                f"reachable: {pv} = {value}"
            )

            return

        except Exception as exc:
            last_error = exc

        await asyncio.sleep(0.5)

    raise RuntimeError(
        "simulator IOC manager did not become "
        f"reachable within {timeout_s:.0f} s; "
        f"last error: {last_error}"
    )

async def main():
    await wait_manager()

    print(
        "[OK] simulator IOC manager reachable"
    )

    for name in ORDER:
        pv = f"{MANAGER}:{name}_control"

        print(
            f"[START] {name}"
        )

        await aioca.caput(
            pv,
            1,
            wait=True,
            timeout=3.0,
        )

        await asyncio.sleep(1.0)


asyncio.run(main())
PY


# ------------------------------------------------------------------
# 9. Verify active simulator deployment
# ------------------------------------------------------------------

echo
echo "===== VERIFY ACTIVE SIMULATOR DEPLOYMENT ====="

export EPICS_CA_ADDR_LIST="127.0.0.1:5068 127.0.0.1:5070 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074"
export EPICS_CA_AUTO_ADDR_LIST=NO

PYTHONPATH="${ROOT}" python - <<'PY'
import asyncio
import aioca


PVS = {
    "SMC COMM":
        "UNH:GEN:SSRF:GEN:COMM",

    "SMC writes":
        "UNH:GEN:SSRF:GEN:WRITE_ENABLED",

    "WaveGen COMM":
        "UNH:GEN:SSRF:WGEN:COMM",

    "WaveGen writes":
        "UNH:GEN:SSRF:WGEN:WRITE_ENABLED",

    "NMR switch COMM":
        "UNH:GEN:SSRF:NMR_SW:COMM",

    "NMR writes":
        "UNH:GEN:SSRF:NMR_SW:WRITE_ENABLED",

    "ssRF switch COMM":
        "UNH:GEN:SSRF:RF_SW:COMM",

    "ssRF writes":
        "UNH:GEN:SSRF:RF_SW:WRITE_ENABLED",

    "execution enabled":
        "UNH:GEN:SSRF:SEQ:EXECUTION_ENABLED",

    "execution state":
        "UNH:GEN:SSRF:SEQ:EXECUTION_STATE",

    "SMC RF":
        "UNH:GEN:SSRF:GEN:RF_RBV",

    "SMC FM":
        "UNH:GEN:SSRF:GEN:FM_RBV",

    "SMC arm":
        "UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV",

    "WaveGen output":
        "UNH:GEN:SSRF:WGEN:OUTPUT_RBV",

    "NMR mask":
        "UNH:GEN:SSRF:NMR_SW:MASK_RBV",

    "ssRF mask":
        "UNH:GEN:SSRF:RF_SW:MASK_RBV",

    "interlock state":
        "UNH:GEN:SSRF:ILK:STATE",
}


async def get(pv):
    for _ in range(20):
        try:
            return await aioca.caget(
                pv,
                timeout=1.0,
            )
        except Exception:
            await asyncio.sleep(0.5)

    raise RuntimeError(
        f"PV did not become reachable: {pv}"
    )


async def main():
    values = {}

    for name, pv in PVS.items():
        values[name] = await get(pv)

    for name, value in values.items():
        print(
            f"{name:22s} = {value}"
        )

    assert int(values["SMC COMM"]) == 1
    assert int(values["SMC writes"]) == 1

    assert int(
        values["WaveGen COMM"]
    ) == 1

    assert int(
        values["WaveGen writes"]
    ) == 1

    assert int(
        values["NMR switch COMM"]
    ) == 1

    assert int(
        values["NMR writes"]
    ) == 1

    assert int(
        values["ssRF switch COMM"]
    ) == 1

    assert int(
        values["ssRF writes"]
    ) == 1

    assert int(
        values["execution enabled"]
    ) == 1

    assert int(values["SMC RF"]) == 0
    assert int(values["SMC FM"]) == 0
    assert int(values["SMC arm"]) == 0

    assert int(
        values["WaveGen output"]
    ) == 0

    nmr_mask = int(
        values["NMR mask"]
    )

    ssrf_mask = int(
        values["ssRF mask"]
    )

    interlock_state = str(
        values["interlock state"]
    )

    assert nmr_mask in (0, 3)
    assert ssrf_mask == 3

    if nmr_mask == 0:
        assert interlock_state == "NMR_LIVE"
    else:
        assert interlock_state == "SAFE_IDLE"

    print()
    print(
        "[PASS] simulator-active deployment "
        "is running in a safe non-burning state"
    )


asyncio.run(main())
PY


echo
echo "============================================================"
echo " SIMULATOR-ACTIVE HANDOFF COMPLETE"
echo "============================================================"
echo
echo "Execution is permitted by configuration."
echo "A burn requires explicit one-shot ARM + START."
