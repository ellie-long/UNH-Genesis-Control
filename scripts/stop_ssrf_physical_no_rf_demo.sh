#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

echo "============================================================"
echo " GENESIS SSRF PHYSICAL NO-RF WORKFLOW STOP"
echo "============================================================"

if [ -f /tmp/genesis_ssrf_demo_ioc.pid ]; then
    PID="$(cat /tmp/genesis_ssrf_demo_ioc.pid 2>/dev/null || true)"
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        echo "[PASS] workflow IOC stop requested: PID $PID"
    fi
    rm -f /tmp/genesis_ssrf_demo_ioc.pid
fi

pkill -TERM -f 'python .*ssrf_physical_no_rf_demo_ioc.py' 2>/dev/null || true
pkill -TERM -f 'python .*online_fitter\.epics_run' 2>/dev/null || true
pkill -TERM -f 'python .*ssrf_analysis\.fitter_stage_runtime' 2>/dev/null || true
sleep 2
pkill -KILL -f 'python .*ssrf_physical_no_rf_demo_ioc.py' 2>/dev/null || true
pkill -KILL -f 'python .*online_fitter\.epics_run' 2>/dev/null || true
pkill -KILL -f 'python .*ssrf_analysis\.fitter_stage_runtime' 2>/dev/null || true

echo
echo "===== STOP PHYSICAL KEYSIGHT UPLOAD IOC ====="
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
    echo "[INFO] physical upload IOC survived TERM; using KILL"
    pkill -KILL -f "$PATTERN" >/dev/null 2>&1 || true
    sleep 1
fi
screen -wipe >/dev/null 2>&1 || true
if pgrep -af "$PATTERN"; then
    echo "[FAIL] physical upload IOC still exists"
else
    echo "[PASS] no physical upload IOC"
fi
REMOTE

sleep 2

echo
echo "===== VERIFY RUNTIMES ====="
ps aux \
  | grep -E 'ssrf_physical_no_rf_demo_ioc|online_fitter\.epics_run|fitter_stage_runtime' \
  | grep -v grep \
  || echo "[PASS] no workflow/fitter runtime processes"

for port in 5074 5084 8777 8778
do
    if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[BUSY] ${port}"
        lsof -nP -iTCP:${port} -sTCP:LISTEN || true
    else
        echo "[PASS] port ${port} free"
    fi
done

ssh genesis-keysight \
  'pgrep -af "[p]ython .*master_ioc.py.*ssrf_physical_upload.*keysight_wavegen" || echo "[PASS] no physical upload IOC"' \
  || true

echo
echo "===== FINAL PHYSICAL STATE ====="
./scripts/genesisctl.sh ssrf-physical-status || true
