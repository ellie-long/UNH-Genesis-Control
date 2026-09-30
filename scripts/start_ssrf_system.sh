#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EXPECTED=(
    genesis-ssrf-smc
    genesis-ssrf-nmr-sw
    genesis-ssrf-rf-sw
    genesis-ssrf-interlock
    genesis-ssrf-sequencer
    genesis-ssrf-fitter
    genesis-ssrf-recipe
    genesis-ssrf-archiver
)

SCREENS="$(screen -ls 2>/dev/null || true)"

has_screen()
{
    printf '%s\n' "$SCREENS" |
        grep -qE "[.]${1}[[:space:]]"
}

running=0

for s in "${EXPECTED[@]}"; do
    if has_screen "$s"; then
        running=$((running + 1))
    fi
done

echo "============================================================"
echo " GENESIS SSRF SYSTEM"
echo "============================================================"

if (( running == ${#EXPECTED[@]} )); then

    echo "[OK] ssRF physical/control stack already running"
    "$ROOT/scripts/genesisctl.sh" ssrf-physical-status

elif (( running == 0 )); then

    echo "[START] ssRF physical/control stack"
    "$ROOT/scripts/genesisctl.sh" ssrf-start

else

    echo "[STOP] ssRF stack is partially running."
    echo "       Refusing duplicate hardware owners."
    echo

    for s in "${EXPECTED[@]}"; do
        if has_screen "$s"; then
            echo "  [UP]      $s"
        else
            echo "  [MISSING] $s"
        fi
    done

    exit 1
fi

echo
echo "===== SSRF PHOEBUS ====="
"$ROOT/scripts/start_ssrf_phoebus.sh"

echo
echo "[READY] ssRF hardware/control + GUI available"
