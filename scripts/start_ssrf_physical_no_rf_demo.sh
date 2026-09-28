#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SETTINGS="$ROOT/configs/phoebus/ssrf_physical_no_rf_demo.ini"
GUI="$ROOT/gui/genesis_ssrf.bob"

cleanup_remote_wgen() {
ssh genesis-keysight 'bash -s' <<'REMOTE' || true
set +e
PATTERN='[p]ython master_ioc.py -s configs/commissioning/ssrf_physical_upload/keysight_wavegen -i keysight_wavegen'
for session in genesis_ssrf_demo_wgen genesis_keysight_upload
do
    screen -S "$session" -X quit >/dev/null 2>&1 || true
done
pkill -TERM -f "$PATTERN" >/dev/null 2>&1 || true
sleep 2
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    pkill -KILL -f "$PATTERN" >/dev/null 2>&1 || true
    sleep 1
fi
screen -wipe >/dev/null 2>&1 || true
REMOTE
}

echo "============================================================"
echo " GENESIS SSRF PHYSICAL NO-RF GUI DEMO START"
echo "============================================================"

echo
echo "===== AUTHORITATIVE PHYSICAL PREFLIGHT ====="
./scripts/genesisctl.sh ssrf-physical-status

echo
echo "===== LOCAL PORT GUARD ====="
for port in 5064 5074 5076 5084 8777 8778
do
  if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1
  then
    echo "[FAIL] local TCP ${port} already in use"
    lsof -nP -iTCP:${port} -sTCP:LISTEN
    exit 1
  fi
done
echo "[PASS] local demo/fitter ports free"

echo
echo "===== CLEAN STALE PHYSICAL KEYSIGHT IOC ====="
cleanup_remote_wgen

echo
echo "===== START PHYSICAL KEYSIGHT UPLOAD-ONLY IOC ====="
if ! ssh genesis-keysight 'bash -s' <<'REMOTE'
set -euo pipefail
ROOT=/home/unhpoltarg/genesis_keysight
RUNTIME=$ROOT/runtime
VENV=$ROOT/.venv
PROFILE=configs/commissioning/ssrf_physical_upload/keysight_wavegen
SESSION=genesis_ssrf_demo_wgen
LOG=$ROOT/logs/$(date -u +%Y-%m-%d_%H-%M-%S_UTC)_demo_upload.log
PATTERN='[p]ython master_ioc.py -s configs/commissioning/ssrf_physical_upload/keysight_wavegen -i keysight_wavegen'

test -f "$RUNTIME/$PROFILE/settings.yaml"

if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    echo "[FAIL] stale physical Keysight IOC survived cleanup"
    pgrep -af "$PATTERN"
    exit 1
fi

screen -dmS "$SESSION" \
bash -lc "
cd '$RUNTIME' &&
source '$VENV/bin/activate' &&
export EPICS_CAS_SERVER_PORT=5064 &&
export EPICS_PVA_SERVER_PORT=5075 &&
export EPICS_CA_MAX_ARRAY_BYTES=262144 &&
exec python master_ioc.py \\
  -s '$PROFILE' \\
  -i keysight_wavegen \\
  >> '$LOG' 2>&1
"

sleep 6
COUNT="$(pgrep -fc "$PATTERN" || true)"
if [[ "$COUNT" -ne 1 ]]; then
    echo "[FAIL] expected exactly one physical Keysight IOC; found $COUNT"
    pgrep -af "$PATTERN" || true
    tail -80 "$LOG" || true
    exit 1
fi

if grep -Eq 'Resource busy|unable to bind port|dynamically assigned TCP port' "$LOG"; then
    echo "[FAIL] physical Keysight IOC started with resource/port contention"
    tail -80 "$LOG"
    exit 1
fi

echo "[PASS] exactly one physical Keysight upload IOC running"
echo "[INFO] log: $LOG"
REMOTE
then
    echo "[FAIL] could not start one clean physical Keysight upload IOC"
    cleanup_remote_wgen
    exit 1
fi

echo
echo "===== VERIFY PHYSICAL KEYSIGHT IOC READY ====="
if ! env \
  EPICS_CA_AUTO_ADDR_LIST=NO \
  EPICS_CA_ADDR_LIST="192.168.1.24:5064" \
  EPICS_CA_MAX_ARRAY_BYTES=262144 \
  python - <<'PYCODE'
import asyncio
from aioca import caget

P = "UNH:GEN:SSRF:WGEN:"

async def main():
    comm = 0
    for _ in range(20):
        try:
            comm = int(await caget(P + "COMM", timeout=2.0))
        except Exception:
            comm = 0
        if comm == 1:
            break
        await asyncio.sleep(0.5)

    values = {}
    for name in (
        "COMM", "WRITE_ENABLED", "OUTPUT_RBV", "PERIOD_RBV",
        "VHIGH_RBV", "VLOW_RBV", "ERROR",
    ):
        values[name] = await caget(P + name, timeout=5.0)
        print(f"{name:<20} = {values[name]}")

    if int(values["COMM"]) != 1:
        raise SystemExit("physical Keysight IOC COMM != 1")
    if int(values["WRITE_ENABLED"]) != 1:
        raise SystemExit("physical upload profile WRITE_ENABLED != 1")
    if int(values["OUTPUT_RBV"]) != 0:
        raise SystemExit("physical Keysight output is not OFF")
    if abs(float(values["PERIOD_RBV"]) - 0.010) > 1e-6:
        raise SystemExit("unexpected physical Keysight period")
    if abs(float(values["VHIGH_RBV"]) - 0.700) > 0.01:
        raise SystemExit("unexpected physical Keysight VHIGH")
    if abs(float(values["VLOW_RBV"]) + 0.700) > 0.01:
        raise SystemExit("unexpected physical Keysight VLOW")
    print("[PASS] physical Keysight upload IOC ready")

asyncio.run(main())
PYCODE
then
    echo "[FAIL] physical Keysight upload IOC did not become ready"
    cleanup_remote_wgen
    exit 1
fi

echo
echo "===== START LOCAL DEMO IOC ====="
rm -f /tmp/genesis_ssrf_demo_ioc.pid /tmp/genesis_ssrf_demo_ioc.log

env \
  EPICS_CAS_SERVER_PORT=5074 \
  EPICS_PVA_SERVER_PORT=5084 \
  EPICS_CA_AUTO_ADDR_LIST=NO \
  EPICS_CA_ADDR_LIST="127.0.0.1:5064 192.168.1.24:5064" \
  EPICS_CA_MAX_ARRAY_BYTES=262144 \
  EPICS_CA_USER_NAME=shrestha \
  PYTHONPATH="$ROOT" \
  python -u tools/ssrf_physical_no_rf_demo_ioc.py \
  >/tmp/genesis_ssrf_demo_ioc.log 2>&1 &

DEMO_PID=$!
echo "$DEMO_PID" >/tmp/genesis_ssrf_demo_ioc.pid
sleep 4

if ! kill -0 "$DEMO_PID" 2>/dev/null; then
    echo "[FAIL] demo IOC process exited"
    cat /tmp/genesis_ssrf_demo_ioc.log
    cleanup_remote_wgen
    exit 1
fi

if ! grep -q 'HEADLESS IOC READY' /tmp/genesis_ssrf_demo_ioc.log; then
    echo "[FAIL] demo IOC did not reach headless-ready state"
    cat /tmp/genesis_ssrf_demo_ioc.log
    kill "$DEMO_PID" 2>/dev/null || true
    cleanup_remote_wgen
    exit 1
fi

echo "[PASS] demo IOC process alive"
echo "[PASS] demo IOC headless-ready"

echo
echo "===== DEMO IOC CA CHECK ====="
env \
  EPICS_CA_AUTO_ADDR_LIST=NO \
  EPICS_CA_ADDR_LIST=127.0.0.1:5074 \
  python - <<'PYCODE'
import asyncio
from aioca import caget

EXPECTED = {
    "UNH:GEN:SSRF:SEQ:DEMO_STATE": "IDLE",
    "UNH:GEN:SSRF:SEQ:NO_RF_LOCK": 1,
    "UNH:GEN:SSRF:SEQ:NMR_MASK": 0,
    "UNH:GEN:SSRF:SEQ:SSRF_MASK": 3,
    "UNH:GEN:SSRF:SEQ:WGEN_OUTPUT": 0,
}

async def main():
    for pv, expected in EXPECTED.items():
        value = await caget(pv, timeout=5.0)
        print(pv, "=", value)
        if isinstance(expected, str):
            if str(value) != expected:
                raise SystemExit(f"{pv} expected {expected}, got {value}")
        elif int(value) != expected:
            raise SystemExit(f"{pv} expected {expected}, got {value}")

asyncio.run(main())
PYCODE

echo
echo "===== LAUNCH PHOEBUS ====="
if ! "$ROOT/scripts/start_genesis_phoebus.sh" \
  "$SETTINGS" \
  "$GUI" \
  >/tmp/genesis_ssrf_demo_phoebus.log 2>&1
then
    echo "[FAIL] Phoebus launcher failed"
    cat /tmp/genesis_ssrf_demo_phoebus.log || true
    ./scripts/stop_ssrf_physical_no_rf_demo.sh || true
    exit 1
fi

echo
echo "============================================================"
echo " GUI READY"
echo "============================================================"
echo "Main GUI workflow:"
echo "  SETUP: START FITTER -> REPLAY EQ -> CALIBRATE"
echo "  ssRF MODE is optional; BLAST/BURN sets it automatically"
echo "  EXECUTION: ARM -> BLAST / BURN"
echo "  PHYSICAL_NO_RF lock: SMC RF OFF, SMC FM OFF, Keysight OUTPUT OFF"
echo "============================================================"
