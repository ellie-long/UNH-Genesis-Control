#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "============================================================"
echo " GENESIS SSRF SYSTEM"
echo "============================================================"

# Process/session observations only. Never run safe-OFF preflight or direct
# instrument probes against existing owners merely to reopen the GUI.
STATE="$(python -m logic_devices.ssrf_stack_status)"
case "$STATE" in
    COMPLETE)
        echo "[OK] ssRF physical/control stack already running; reusing current operation"
        ;;
    ZERO)
        echo "[START] ssRF physical/control stack"
        "$ROOT/scripts/genesisctl.sh" ssrf-start
        ;;
    *)
        echo "[FAIL] ambiguous ssRF ownership; refusing duplicate physical owners" >&2
        exit 1
        ;;
esac

echo
echo "===== SSRF PHOEBUS ====="
"$ROOT/scripts/start_ssrf_phoebus.sh"
echo
echo "[READY] ssRF hardware/control + GUI available"
