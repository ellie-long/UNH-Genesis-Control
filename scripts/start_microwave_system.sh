#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "============================================================"
echo " GENESIS MICROWAVE SYSTEM"
echo "============================================================"

STATUS="$("$ROOT/scripts/microwave_physical.sh" status 2>&1 || true)"

healthy=1

for pattern in \
    '^UCA COMM[[:space:]]*=[[:space:]]*1$' \
    '^Source connected[[:space:]]*=[[:space:]]*1$' \
    '^Robot COMM[[:space:]]*=[[:space:]]*1$' \
    '^System connected[[:space:]]*=[[:space:]]*1$' \
    '^Archive status[[:space:]]*=[[:space:]]*1$'
do
    if ! printf '%s\n' "$STATUS" | grep -Eq "$pattern"; then
        healthy=0
    fi
done

if (( healthy == 1 )); then

    echo "[OK] Microwave hardware/control stack already running"
    echo
    printf '%s\n' "$STATUS"

else

    SCREEN_LIST="$(screen -ls 2>&1 || true)"
    if grep -Eq \
        '[.](mw_spd1305x|mw_vaunix_lms|mw_pcl602|mw_supervisor|archiver|ioc-manager)[[:space:]]' <<< "$SCREEN_LIST"
    then
        echo "[STOP] Microwave processes exist but the stack is not healthy."
        echo "       Refusing to create duplicate hardware owners."
        echo
        printf '%s\n' "$STATUS"
        exit 1
    fi

    echo "[START] Microwave physical/control stack"
    "$ROOT/scripts/microwave_physical.sh" start
fi

echo
echo "===== MICROWAVE PHOEBUS ====="
"$ROOT/scripts/start_microwave_phoebus.sh"

echo
echo "[READY] Microwave hardware/control + GUI available"
