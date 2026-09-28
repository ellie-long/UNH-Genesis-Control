#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PHOEBUS="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
SETTINGS="$ROOT/configs/phoebus/ssrf_physical_no_rf_demo.ini"
GUI="$ROOT/gui/genesis_ssrf.bob"

echo "============================================================"
echo " GENESIS SSRF PHYSICAL NO-RF GUI DEMO START"
echo "============================================================"

echo
echo "===== AUTHORITATIVE PHYSICAL PREFLIGHT ====="
./scripts/genesisctl.sh physical-status

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
echo "===== START PHYSICAL KEYSIGHT UPLOAD-ONLY IOC ====="
ssh genesis-keysight 'bash -s' <<'REMOTE'
set -euo pipefail
ROOT=/home/unhpoltarg/genesis_keysight
RUNTIME=$ROOT/runtime
VENV=$ROOT/.venv
PROFILE=configs/commissioning/ssrf_physical_upload/keysight_wavegen
SESSION=genesis_ssrf_demo_wgen
LOG=$ROOT/logs/$(date -u +%Y-%m-%d_%H-%M-%S_UTC)_demo_upload.log

test -f "$RUNTIME/$PROFILE/settings.yaml"

screen -S "$SESSION" -X quit >/dev/null 2>&1 || true
screen -dmS "$SESSION" bash
sleep 1

CMD="cd '$RUNTIME' && \
source '$VENV/bin/activate' && \
export EPICS_CAS_SERVER_PORT=5064 && \
export EPICS_PVA_SERVER_PORT=5075 && \
export EPICS_CA_MAX_ARRAY_BYTES=262144 && \
exec python master_ioc.py -s '$PROFILE' -i keysight_wavegen >> '$LOG' 2>&1"

screen -S "$SESSION" -p 0 -X stuff "$CMD"
screen -S "$SESSION" -p 0 -X stuff $'\r'
sleep 5

pgrep -af '[p]ython .*master_ioc.py.*ssrf_physical_upload.*keysight_wavegen' >/dev/null

echo "[PASS] physical Keysight upload IOC running"
REMOTE

echo
echo "===== START LOCAL DEMO IOC ====="

rm -f \
  /tmp/genesis_ssrf_demo_ioc.pid \
  /tmp/genesis_ssrf_demo_ioc.log

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

if ! kill -0 "$DEMO_PID" 2>/dev/null
then
    echo "[FAIL] demo IOC process exited"
    echo
    cat /tmp/genesis_ssrf_demo_ioc.log
    exit 1
fi

if ! grep -q \
  'HEADLESS IOC READY' \
  /tmp/genesis_ssrf_demo_ioc.log
then
    echo "[FAIL] demo IOC did not reach headless-ready state"
    echo
    cat /tmp/genesis_ssrf_demo_ioc.log
    exit 1
fi

echo "[PASS] demo IOC process alive"
echo "[PASS] demo IOC headless-ready"

echo
echo "===== DEMO IOC CA CHECK ====="
env \
  EPICS_CA_AUTO_ADDR_LIST=NO \
  EPICS_CA_ADDR_LIST=127.0.0.1:5074 \
  python - <<'PY'
import asyncio
from aioca import caget

async def main():
    for pv in (
        "UNH:GEN:SSRF:SEQ:DEMO_STATE",
        "UNH:GEN:SSRF:SEQ:NO_RF_LOCK",
        "UNH:GEN:SSRF:SEQ:NMR_MASK",
        "UNH:GEN:SSRF:SEQ:SSRF_MASK",
        "UNH:GEN:SSRF:SEQ:WGEN_OUTPUT",
    ):
        print(pv, "=", await caget(pv, timeout=5.0))

asyncio.run(main())
PY

echo
echo "===== LAUNCH PHOEBUS ====="
"$ROOT/scripts/start_genesis_phoebus.sh" \
  "$SETTINGS" \
  "$GUI" \
  >/tmp/genesis_ssrf_demo_phoebus.log 2>&1

sleep 2

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
