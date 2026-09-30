#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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
    screen -dmS "$session" bash -lc "cd '$ROOT' && env EPICS_CAS_SERVER_PORT='$ca_port' EPICS_PVA_SERVER_PORT='$pva_port' EPICS_CA_MAX_ARRAY_BYTES=262144 EPICS_CA_USER_NAME='$ca_user' PYTHONPATH='$ROOT' '$PYTHON_BIN' -u master_ioc.py -s '$settings' -i '$ioc' >> '$log' 2>&1"
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

for port in 8777 8778
do
    if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[FAIL] fitter TCP port ${port} is already occupied" >&2
        lsof -nP -iTCP:${port} -sTCP:LISTEN >&2 || true
        exit 1
    fi
done

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
screen -dmS genesis-ssrf-fitter bash -lc "cd '$ROOT' && env EPICS_CAS_SERVER_PORT=5100 EPICS_PVA_SERVER_PORT=5101 EPICS_CA_MAX_ARRAY_BYTES=262144 PYTHONPATH='$ROOT' '$PYTHON_BIN' -u -m online_fitter.epics_run --replace --material d-butanol --mode equilibrium >> '$ROOT/logs/genesis-ssrf-fitter.log' 2>&1"
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
    for pv in PVS:
        value = await caget(pv, timeout=5.0)
        print(f"{pv:<48} = {value}")
    if int(await caget("UNH:GEN:SSRF:GEN:RF_RBV", timeout=3)) != 0:
        raise SystemExit("SMC RF is not OFF")
    if int(await caget("UNH:GEN:SSRF:GEN:FM_RBV", timeout=3)) != 0:
        raise SystemExit("SMC FM is not OFF")
    if int(await caget("UNH:GEN:SSRF:WGEN:OUTPUT_RBV", timeout=3)) != 0:
        raise SystemExit("WaveGen output is not OFF")
    if int(await caget("UNH:GEN:SSRF:SEQ:EXECUTION_ARMED", timeout=3)) != 0:
        raise SystemExit("sequencer unexpectedly armed at startup")
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
echo " Workflow: NMR/calibrate -> Burn Setup -> GENERATE / LOAD -> ARM -> BURN ONCE"
echo "============================================================"
