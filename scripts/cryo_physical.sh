#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

cd "$ROOT"

MANAGER="configs/target_physical/ioc_manager/settings.yaml"
PHOEBUS_SETTINGS="$ROOT/configs/phoebus/target_physical.ini"
PHOEBUS_DISPLAY="$ROOT/gui/genesis_cryo.bob"

PYTHON_BIN="$(command -v python)"

NAMES=(
    siglent_coldboard
    mks902b
    tpg262
    ionivac
    ionivac_pump
    ls455
    ls224_fridge
    ls224_magnet
    flowmeter_pi
    flowmeter_left
    flowmeter_sep
    lhe_fridge
    lhe_magnet
    manometer_low
    safety_interlock
    target_status
    save_restore
    archiver
    alarm_summary
)

START_ORDER=(
    siglent_coldboard
    mks902b
    tpg262
    ionivac
    ionivac_pump
    ls455
    ls224_fridge
    ls224_magnet
    flowmeter_pi
    flowmeter_left
    flowmeter_sep
    lhe_fridge
    lhe_magnet
    manometer_low
    safety_interlock
    target_status
    save_restore
    archiver
    alarm_summary
)

STOP_ORDER=(
    alarm_summary
    archiver
    save_restore
    target_status
    safety_interlock
    manometer_low
    lhe_magnet
    lhe_fridge
    flowmeter_sep
    flowmeter_left
    flowmeter_pi
    ls224_magnet
    ls224_fridge
    ls455
    ionivac_pump
    ionivac
    tpg262
    mks902b
    siglent_coldboard
)


session_name()
{
    local name="$1"

    name="${name//_/-}"

    printf 'genesis-cryo-%s\n' "$name"
}


session_exists()
{
    local session="$1"
    local listing

    listing="$(screen -ls 2>&1 || true)"

    grep -E \
        "\\.${session}[[:space:]]" \
        <<< "$listing" \
        >/dev/null
}


require_physical_host()
{
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "[FAIL] Cryo physical operation is Ubuntu/WSL only." >&2
        echo "       The Mac is the Genesis development/edit host." >&2
        exit 1
    fi
}


require_environment()
{
    if [[ "${CONDA_DEFAULT_ENV:-}" != "genesis-p1" ]]; then
        echo "[FAIL] Activate the Genesis environment first:" >&2
        echo "       conda activate genesis-p1" >&2
        exit 1
    fi
}


ca_list()
{
    "$PYTHON_BIN" - "$MANAGER" <<'PY_CA'
import sys
from pathlib import Path

import yaml


path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text())

ports = []

for name, cfg in data.items():
    if name == "general":
        continue

    if not isinstance(cfg, dict):
        continue

    port = cfg.get("ca_server_port")

    if port is not None:
        ports.append(int(port))

print(
    " ".join(
        f"127.0.0.1:{port}"
        for port in sorted(set(ports))
    )
)
PY_CA
}


profile_fields()
{
    local name="$1"

    "$PYTHON_BIN" - "$MANAGER" "$name" <<'PY_PROFILE'
import sys
from pathlib import Path

import yaml


path = Path(sys.argv[1])
name = sys.argv[2]

data = yaml.safe_load(path.read_text())
cfg = data[name]

print(
    "\t".join(
        (
            str(cfg["settings_folder"]),
            str(cfg.get("ioc", name)),
            str(cfg["ca_server_port"]),
            str(cfg["pva_server_port"]),
        )
    )
)
PY_PROFILE
}


static_preflight()
{
    "$PYTHON_BIN" - "$MANAGER" <<'PY_PREFLIGHT'
import sys
from pathlib import Path

import yaml


manager_path = Path(sys.argv[1])
root = Path.cwd()

manager = yaml.safe_load(
    manager_path.read_text()
)

expected = (
    "siglent_coldboard",
    "mks902b",
    "tpg262",
    "ionivac",
    "ls455",
    "ls224_fridge",
    "ls224_magnet",
    "ionivac_pump",
    "alarm_summary",
    "safety_interlock",
    "target_status",
    "save_restore",
    "archiver",
)

optional = (
    "flowmeter_pi",
    "flowmeter_left",
    "flowmeter_sep",
    "lhe_fridge",
    "lhe_magnet",
    "manometer_low",
)

# Validate all registered Cryo IOCs, including those excluded
# from normal physical startup pending commissioning.
physical = expected[:8] + optional
expected = expected + optional

for name in expected:
    if name not in manager:
        raise SystemExit(
            f"[FAIL] missing Cryo manager entry: {name}"
        )

    cfg = manager[name]

    if cfg.get("autostart") is not False:
        raise SystemExit(
            f"[FAIL] {name} must remain autostart=false"
        )

    settings_file = (
        root
        / cfg["settings_folder"]
        / "settings.yaml"
    )

    if not settings_file.is_file():
        raise SystemExit(
            f"[FAIL] settings missing: {settings_file}"
        )

    child = yaml.safe_load(
        settings_file.read_text()
    )

    ioc = cfg.get(
        "ioc",
        name,
    )

    if ioc not in child:
        raise SystemExit(
            f"[FAIL] IOC {ioc} absent from {settings_file}"
        )

    if name in physical:
        write_enabled = (
            child[ioc]
            .get("write_enabled")
        )

        if write_enabled is not False:
            raise SystemExit(
                f"[FAIL] physical writes enabled in {settings_file}"
            )

ca_ports = [
    manager[name]["ca_server_port"]
    for name in expected
]

pva_ports = [
    manager[name]["pva_server_port"]
    for name in expected
]

if len(ca_ports) != len(set(ca_ports)):
    raise SystemExit(
        "[FAIL] duplicate Cryo CA server port"
    )

if len(pva_ports) != len(set(pva_ports)):
    raise SystemExit(
        "[FAIL] duplicate Cryo PVA server port"
    )

limits = yaml.safe_load(
    (
        root
        / "logic_devices"
        / "limits_unh.yaml"
    ).read_text()
)

enforce_values = []


def collect_enforce(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "enforce":
                enforce_values.append(child)

            collect_enforce(child)

    elif isinstance(value, list):
        for child in value:
            collect_enforce(child)


collect_enforce(limits)

if not enforce_values:
    raise SystemExit(
        "[FAIL] limits_unh.yaml has no enforce setting"
    )

if any(
    value is not False
    for value in enforce_values
):
    raise SystemExit(
        "[FAIL] placeholder safety-limit enforcement "
        "must remain disabled"
    )

print(
    "[PASS] Cryo deployment manifest complete"
)
print(
    "[PASS] physical profiles remain write_enabled=false"
)
print(
    "[PASS] Cryo CA/PVA ports unique"
)
print(
    "[PASS] placeholder safety limits remain report-only"
)
PY_PREFLIGHT
}


stack_state()
{
    local count=0
    local name
    local session

    for name in "${NAMES[@]}"; do
        session="$(session_name "$name")"

        if session_exists "$session"; then
            count=$((count + 1))
        fi
    done

    if [[ "$count" -eq 0 ]]; then
        printf 'ZERO\n'
        return
    fi

    if [[ "$count" -eq "${#NAMES[@]}" ]]; then
        printf 'COMPLETE\n'
        return
    fi

    printf 'PARTIAL\n'
}


legacy_owner_guard()
{
    local found=""

    found="$(
        {
            pgrep -af \
                '[m]aster_ioc.py.*configs/target_physical/' \
                || true

            pgrep -af \
                '[i]oc_manager.py.*configs/target_physical/' \
                || true
        }
    )"

    if [[ -n "$found" ]]; then
        echo "[FAIL] existing non-lifecycle Cryo owner detected." >&2
        echo "       Refusing to create duplicate hardware owners." >&2
        echo "$found" >&2
        exit 1
    fi
}


start_one()
{
    local name="$1"
    local session
    local settings
    local ioc
    local ca_port
    local pva_port
    local logfile
    local addresses

    session="$(session_name "$name")"

    if session_exists "$session"; then
        echo "[FAIL] session already exists: $session" >&2
        return 1
    fi

    IFS=$'\t' read -r \
        settings \
        ioc \
        ca_port \
        pva_port \
        < <(
            profile_fields "$name"
        )

    if ss -ltn | awk -v p=":$ca_port"         '$4 ~ (p "$") {found=1} END {exit !found}'; then
        echo "[FAIL] CA port $ca_port already occupied for $name" >&2
        return 1
    fi

    logfile="$ROOT/logs/${session}.log"
    addresses="$(ca_list)"

    mkdir -p "$ROOT/logs"

    screen \
        -dmS "$session" \
        bash -lc \
        "cd '$ROOT' && \
env \
EPICS_CAS_SERVER_PORT='$ca_port' \
EPICS_PVA_SERVER_PORT='$pva_port' \
EPICS_CA_AUTO_ADDR_LIST=NO \
EPICS_CA_ADDR_LIST='$addresses' \
EPICS_CA_MAX_ARRAY_BYTES=262144 \
PYTHONPATH='$ROOT' \
'$PYTHON_BIN' -u master_ioc.py \
-s '$settings' \
-i '$ioc' \
>> '$logfile' 2>&1"

    sleep 1

    if ! session_exists "$session"; then
        echo "[FAIL] $session exited during startup" >&2
        echo "===== LOG =====" >&2
        tail -120 "$logfile" >&2 || true
        echo >&2
        echo "[INFO] Already-started Cryo owners were retained." >&2
        echo "       Inspect with: genesis cryo-status" >&2
        return 1
    fi

    echo "[PASS] $session"
}


verify_epics_surface()
{
    local addresses

    addresses="$(ca_list)"

    env \
        EPICS_CA_AUTO_ADDR_LIST=NO \
        EPICS_CA_ADDR_LIST="$addresses" \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        "$PYTHON_BIN" - <<'PY_VERIFY'
import asyncio

from aioca import caget


WRITE_GATES = (
    "UNH:GEN:CRYO:COLDBOARD:WRITE_ENABLED",
    "UNH:GEN:CRYO:CELL:WRITE_ENABLED",
    "UNH:GEN:CRYO:VAC:WRITE_ENABLED",
    "UNH:GEN:CRYO:ION:WRITE_ENABLED",
    "UNH:GEN:CRYO:ION:PUMP:WRITE_ENABLED",
    "UNH:GEN:CRYO:FIELD:WRITE_ENABLED",
    "UNH:GEN:CRYO:FRIDGE:WRITE_ENABLED",
    "UNH:GEN:CRYO:MAG:WRITE_ENABLED",
)

STATUS_PVS = (
    "UNH:GEN:CRYO:SAFE:Safe_Permit",
    "UNH:GEN:CRYO:SAFE:Safe_Fault",
    "UNH:GEN:CRYO:STATE:production",
    "UNH:GEN:CRYO:SAVE:SR_Status",
    "UNH:GEN:CRYO:ARCH:Archive_Status",
    "UNH:GEN:CRYO:ALARM:Alarm_Count",
)


async def read_with_retry(pv):
    last_error = None

    for _ in range(25):
        try:
            return await caget(
                pv,
                timeout=2.0,
            )

        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.4)

    raise RuntimeError(
        f"{pv} unavailable: {last_error}"
    )


def is_enabled(value):
    try:
        return int(value) != 0

    except Exception:
        return (
            str(value).strip().lower()
            not in (
                "0",
                "false",
                "off",
                "disabled",
            )
        )


async def main():
    for pv in WRITE_GATES:
        value = await read_with_retry(
            pv
        )

        print(
            f"{pv:<52} = {value}"
        )

        if is_enabled(value):
            raise RuntimeError(
                "physical write gate unexpectedly enabled: "
                + pv
            )

    print(
        "[PASS] all physical Genesis write gates disabled"
    )

    for pv in STATUS_PVS:
        try:
            value = await read_with_retry(
                pv
            )

            print(
                f"{pv:<52} = {value}"
            )

        except Exception as exc:
            print(
                f"[WARN] {pv}: {exc}"
            )


asyncio.run(main())
PY_VERIFY
}


enable_archiver()
{
    local addresses

    addresses="$(ca_list)"

    env \
        EPICS_CA_AUTO_ADDR_LIST=NO \
        EPICS_CA_ADDR_LIST="$addresses" \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        "$PYTHON_BIN" - <<'PY_ARCHIVE'
import asyncio

from aioca import caget, caput


async def main():
    enable = (
        "UNH:GEN:CRYO:ARCH:Archive_Enable"
    )

    status = (
        "UNH:GEN:CRYO:ARCH:Archive_Status"
    )

    count = (
        "UNH:GEN:CRYO:ARCH:Archive_PV_Count"
    )

    await caput(
        enable,
        1,
        wait=True,
        timeout=3,
    )

    for _ in range(30):
        try:
            current = int(
                await caget(
                    status,
                    timeout=2,
                )
            )

            n_pvs = int(
                await caget(
                    count,
                    timeout=2,
                )
            )

            if current == 1 and n_pvs > 0:
                print(
                    f"[PASS] Cryo archiver active "
                    f"with {n_pvs} PVs"
                )
                return

        except Exception:
            pass

        await asyncio.sleep(0.25)

    raise SystemExit(
        "[FAIL] Cryo archiver did not become active"
    )


asyncio.run(main())
PY_ARCHIVE
}


show_status()
{
    require_environment

    local name
    local session
    local addresses

    echo "============================================================"
    echo " GENESIS CRYOGENIC TARGET STATUS"
    echo "============================================================"

    echo
    echo "===== SOFTWARE OWNERS ====="

    for name in "${NAMES[@]}"; do
        session="$(session_name "$name")"

        if session_exists "$session"; then
            printf '[UP]   %s\n' "$session"
        else
            printf '[DOWN] %s\n' "$session"
        fi
    done

    echo
    echo "===== RASPBERRY PI CRYO BRIDGES ====="

    if ! pi_bridge_control status; then
        echo "[WARN] Raspberry Pi bridge status unavailable." >&2
    fi

    echo
    echo "===== EPICS STATUS ====="

    addresses="$(ca_list)"

    env \
        EPICS_CA_AUTO_ADDR_LIST=NO \
        EPICS_CA_ADDR_LIST="$addresses" \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        "$PYTHON_BIN" - <<'PY_STATUS'
import asyncio

from aioca import caget


PVS = (
    "UNH:GEN:CRYO:COLDBOARD:COMM",
    "UNH:GEN:CRYO:CELL:COMM",
    "UNH:GEN:CRYO:VAC:COMM",
    "UNH:GEN:CRYO:ION:COMM",
    "UNH:GEN:CRYO:ION:PUMP:COMM",
    "UNH:GEN:CRYO:FIELD:COMM",
    "UNH:GEN:CRYO:FRIDGE:COMM",
    "UNH:GEN:CRYO:MAG:COMM",
    "UNH:GEN:CRYO:FLOW:LEFT:LPump_SLM",
    "UNH:GEN:CRYO:FLOW:LEFT:LPump_Status",
    "UNH:GEN:CRYO:FLOW:SEP:SepFlow_SLM",
    "UNH:GEN:CRYO:FLOW:SEP:SepFlow_Status",

    "UNH:GEN:CRYO:FLOW:RIGHT:COMM",
    "UNH:GEN:CRYO:FLOW:RIGHT:RPump_Raw",
    "UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM",
    "UNH:GEN:CRYO:FLOW:RIGHT:RPump_Status",

    "UNH:GEN:CRYO:VAC:LOW:COMM",
    "UNH:GEN:CRYO:VAC:LOW:Mano_PI",
    "UNH:GEN:CRYO:VAC:LOW:Mano_Status",

    "UNH:GEN:CRYO:SAFE:Safe_Permit",
    "UNH:GEN:CRYO:SAFE:Safe_Fault",
    "UNH:GEN:CRYO:SAFE:Safe_Reason",

    "UNH:GEN:CRYO:STATE:production",
    "UNH:GEN:CRYO:STATE:material",

    "UNH:GEN:CRYO:ALARM:Alarm_Count",
    "UNH:GEN:CRYO:ALARM:Alarm_Worst",
    "UNH:GEN:CRYO:ALARM:Alarm_Missing",

    "UNH:GEN:CRYO:ARCH:Archive_Status",
    "UNH:GEN:CRYO:ARCH:Archive_PV_Count",
    "UNH:GEN:CRYO:ARCH:Archive_Writes",
)


async def main():
    for pv in PVS:
        try:
            value = await caget(
                pv,
                timeout=1.5,
            )

            print(
                f"{pv:<52} = {value}"
            )

        except Exception:
            print(
                f"{pv:<52} = UNAVAILABLE"
            )


asyncio.run(main())
PY_STATUS
}


coldboard_safe_to_release()
{
    local session
    local addresses

    session="$(
        session_name siglent_coldboard
    )"

    if ! session_exists "$session"; then
        return 0
    fi

    addresses="$(ca_list)"

    env \
        EPICS_CA_AUTO_ADDR_LIST=NO \
        EPICS_CA_ADDR_LIST="$addresses" \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        "$PYTHON_BIN" - <<'PY_COLD'
import asyncio

from aioca import caget


def is_enabled(value):
    try:
        return int(value) != 0

    except Exception:
        return (
            str(value).strip().lower()
            not in (
                "0",
                "false",
                "off",
                "disabled",
            )
        )


async def main():
    pv = (
        "UNH:GEN:CRYO:COLDBOARD:"
        "Coldboard_Mode"
    )

    try:
        value = await caget(
            pv,
            timeout=3,
        )

    except Exception as exc:
        raise SystemExit(
            "[FAIL] Cannot confirm cold-board output state; "
            "retaining IOC owners for safety. "
            f"Error: {exc}"
        )

    print(
        f"{pv} = {value}"
    )

    if is_enabled(value):
        raise SystemExit(
            "[FAIL] Cold-board output is ON. "
            "Refusing to abandon its software owner. "
            "Place the system in the intended safe state first."
        )

    print(
        "[PASS] cold-board output is OFF"
    )


asyncio.run(main())
PY_COLD
}


disable_archiver()
{
    local session
    local addresses

    session="$(
        session_name archiver
    )"

    if ! session_exists "$session"; then
        return 0
    fi

    addresses="$(ca_list)"

    env \
        EPICS_CA_AUTO_ADDR_LIST=NO \
        EPICS_CA_ADDR_LIST="$addresses" \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        "$PYTHON_BIN" - <<'PY_ARCHIVE_STOP'
import asyncio

from aioca import caput


async def main():
    try:
        await caput(
            "UNH:GEN:CRYO:ARCH:Archive_Enable",
            0,
            wait=True,
            timeout=3,
        )

        await asyncio.sleep(0.5)

        print(
            "[PASS] Cryo archiver disabled before shutdown"
        )

    except Exception as exc:
        raise SystemExit(
            "[FAIL] could not disable Cryo archiver: "
            f"{exc}"
        )


asyncio.run(main())
PY_ARCHIVE_STOP
}


close_phoebus()
{
    "$PYTHON_BIN" \
        - "$PHOEBUS_SETTINGS" "$PHOEBUS_DISPLAY" \
        <<'PY_PHOEBUS'
import asyncio
import sys
from pathlib import Path

from tools.phoebus_shutdown import close_instance


async def main():
    settings = Path(
        sys.argv[1]
    ).resolve()

    display = Path(
        sys.argv[2]
    ).resolve()

    try:
        await close_instance(
            settings,
            display,
        )

        print(
            "[PASS] Cryo Phoebus close request complete"
        )

    except Exception as exc:
        print(
            "[WARN] Cryo Phoebus close request failed:",
            exc,
        )


asyncio.run(main())
PY_PHOEBUS
}


pi_bridge_control()
{
    local operation="$1"
    "$ROOT/scripts/pi_cryo_bridges/ubuntu_control.sh" "$operation"
}

start_stack()
{
    require_physical_host
    require_environment

    echo "============================================================"
    echo " GENESIS CRYOGENIC TARGET START"
    echo "============================================================"

    static_preflight

    echo
    echo "===== ENSURE GENESIS CRYO PI BRIDGES ====="

    local bridge_failed=0

    if ! pi_bridge_control start; then
        echo "[WARN] Pi bridge startup failed; continuing all Cryo IOC startup attempts." >&2
        bridge_failed=1
    fi

    local state

    state="$(stack_state)"

    case "$state" in
        COMPLETE)
            echo
            echo "[OK] complete Cryo stack already running."
            echo "     Existing owners were not touched."
            return "$bridge_failed"
            ;;

        PARTIAL)
            echo
            echo "[INFO] Partial Cryo stack detected."
            echo "       Starting missing IOC owners only."
            ;;

        ZERO)
            legacy_owner_guard
            ;;

        *)
            echo "[FAIL] unknown Cryo stack state: $state" >&2
            return 1
            ;;
    esac

    echo
    echo "===== START CRYO IOC OWNERS ====="

    local name

    local failures=0
    local missing=0
    local session

    for name in "${START_ORDER[@]}"; do
        session="$(session_name "$name")"

        if session_exists "$session"; then
            echo "[UP] $session"
            continue
        fi

        if ! start_one "$name"; then
            echo "[FAIL] Could not start $session" >&2
            ((failures += 1))
        fi
    done

    echo
    echo "===== VERIFY ALL CRYO IOC OWNERS ====="

    for name in "${NAMES[@]}"; do
        session="$(session_name "$name")"
        if ! session_exists "$session"; then
            echo "[MISSING] $session" >&2
            ((missing += 1))
        fi
    done

    if (( failures > 0 || missing > 0 )); then
        echo "[FAIL] Cryo startup incomplete:" >&2
        echo "       launch failures=$failures, missing=$missing" >&2
        return 1
    fi

    echo "[PASS] All ${#NAMES[@]} Cryo IOCs running."

    echo
    echo "===== VERIFY READ-ONLY PHYSICAL BASELINE ====="

    verify_epics_surface

    echo
    echo "===== ENABLE DATA ARCHIVING ====="

    enable_archiver

    echo
    echo "============================================================"
    echo " CRYO STACK READY"
    echo "============================================================"
    echo " Physical hardware writes remain disabled."
    echo " Placeholder safety limits remain report-only."
    echo
    echo " Operator GUI:"
    echo "   genesis cryo-phoebus"
    echo
    echo " Historical viewer:"
    echo "   genesis cryo-viewer"
    if (( bridge_failed != 0 )); then
        echo "[WARN] IOC startup completed, but Pi bridge integration needs attention." >&2
        return 1
    fi

}


stop_stack()
{
    require_physical_host
    require_environment

    echo "============================================================"
    echo " GENESIS CRYOGENIC TARGET STOP"
    echo "============================================================"

    echo
    echo "===== VERIFY COLD-BOARD OWNER MAY BE RELEASED ====="

    coldboard_safe_to_release

    echo
    echo "===== STOP ARCHIVE WRITES CLEANLY ====="

    disable_archiver

    echo
    echo "===== CLOSE CRYO OPERATOR GUI ====="

    close_phoebus

    echo
    echo "===== STOP CRYO IOC OWNERS ====="

    local name
    local session

    for name in "${STOP_ORDER[@]}"; do
        session="$(session_name "$name")"

        if session_exists "$session"; then
            screen \
                -S "$session" \
                -X quit \
                >/dev/null 2>&1 \
                || true

            echo "[STOP] $session"
        fi
    done

    sleep 1

    local remaining=0

    for name in "${NAMES[@]}"; do
        session="$(session_name "$name")"

        if session_exists "$session"; then
            echo "[FAIL] still running: $session" >&2
            remaining=1
        fi
    done

    if [[ "$remaining" -ne 0 ]]; then
        return 1
    fi

    echo
    echo "===== STOP GENESIS CRYO PI BRIDGES ====="

    if ! pi_bridge_control stop; then
        echo "[WARN] Local IOCs stopped, but Pi bridge shutdown needs attention." >&2
        return 1
    fi

    echo "[PASS] Cryo software stack stopped."
    echo "       Shutdown issued no hardware ON/OFF command."
}


launch_phoebus()
{
    require_environment

    exec "$ROOT/scripts/start_cryo_phoebus.sh"
}


launch_viewer()
{
    require_environment

    exec "$ROOT/archive/start_archive_viewer.sh"
}


usage()
{
    printf '%s\n' \
        "Usage:" \
        "  cryo_physical.sh start" \
        "  cryo_physical.sh status" \
        "  cryo_physical.sh stop" \
        "  cryo_physical.sh phoebus" \
        "  cryo_physical.sh viewer"
}


case "${1:-}" in
    start)
        start_stack
        ;;

    status)
        show_status
        ;;

    stop)
        stop_stack
        ;;

    phoebus)
        launch_phoebus
        ;;

    viewer)
        launch_viewer
        ;;

    *)
        usage
        exit 2
        ;;
esac
