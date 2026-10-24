#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${ROOT}/configs/commissioning/microwave_physical"
MANAGER_CA="127.0.0.1:5094"
ALL_CA="127.0.0.1:5094 127.0.0.1:5066 127.0.0.1:5067 127.0.0.1:5069 127.0.0.1:5085 127.0.0.1:5087"

die() {
    echo "[STOP] $*" >&2
    exit 1
}

require_env() {
    [[ "${CONDA_DEFAULT_ENV:-}" == "genesis-p1" ]] \
        || die "activate conda environment genesis-p1 first"
}

screen_exists() {
    local listing
    listing="$(screen -ls 2>&1 || true)"
    grep -Eq "[.]${1}[[:space:]]" <<< "$listing"
}

manager_put() {
    local name="$1"
    local value="$2"

    EPICS_CA_ADDR_LIST="${MANAGER_CA}" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - "$name" "$value" <<'PY'
import asyncio
import sys
from aioca import caput, caget

name = sys.argv[1]
value = int(sys.argv[2])
pv = f"UNH:GEN:MAN:{name}_control"

async def main():
    await caput(pv, value, wait=True, timeout=3)
    await asyncio.sleep(4 if value == 1 else 2)
    print(pv, "=", await caget(pv, timeout=3))

asyncio.run(main())
PY
}

start_manager() {
    if screen_exists "ioc-manager"; then
        die "an IOC Manager is already running; use '$0 stop' first"
    fi

    screen -dmS ioc-manager bash -lc \
        "cd '$ROOT' && \
         exec env \
         EPICS_CAS_SERVER_PORT=5094 \
         EPICS_PVA_SERVER_PORT=5075 \
         '$CONDA_PREFIX/bin/python' ioc_manager.py \
         -s '$PROFILE/ioc_manager'"

    sleep 3

    EPICS_CA_ADDR_LIST="${MANAGER_CA}" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
from aioca import caget

async def main():
    await caget(
        "UNH:GEN:MAN:mw_spd1305x_control",
        timeout=5,
    )
    print("[PASS] physical IOC Manager online")

asyncio.run(main())
PY
}

set_uca_safe() {
    EPICS_CA_ADDR_LIST="127.0.0.1:5066" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
import sys
from aioca import caget, caput

P = "UNH:GEN:MW:PS"

async def main():
    if int(await caget(f"{P}:COMM", timeout=4)) != 1:
        sys.exit("[STOP] UCA IOC is not connected")

    await caput(
        f"{P}:VSET_CMD",
        5.0,
        wait=True,
        timeout=3,
    )

    await caput(
        f"{P}:ISET_CMD",
        0.05,
        wait=True,
        timeout=3,
    )

    await caput(
        f"{P}:OVP_CMD",
        5.1,
        wait=True,
        timeout=3,
    )

    await caput(
        f"{P}:OCP_CMD",
        0.11,
        wait=True,
        timeout=3,
    )

    await caput(
        f"{P}:OUTPUT_CMD",
        1,
        wait=True,
        timeout=3,
    )

    await asyncio.sleep(1.5)

    v = float(
        await caget(
            f"{P}:VMEAS",
            timeout=3,
        )
    )

    output = int(
        await caget(
            f"{P}:OUTPUT_RBV",
            timeout=3,
        )
    )

    print("UCA VMEAS  =", v)
    print("UCA OUTPUT =", output)

    if output != 1 or not 4.9 <= v <= 5.1:
        sys.exit(
            "[STOP] UCA failed safe 5 V state"
        )

    print("[PASS] UCA at safe 5 V state")

asyncio.run(main())
PY
}

verify_vaunix_deployment() {
    local expected deployed

    expected="$(sha256sum \
        "$ROOT/tools/vaunix_shim/vaunix_shim.py")" \
        || die "Cannot hash Genesis Vaunix source"
    expected="${expected%% *}"

    deployed="$(ssh -o ConnectTimeout=8 \
        "unhpoltarg@${MW_PI_HOST:-192.168.1.23}" '
        set -eu
        F=/home/unhpoltarg/vaunix_shim/vaunix_shim.py
        S=vaunix-shim.service

        test "$(systemctl --user is-active "$S")" = active

        PID=$(systemctl --user show -P MainPID "$S")
        test "$PID" -gt 0

        tr "\000" "\n" < "/proc/$PID/cmdline" |
            grep -Fxq "$F"

        START=$(systemctl --user show -P ExecMainStartTimestamp "$S")
        test "$(date -d "$START" +%s)" -ge "$(stat -c %Y "$F")"

        sha256sum "$F" | cut -d " " -f1
    ')" || die "Vaunix Pi service/version verification failed"

    [[ "$expected" == "$deployed" ]] \
        || die "Vaunix Pi shim differs from Genesis repository"

    echo "[PASS] Vaunix deployed and running version verified: ${expected:0:12}"
}

check_vaunix_service() {
    ssh unhpoltarg@"${MW_PI_HOST:-192.168.1.23}" \
        'systemctl --user is-active --quiet vaunix-shim.service &&
         systemctl --user --no-pager --full status vaunix-shim.service |
         sed -n "1,18p"'

    python - <<'PY'
import socket
import sys
import os

def ask(command):
    with socket.create_connection(
        (os.environ.get("MW_PI_HOST", "192.168.1.23"), 1701),
        timeout=4,
    ) as s:
        f = s.makefile("rb")
        s.sendall((command + "\n").encode())
        return f.readline(4096).decode().strip()

identity = ask("*IDN?")
state = ask("READ?")

print("*IDN? ->", identity)
print("READ? ->", state)

if not identity.endswith(",HARDWARE"):
    sys.exit(
        "[STOP] Vaunix shim is not in HARDWARE mode"
    )

fields = state.split()

if len(fields) != 5:
    sys.exit("[STOP] invalid Vaunix READ? response")

if int(fields[2]) != 0:
    sys.exit("[STOP] Vaunix RF is not OFF")

print(
    "[PASS] Vaunix hardware service online "
    "with RF OFF"
)
PY
}

prepare_source() {
    EPICS_CA_ADDR_LIST="127.0.0.1:5069 127.0.0.1:5085" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
import sys
import time
from aioca import caget, caput

S = "UNH:GEN:MW:SRC"
P = "UNH:GEN:MW:SUP"

async def main():
    requested = time.time()
    await caput(f"{P}:RFEnable", 0, wait=True, timeout=3)
    await caput(f"{S}:MW_RF_CMD", 0, wait=True, timeout=3)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        done = float(await caget(f"{S}:MW_RF_DONE_TIME", timeout=3))
        valid = int(await caget(f"{S}:MW_RF_VALID", timeout=3))
        rf = int(await caget(f"{S}:MW_RF_RBV", timeout=3))
        ready = int(await caget(f"{S}:MW_RF_READY", timeout=3))
        if done >= requested and valid == 1 and rf == 0 and ready == 1:
            break
        await asyncio.sleep(0.1)
    else:
        sys.exit("[STOP] fresh vendor RF OFF/sweep-stop confirmation unavailable")

    await caput(
        f"{S}:MW_FC",
        11.666666667,
        wait=True,
        timeout=3,
    )

    await caput(
        f"{S}:MW_PWRC",
        10.0,
        wait=True,
        timeout=3,
    )

    await asyncio.sleep(1)

    output = float(
        await caget(
            f"{S}:MW_OutFI",
            timeout=3,
        )
    )

    power = float(
        await caget(
            f"{S}:MW_PWRI",
            timeout=3,
        )
    )

    rf = int(
        await caget(
            f"{S}:MW_RF_RBV",
            timeout=3,
        )
    )

    if not 139.9 <= output <= 140.1:
        sys.exit(
            "[STOP] source is not at 140 GHz"
        )

    if abs(power - 10.0) > 0.26:
        sys.exit(
            "[STOP] source power is not +10 dBm"
        )

    if rf != 0:
        sys.exit(
            "[STOP] RF unexpectedly ON"
        )

    await caput(
        f"{P}:OutputFrequency",
        140.0,
        wait=True,
        timeout=3,
    )

    await asyncio.sleep(0.5)

    print(
        "Output frequency =",
        await caget(
            f"{S}:MW_OutFI",
            timeout=3,
        ),
        "GHz",
    )

    print(
        "Source power     =",
        await caget(
            f"{S}:MW_PWRI",
            timeout=3,
        ),
        "dBm",
    )

    print(
        "RF               =",
        await caget(
            f"{S}:MW_RF_RBV",
            timeout=3,
        ),
    )

    print(
        "Supervisor cmd   =",
        await caget(
            f"{P}:OutputFrequency",
            timeout=3,
        ),
    )

    print(
        "[PASS] source prepared at "
        "140 GHz / +10 dBm / RF OFF"
    )

asyncio.run(main())
PY
}

enable_archiver() {
    EPICS_CA_ADDR_LIST="127.0.0.1:5067" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
import sys
from aioca import caput, caget

async def main():
    await caput(
        "UNH:GEN:Archive_Enable",
        1,
        wait=True,
        timeout=3,
    )

    await asyncio.sleep(5)

    status = int(
        await caget(
            "UNH:GEN:Archive_Status",
            timeout=3,
        )
    )

    count = int(
        await caget(
            "UNH:GEN:Archive_PV_Count",
            timeout=3,
        )
    )

    print("Archive_Status   =", status)
    print("Archive_PV_Count =", count)

    if status != 1 or count <= 0:
        sys.exit(
            "[STOP] archiver is not monitoring "
            "the physical stack"
        )

    print("[PASS] archiver running")

asyncio.run(main())
PY
}

status_stack() {
    EPICS_CA_ADDR_LIST="${ALL_CA}" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
import math
import time
from aioca import caget

PVS = [
    ("UCA COMM", "UNH:GEN:MW:PS:COMM"),
    ("UCA V", "UNH:GEN:MW:PS:VMEAS"),
    ("UCA OUT", "UNH:GEN:MW:PS:OUTPUT_RBV"),
    (
        "Source connected",
        "UNH:GEN:MW:SRC:MW_Connected",
    ),
    (
        "Output GHz",
        "UNH:GEN:MW:SRC:MW_OutFI",
    ),
    (
        "Power dBm",
        "UNH:GEN:MW:SRC:MW_PWRI",
    ),
    ("RF", "UNH:GEN:MW:SRC:MW_RF_RBV"),
    ("RF valid", "UNH:GEN:MW:SRC:MW_RF_VALID"),
    ("RF ready", "UNH:GEN:MW:SRC:MW_RF_READY"),
    ("RF state", "UNH:GEN:MW:SUP:RFState"),
    ("Control owned", "UNH:GEN:MW:SRC:MW_CONTROL_OWNED"),
    ("PLL", "UNH:GEN:MW:SRC:MW_Lock"),
    (
        "Robot COMM",
        "UNH:GEN:MW:ROBOT:COMM_STATUS",
    ),
    (
        "Robot mode",
        "UNH:GEN:MW:ROBOT:CONTROL_MODE",
    ),
    ("X busy", "UNH:GEN:MW:ROBOT:X_BUSY"),
    ("Y busy", "UNH:GEN:MW:ROBOT:Y_BUSY"),
    (
        "System connected",
        "UNH:GEN:MW:SUP:SystemConnected",
    ),
    (
        "RF permissive",
        "UNH:GEN:MW:SUP:RFPermissive",
    ),
    (
        "Supervisor RF",
        "UNH:GEN:MW:SUP:RFEnable",
    ),
    (
        "Archive status",
        "UNH:GEN:Archive_Status",
    ),
    (
        "Archive PVs",
        "UNH:GEN:Archive_PV_Count",
    ),
]

async def main():
    for label, pv in PVS:
        try:
            value = await caget(
                pv,
                timeout=3,
            )
            if label == "RF valid" and int(value) == 1:
                for stamp in ("UNH:GEN:MW:SRC:MW_RF_TIME", "UNH:GEN:MW:SRC:MAN:vaunix_lms_time"):
                    age = time.time() - float(await caget(stamp, timeout=3))
                    if not math.isfinite(age) or not 0 <= age <= 4.0:
                        value = 0
        except Exception as exc:
            value = f"ERROR: {exc}"

        print(
            f"{label:20s} = {value}"
        )

asyncio.run(main())
PY
}

rf_ready() {
    verify_vaunix_deployment
    EPICS_CA_ADDR_LIST="${ALL_CA}" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python - <<'PY'
import asyncio
import sys
from aioca import caget

async def get(pv):
    return await caget(
        pv,
        timeout=3,
    )

async def main():
    uca_comm = int(
        await get(
            "UNH:GEN:MW:PS:COMM"
        )
    )

    uca_v = float(
        await get(
            "UNH:GEN:MW:PS:VMEAS"
        )
    )

    uca_out = int(
        await get(
            "UNH:GEN:MW:PS:OUTPUT_RBV"
        )
    )

    src_conn = int(
        await get(
            "UNH:GEN:MW:SRC:MW_Connected"
        )
    )

    output = float(
        await get(
            "UNH:GEN:MW:SRC:MW_OutFI"
        )
    )

    power = float(
        await get(
            "UNH:GEN:MW:SRC:MW_PWRI"
        )
    )

    rf = int(
        await get(
            "UNH:GEN:MW:SRC:MW_RF_RBV"
        )
    )

    lock = int(
        await get(
            "UNH:GEN:MW:SRC:MW_Lock"
        )
    )

    test_mode = int(
        await get(
            "UNH:GEN:MW:SRC:MW_TestMode"
        )
    )

    robot_mode = int(
        await get(
            "UNH:GEN:MW:ROBOT:CONTROL_MODE"
        )
    )

    x_busy = int(
        await get(
            "UNH:GEN:MW:ROBOT:X_BUSY"
        )
    )

    y_busy = int(
        await get(
            "UNH:GEN:MW:ROBOT:Y_BUSY"
        )
    )

    system_connected = int(
        await get(
            "UNH:GEN:MW:SUP:SystemConnected"
        )
    )

    permit = int(
        await get(
            "UNH:GEN:MW:SUP:RFPermissive"
        )
    )

    supervisor_rf = int(
        await get(
            "UNH:GEN:MW:SUP:RFEnable"
        )
    )

    archive = int(
        await get(
            "UNH:GEN:Archive_Status"
        )
    )

    count = int(
        await get(
            "UNH:GEN:Archive_PV_Count"
        )
    )

    rf_valid = int(await get("UNH:GEN:MW:SRC:MW_RF_VALID"))
    owned = int(await get("UNH:GEN:MW:SRC:MW_CONTROL_OWNED"))
    ready = int(await get("UNH:GEN:MW:SRC:MW_RF_READY"))
    checks = [
        (rf_valid == 1 and owned == 1 and ready == 1, "Vendor RF valid and exclusive control owned"),
        (
            uca_comm == 1,
            "UCA connected",
        ),
        (
            uca_out == 1
            and 4.9 <= uca_v <= 5.1,
            "UCA approximately 5 V and ON",
        ),
        (
            src_conn == 1,
            "Vaunix connected",
        ),
        (
            test_mode == 0,
            "Vaunix hardware mode",
        ),
        (
            138.0 <= output <= 142.0,
            "frequency within 138-142 GHz",
        ),
        (
            -40.0 <= power <= 10.0,
            "source power within configured -40..+10 dBm",
        ),
        (
            rf == 0
            and supervisor_rf == 0,
            "RF OFF",
        ),
        (
            lock == 1,
            "PLL locked",
        ),
        (
            robot_mode == 0
            and x_busy == 0
            and y_busy == 0,
            "robot Disabled and idle",
        ),
        (
            system_connected == 1
            and permit == 1,
            "Supervisor connected and "
            "RF permitted",
        ),
        (
            archive == 1
            and count > 0,
            "archiver active",
        ),
    ]

    failed = False

    for ok, label in checks:
        print(
            "[PASS]" if ok else "[FAIL]",
            label,
        )
        failed |= not ok

    if failed:
        sys.exit(
            "[STOP] RF readiness check failed"
        )

    print()
    print(
        "MANUAL CONFIRMATIONS STILL REQUIRED:"
    )
    print(
        "  1. VDI-175T green light ON."
    )
    print(
        "  2. SIG readout visible."
    )
    print(
        "  3. Aluminum/foil reflector available."
    )
    print(
        "  4. Keep reflected power <10%."
    )
    print(
        "  5. UCA 5 V before RF enable "
        "or robot movement."
    )
    print(
        "  6. For initial SIG check, begin "
        "near 4 V and do not go below 3.5 V."
    )
    print()
    print(
        "[READY] software/control chain ready "
        "for final RF/SIG test"
    )

asyncio.run(main())
PY
}

start_stack() {
    require_env
    verify_vaunix_deployment

    [[ -d "$PROFILE/ioc_manager" ]] \
        || die \
        "physical profile not found: $PROFILE"

    echo \
        "===== START PHYSICAL MICROWAVE STACK ====="

    start_manager

    echo
    echo "===== START UCA FIRST ====="

    manager_put mw_spd1305x 1
    set_uca_safe

    echo
    echo "The apparatus procedure requires:"
    echo "  - VDI-175T green light ON"
    echo "  - UCA already at 5 V"
    echo "  - USB connected only after the above state"
    echo

    read -r -p \
        "Type READY after the physical USB/VDI sequence is satisfied: " \
        answer

    [[ "$answer" == "READY" ]] \
        || die \
        "physical confirmation not supplied"

    echo
    echo "===== VAUNIX HARDWARE SERVICE ====="

    check_vaunix_service

    echo
    echo \
        "===== START VAUNIX / ROBOT / SUPERVISOR ====="

    manager_put mw_vaunix_lms 1
    manager_put mw_pcl602 1
    manager_put mw_supervisor 1

    echo
    echo \
        "===== PREPARE SOURCE WITH RF OFF ====="

    prepare_source

    echo
    echo "===== START ARCHIVER ====="

    manager_put archiver 1
    enable_archiver

    echo
    echo "===== FINAL STATUS ====="

    status_stack

    echo
    rf_ready

    echo "[INFO] Microwave hardware/control stack is ready."
    echo "[INFO] GUI is independent; launch when wanted with:"
    echo "       ./scripts/start_microwave_phoebus.sh"
}

stop_stack() {
    require_env

    echo \
        "===== SAFE SOFTWARE SHUTDOWN ====="

    EPICS_CA_ADDR_LIST="${ALL_CA}" \
    EPICS_CA_AUTO_ADDR_LIST=NO \
    python "$ROOT/tools/microwave_shutdown.py"

    echo
    echo "Software stack stopped."
    echo \
        "Complete the physical shutdown in this order:"
    echo "  1. Confirm UCA = 5 V."
    echo "  2. Confirm LabBrick RF OFF."
    echo "  3. Unplug USB at the green box."
    echo "  4. Turn OFF VDI-175T."
    echo \
        "  5. Replace USB-input sticker if required."
    echo "  6. Turn OFF UCA power supply."
}

case "${1:-}" in
    start)
        start_stack
        ;;

    status)
        require_env
        status_stack
        ;;

    rf-ready)
        require_env
        rf_ready
        ;;

    stop)
        stop_stack
        ;;

    *)
        echo "Usage:"
        echo "  $0 start"
        echo "  $0 status"
        echo "  $0 rf-ready"
        echo "  $0 stop"
        exit 2
        ;;
esac
