#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! python -m logic_devices.ssrf_lifecycle --held; then
    exec python -m logic_devices.ssrf_lifecycle "$ROOT/scripts/start_ssrf_physical.sh" "$@"
fi

# Analysis-mode choice; never arms hardware or starts a burn.
GENESIS_SSRF_BURN_MODE=fit
GENESIS_SSRF_OPERATOR_MATERIAL=Butanol
GENESIS_SSRF_OPERATOR_POLARIZATION=0.30
GENESIS_SSRF_OPERATOR_CENTER_MHZ=""
while (( $# )); do
    case "$1" in
        --burn-mode) GENESIS_SSRF_BURN_MODE="${2:?missing mode}"; shift 2 ;;
        --material) GENESIS_SSRF_OPERATOR_MATERIAL="${2:?missing material}"; shift 2 ;;
        --polarization) GENESIS_SSRF_OPERATOR_POLARIZATION="${2:?missing polarization}"; shift 2 ;;
        --center-mhz) GENESIS_SSRF_OPERATOR_CENTER_MHZ="${2:?missing center}"; shift 2 ;;
        *) echo "[FAIL] Unknown ssRF startup option: $1" >&2; exit 2 ;;
    esac
done
[[ "$GENESIS_SSRF_BURN_MODE" == fit || "$GENESIS_SSRF_BURN_MODE" == operator ]] || {
    echo '[FAIL] --burn-mode must be fit or operator' >&2; exit 2;
}
# Validate the user-supplied strings before they enter IOC startup command lines.
[[ "$GENESIS_SSRF_OPERATOR_MATERIAL" == Butanol || "$GENESIS_SSRF_OPERATOR_MATERIAL" == ND3 ]] || {
    echo '[FAIL] --material must be Butanol or ND3' >&2; exit 2;
}
python - "$GENESIS_SSRF_OPERATOR_POLARIZATION" "$GENESIS_SSRF_OPERATOR_CENTER_MHZ" <<'PY_SSRF_ARGS'
import math, sys
p = float(sys.argv[1])
assert math.isfinite(p) and -1.0 <= p <= 1.0
if sys.argv[2]:
    c = float(sys.argv[2])
    assert math.isfinite(c) and 1.0 <= c <= 100.0
PY_SSRF_ARGS
if [[ "$GENESIS_SSRF_BURN_MODE" == operator ]]; then
    export GENESIS_SSRF_BURN_MODE GENESIS_SSRF_OPERATOR_MATERIAL GENESIS_SSRF_OPERATOR_POLARIZATION
    if [[ -n "$GENESIS_SSRF_OPERATOR_CENTER_MHZ" ]]; then export GENESIS_SSRF_OPERATOR_CENTER_MHZ; fi
    echo "[NOTICE] OPERATOR REFERENCE waveform mode (not NMR fit validated)"
else
    export GENESIS_SSRF_BURN_MODE
fi
REMOTE="${GENESIS_KEYSIGHT_HOST:-genesis-keysight}"
REMOTE_ROOT="${GENESIS_KEYSIGHT_ROOT:-/home/unhpoltarg/genesis_keysight/runtime}"
REMOTE_VENV="${GENESIS_KEYSIGHT_VENV:-/home/unhpoltarg/genesis_keysight/.venv}"
PHOEBUS_SETTINGS="$ROOT/configs/phoebus/ssrf_physical.ini"
GUI="$ROOT/gui/genesis_ssrf.bob"
PYTHON_BIN="$(command -v python)"

session_exists() {
    local name="$1"
    local listing

    listing="$(screen -ls 2>&1 || true)"

    grep -E "\\.${name}[[:space:]]" <<< "${listing}" >/dev/null
}

start_local_ioc() {
    local session="$1" settings="$2" ioc="$3" ca_port="$4" pva_port="$5" ca_user="${6:-shrestha}"
    local log="$ROOT/logs/${session}.log"
    if session_exists "$session"; then
        echo "[FAIL] screen session already exists: $session" >&2
        exit 1
    fi
    mkdir -p "$ROOT/logs"
    screen -dmS "$session" bash -lc "cd '$ROOT' && env EPICS_CAS_SERVER_PORT='$ca_port' EPICS_PVA_SERVER_PORT='$pva_port' EPICS_CA_MAX_ARRAY_BYTES=262144 EPICS_CA_USER_NAME='$ca_user' GENESIS_SSRF_BURN_MODE='$GENESIS_SSRF_BURN_MODE' GENESIS_SSRF_OPERATOR_MATERIAL='$GENESIS_SSRF_OPERATOR_MATERIAL' GENESIS_SSRF_OPERATOR_POLARIZATION='$GENESIS_SSRF_OPERATOR_POLARIZATION' GENESIS_SSRF_OPERATOR_CENTER_MHZ='$GENESIS_SSRF_OPERATOR_CENTER_MHZ' PYTHONPATH='$ROOT' '$PYTHON_BIN' -u master_ioc.py -s '$settings' -i '$ioc' >> '$log' 2>&1"
    sleep 2
    if ! session_exists "$session"; then
        echo "[FAIL] $session exited; log follows" >&2
        tail -100 "$log" >&2 || true
        exit 1
    fi
    echo "[PASS] $session"
}

echo "============================================================"
echo " GENESIS SSRF FINAL PHYSICAL START"
echo "============================================================"

echo
if [[ "${CONDA_DEFAULT_ENV:-}" != "genesis-p1" ]]; then
    echo "[FAIL] Activate conda environment first: conda activate genesis-p1" >&2
    exit 1
fi

if pgrep -f 'tools/ssrf_physical_no_rf_demo_ioc.py' >/dev/null 2>&1; then
    echo "[FAIL] Legacy physical no-RF demo IOC is running. Stop it first." >&2
    exit 1
fi

if pgrep -f 'master_ioc.py -s configs/ssrf/' >/dev/null 2>&1; then
    echo "[FAIL] Normal/simulator ssRF IOC process detected. Stop it before final physical startup." >&2
    pgrep -af 'master_ioc.py -s configs/ssrf/' || true
    exit 1
fi

for s in genesis-ssrf-smc genesis-ssrf-nmr-sw genesis-ssrf-rf-sw genesis-ssrf-interlock genesis-ssrf-sequencer genesis-ssrf-recipe genesis-ssrf-fitter genesis-ssrf-archiver; do
    if session_exists "$s"; then
        echo "[FAIL] stale final ssRF session exists: $s" >&2
        exit 1
    fi
done

# STALE_FITTER_GUARD_V1
FITTER_PATTERN='[p]ython.*-m online_fitter\.epics_run'

if pgrep -f "$FITTER_PATTERN" >/dev/null 2>&1; then
    echo "[FAIL] stale online fitter process detected before startup" >&2
    pgrep -af "$FITTER_PATTERN" >&2 || true
    echo "Run genesis ssrf-stop before restarting." >&2
    exit 1
fi

for port in 8778 8779
do
    if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[FAIL] fitter TCP port ${port} is already occupied" >&2
        lsof -nP -iTCP:${port} -sTCP:LISTEN >&2 || true
        exit 1
    fi
done

# GUARD_STALE_PYNMR_IPC_V1
echo "===== CHECK PYNMR IPC ====="
"$PYTHON_BIN" - <<'PY_IPC'
import errno, os, socket, stat
from pathlib import Path

p = Path.home() / ".local/state/genesis/nmr-ipc/events.sock"

# Create and validate the private listener directory.
parent = p.parent
parent.mkdir(mode=0o700, parents=True, exist_ok=True)

for component in (
    Path.home() / ".local",
    Path.home() / ".local/state",
    Path.home() / ".local/state/genesis",
    parent,
):
    info = component.lstat()
    if (
        component.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise SystemExit("[FAIL] Unsafe IPC directory component")

info = parent.lstat()
if stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit("[FAIL] Private IPC requires mode 0700")

if not os.path.lexists(p):
    print("[PASS] PyNMR IPC path clear")
else:
    d, st = p.parent.lstat(), p.lstat()
    if (not stat.S_ISDIR(d.st_mode)
            or p.parent.is_symlink()
            or d.st_uid != os.getuid()
            or not stat.S_ISSOCK(st.st_mode)
            or st.st_uid != os.getuid()):
        raise SystemExit("[FAIL] Unsafe IPC identity")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
        sk.settimeout(1.0)
        rc = sk.connect_ex(str(p))

    if rc != errno.ECONNREFUSED:
        raise SystemExit("[FAIL] IPC live or uncertain")

    now = p.lstat()
    if (now.st_dev, now.st_ino, now.st_uid, now.st_mode) != (
            st.st_dev, st.st_ino, st.st_uid, st.st_mode):
        raise SystemExit("[FAIL] IPC changed during check")

    p.unlink()
    print("[PASS] Removed verified stale PyNMR IPC socket")
PY_IPC

echo "===== AUTHORITATIVE SAFE PHYSICAL PREFLIGHT ====="
"$ROOT/scripts/check_ssrf_physical_readonly.sh"

echo
echo "===== VERIFY FINAL KEYSIGHT RUNTIME IS DEPLOYED ====="
ssh "$REMOTE" "test -f '$REMOTE_ROOT/configs/ssrf_physical/keysight_wavegen/settings.yaml' && test -f '$REMOTE_ROOT/ssrf_ioc/keysight_wavegen.py'" || {
    echo "[FAIL] Final Keysight runtime is not deployed." >&2
    echo "Run: genesis ssrf-deploy-keysight" >&2
    exit 1
}

echo
echo "===== START SINGLE PHYSICAL KEYSIGHT OWNER ON PI ====="
ssh "$REMOTE" "bash -s" <<REMOTE_SCRIPT
set -euo pipefail
ROOT='$REMOTE_ROOT'
VENV='$REMOTE_VENV'
SESSION='genesis_ssrf_wgen'
PROFILE='configs/ssrf_physical/keysight_wavegen'
LOG="\$ROOT/logs/genesis_ssrf_wgen.log"
PATTERN='[p]ython -u master_ioc.py -s configs/ssrf_physical/keysight_wavegen -i keysight_wavegen'
mkdir -p "\$ROOT/logs"
if pgrep -f "\$PATTERN" >/dev/null 2>&1; then
  echo '[FAIL] final physical Keysight IOC already running'
  pgrep -af "\$PATTERN"
  exit 1
fi
screen -S "\$SESSION" -X quit >/dev/null 2>&1 || true
screen -dmS "\$SESSION" bash -lc "cd '\$ROOT' && source '\$VENV/bin/activate' && env EPICS_CAS_SERVER_PORT=5064 EPICS_PVA_SERVER_PORT=5075 EPICS_CA_MAX_ARRAY_BYTES=262144 PYTHONPATH='\$ROOT' python -u master_ioc.py -s '\$PROFILE' -i keysight_wavegen >> '\$LOG' 2>&1"
sleep 4
pgrep -f "\$PATTERN" >/dev/null 2>&1 || { tail -100 "\$LOG"; exit 1; }
echo '[PASS] single physical Keysight IOC running'
REMOTE_SCRIPT

echo
echo "===== START LOCAL PHYSICAL HARDWARE OWNERS ====="
start_local_ioc genesis-ssrf-smc configs/ssrf_physical/smc100a smc100a 5068 5078
start_local_ioc genesis-ssrf-nmr-sw configs/ssrf_physical/nmr_switch minicircuits_switch 5071 5081
start_local_ioc genesis-ssrf-rf-sw configs/ssrf_physical/rf_switch minicircuits_switch 5072 5082
start_local_ioc genesis-ssrf-interlock configs/ssrf_physical/interlock ssrf_interlock 5073 5083
start_local_ioc genesis-ssrf-sequencer configs/ssrf_physical/sequencer ssrf_sequencer 5074 5084 genesis_ssrf_sequencer

echo
echo "===== START FITTER + BURN SETUP ANALYSIS ====="
mkdir -p "$ROOT/logs"
# Pin the existing physical PyNMR Deuteron process at each startup.
PYNMR_ID="$("$PYTHON_BIN" - <<'PY_PYNMR'
from pathlib import Path
from online_fitter.pynmr_producer_session import (
    PinnedPyNMRProducer,
    process_start_ticks,
)

matches = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        pid = int(proc.name)
        ticks = process_start_ticks(pid)
        if PinnedPyNMRProducer(pid, ticks).matches_process():
            matches.append((pid, ticks))
    except (OSError, ValueError):
        continue

if len(matches) != 1:
    raise SystemExit(
        f"Expected one authorized PyNMR Deuteron process; found {len(matches)}"
    )

print(*matches[0])
PY_PYNMR
)"

read -r PYNMR_PID PYNMR_TICKS <<< "$PYNMR_ID"
[[ "$PYNMR_PID" =~ ^[0-9]+$ && "$PYNMR_TICKS" =~ ^[0-9]+$ ]] || {
    echo "[FAIL] Invalid PyNMR producer identity" >&2
    exit 1
}
echo "[PASS] Authorized PyNMR producer PID=$PYNMR_PID"

screen -dmS genesis-ssrf-fitter bash -lc "cd '$ROOT' && exec env EPICS_CAS_SERVER_PORT=5100 EPICS_PVA_SERVER_PORT=5101 EPICS_CA_MAX_ARRAY_BYTES=262144 EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST=127.0.0.1:5071 PYTHONPATH='$ROOT' GENESIS_PYNMR_PRODUCER_PID='$PYNMR_PID' GENESIS_PYNMR_PRODUCER_START_TICKS='$PYNMR_TICKS' '$PYTHON_BIN' -u -m online_fitter.epics_run --nmr-protected --nmr-source pynmr --pynmr-only --pynmr-port 8779 --pynmr-ipc-path '$HOME/.local/state/genesis/nmr-ipc/events.sock' --pynmr-ipc-allowed-uid '$(id -u)' --material d-butanol --mode equilibrium >> '$ROOT/logs/genesis-ssrf-fitter.log' 2>&1"
sleep 3
session_exists genesis-ssrf-fitter || { tail -100 "$ROOT/logs/genesis-ssrf-fitter.log"; exit 1; }
start_local_ioc genesis-ssrf-recipe configs/ssrf_physical/burn_recipe burn_recipe 5090 5091

echo
echo "===== START CSV PV ARCHIVER ====="
start_local_ioc genesis-ssrf-archiver configs/ssrf_physical/archiver archiver 5092 5093
env EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST="127.0.0.1:5092" python - <<'PY'
import asyncio
from aioca import caput, caget
async def main():
    await caput("UNH:GEN:SSRF:ARCH:Archive_Enable", 1, wait=True, timeout=3)
    for _ in range(30):
        status = int(await caget("UNH:GEN:SSRF:ARCH:Archive_Status", timeout=2))
        count = int(await caget("UNH:GEN:SSRF:ARCH:Archive_PV_Count", timeout=2))
        if status == 1 and count > 0:
            print(f"[PASS] CSV archiver running with {count} PVs")
            return
        await asyncio.sleep(0.2)
    raise SystemExit("CSV archiver did not become active")
asyncio.run(main())
PY

echo
echo "===== VERIFY FINAL EPICS SURFACE ====="
env EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST="127.0.0.1:5068 192.168.1.24:5064 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074 127.0.0.1:5100 127.0.0.1:5090 127.0.0.1:5092" EPICS_CA_MAX_ARRAY_BYTES=262144 python - <<'PY'
import asyncio
from aioca import caget
from logic_devices.ssrf_epics_io import EpicsIO

PVS = [
    "UNH:GEN:SSRF:GEN:COMM",
    "UNH:GEN:SSRF:WGEN:COMM",
    "UNH:GEN:SSRF:NMR_SW:COMM",
    "UNH:GEN:SSRF:RF_SW:COMM",
    "UNH:GEN:SSRF:ILK:INPUTS_OK",
    "UNH:GEN:SSRF:SEQ:EXECUTION_ENABLED",
    "UNH:GEN:SSRF:SEQ:EXECUTION_ARMED",
    "UNH:GEN:SSRF:SEQ:PREPARED",
    "UNH:GEN:SSRF:FIT:FITTER_UP",
    "UNH:GEN:SSRF:RECIPE:STATE",
    "UNH:GEN:SSRF:ARCH:Archive_Status",
    "UNH:GEN:SSRF:ARCH:Archive_PV_Count",
]
async def main():
    io = EpicsIO(timeout_s=3)
    for pv in PVS:
        value = await caget(pv, timeout=5.0)
        print(f"{pv:<48} = {value}")
    if int(await io.read_safety("UNH:GEN:SSRF:GEN:RF_RBV")) != 0:
        raise SystemExit("SMC RF is not OFF")
    if int(await io.read_safety("UNH:GEN:SSRF:GEN:FM_RBV")) != 0:
        raise SystemExit("SMC FM is not OFF")
    if int(await io.read_safety("UNH:GEN:SSRF:WGEN:OUTPUT_RBV")) != 0:
        raise SystemExit("WaveGen output is not OFF")
    if int(await io.read_safety("UNH:GEN:SSRF:SEQ:EXECUTION_ARMED")) != 0:
        raise SystemExit("sequencer unexpectedly armed at startup")
    if int(await io.read_safety("UNH:GEN:SSRF:SEQ:MANUAL_ARMED")) != 0:
        raise SystemExit("manual operation unexpectedly armed at startup")
    if int(await io.read_safety("UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV")) != 0:
        raise SystemExit("SMC sweep is not disarmed")
asyncio.run(main())
PY

echo
echo "===== OPEN FINAL OPERATOR GUI ====="
echo "[INFO] ssRF hardware/control stack is ready."
echo "[INFO] GUI is independent; launch when wanted with:"
echo "       ./scripts/start_ssrf_phoebus.sh"

echo
echo "============================================================"
echo " FINAL SSRF STACK READY"
echo " JSON/session logging and per-PV CSV archiving are active."
echo " No burn is armed. No RF/FM/WaveGen output was enabled by startup."
echo " Burn mode: $GENESIS_SSRF_BURN_MODE (no RF execution at startup)"
echo " Adaptive burn: GENERATE / LOAD -> ARM -> BURN ONCE"
echo " Manual RF: select carrier/level -> MANUAL ARM -> RF ON; RF OFF / ABORT for safe cleanup."
echo "============================================================"
