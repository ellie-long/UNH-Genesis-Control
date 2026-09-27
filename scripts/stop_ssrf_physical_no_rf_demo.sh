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
pkill -f 'python .*ssrf_physical_no_rf_demo_ioc.py' 2>/dev/null || true
pkill -f 'python .*online_fitter\.epics_run' 2>/dev/null || true

ssh genesis-keysight   'screen -S genesis_ssrf_demo_wgen -X quit >/dev/null 2>&1 || true'   || true

sleep 3

echo
echo "===== VERIFY RUNTIMES ====="
ps aux | grep -E   'ssrf_physical_no_rf_demo_ioc|online_fitter\.epics_run|fitter_stage_runtime'   | grep -v grep   || echo "[PASS] no workflow/fitter runtime processes"

for port in 5074 8777 8778
do
    if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[BUSY] ${port}"
        lsof -nP -iTCP:${port} -sTCP:LISTEN || true
    else
        echo "[PASS] port ${port} free"
    fi
done

ssh genesis-keysight   'pgrep -af "[p]ython .*master_ioc.py.*ssrf_physical_upload.*keysight_wavegen" || echo "[PASS] no physical upload IOC"'   || true

echo
echo "===== FINAL PHYSICAL STATE ====="
./scripts/genesisctl.sh physical-status || true
