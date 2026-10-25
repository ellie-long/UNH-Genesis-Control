#!/usr/bin/env bash
# Stop the Genesis live system on this laptop and copy the night's data to
# Windows (a copy: WSL keeps the originals; nothing is ever deleted).
#
#   stop.sh                      # microwaves, Phoebus, fitter, magnet, trend history, archiver, cryo
#   stop.sh --cryo-readonly-kill # if Utsav's cryo stop refuses because the cold-board output is ON:
#                                # stop the read-only cryo IOCs directly (Genesis never writes the
#                                # cold board, so its output is unchanged)
#
# PyNMR is left to you: close its window yourself (it may ask "Exit While
# Running?", which only a person should answer).
set -uo pipefail

ROOT="${GENESIS_ROOT:-$HOME/genesis-live}"
VENV="${GENESIS_VENV:-$HOME/.venvs/genesis-p1}"
ENV_FILE="$HOME/.genesis-live.env"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
export PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin"
export CONDA_DEFAULT_ENV=genesis-p1 CONDA_PREFIX="$VENV"
force_cryo=0
[[ "${1:-}" == "--cryo-readonly-kill" ]] && force_cryo=1
cd "$ROOT" || exit 1
step() { printf '\n===== %s =====\n' "$*"; }

quit_session() {   # screen session name, process pattern
    local s
    for s in $(screen -ls 2>/dev/null | grep -oE "[0-9]+\.$1\b"); do screen -S "$s" -X quit; done
    sleep 1
    local pid
    for pid in $(pgrep -f "$2"); do [[ "$pid" != "$$" ]] && kill "$pid" 2>/dev/null; done
    echo "[STOPPED] $1"
}

step "1. Microwaves (Utsav's stop: RF off, safe UCA baseline)"
if screen -ls | grep -q "\.ioc-manager"; then
    timeout 180 ./scripts/microwave_physical.sh stop 2>&1 | tail -6
    screen -ls | grep -q "\.ioc-manager" && echo "[WARN] the microwave stop kept its IOCs (it could not confirm a safe state); check the UCA and RF, then stop them by hand"
else
    echo "[--] not running"
fi

step "2. Phoebus, fitter, magnet IOC, trend history"
quit_session genesis-phoebus "product-5.0.5.jar"
quit_session genesis-fitter "online_fitter.epics_run"
quit_session genesis-magnet "master_ioc.py -s configs/magnet_physical/ips120"
quit_session genesis-hist "trend_history -i trend_history"

step "3. Master archiver (stopped properly, so it flushes)"
python -m archive.master_service stop 2>&1 | tail -2

step "4. Cryo stack"
timeout 300 ./scripts/cryo_physical.sh stop 2>&1 | tail -4
if screen -ls | grep -q "genesis-cryo-"; then
    if ((force_cryo)); then
        quit_session genesis-cryo-siglent-coldboard "configs/target_physical/siglent_coldboard"
        for s in $(screen -ls | grep -oE "genesis-cryo-[a-z0-9-]+" | sort -u); do screen -S "$s" -X quit; done
        sleep 2
        for pid in $(pgrep -f "master_ioc.py -s configs/target_physical/|ioc_manager.py -s configs/target_physical/"); do
            [[ "$pid" != "$$" ]] && kill "$pid" 2>/dev/null
        done
        echo "[STOPPED] read-only cryo IOCs (cold-board output unchanged)"
    else
        echo "[WARN] the cryo stop kept its IOCs (usually: cold-board output ON)."
        echo "       If that is expected, run: $0 --cryo-readonly-kill"
    fi
fi

step "5. Copy the data to Windows (copy, never move)"
dest="${DATA_COPY_DIR:-/mnt/c/Users/${WIN_USER:-$USER}}/Genesis_live_$(date +%F)"
mkdir -p "$dest"
cp -a "$HOME/genesis-live-local_data" "$dest/" 2>/dev/null
cp -a "$ROOT/logs" "$dest/genesis-live-logs" 2>/dev/null
cp -a "$ROOT/online_fitter/logs" "$dest/online_fitter-logs" 2>/dev/null
echo "[COPIED] $(du -sh "$dest" | cut -f1) to $dest"

step "Left running"
screen -ls | grep -E "genesis|ioc-manager|mw_" || echo "  no Genesis sessions"
echo "PyNMR: close its window yourself (Windows)."
