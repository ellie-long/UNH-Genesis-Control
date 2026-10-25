#!/usr/bin/env bash
# Start the whole Genesis live system on this laptop (WSL), as on 2026-10-24:
# cryo stack (+ Pi bridges), trend history, magnet IOC, online fitter, master
# archiver, PyNMR (Windows), Phoebus with the Panopticon, then the microwave
# stack (you type READY at its physical-sequence prompt).
#
#   start.sh                 # everything
#   start.sh --no-pynmr      # skip PyNMR          --no-microwaves   skip the microwave stack
#   start.sh --no-phoebus    # skip Phoebus
#
# Each part is skipped if it is already running, so a second run only fills in
# what is missing.  Nothing here answers a safety prompt for you.
set -uo pipefail

ROOT="${GENESIS_ROOT:-$HOME/genesis-live}"
VENV="${GENESIS_VENV:-$HOME/.venvs/genesis-p1}"
PHOEBUS_JAR="${PHOEBUS_JAR:-$HOME/phoebus/product-5.0.5/product-5.0.5.jar}"
ENV_FILE="$HOME/.genesis-live.env"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
PY="$VENV/bin/python"
export PATH="$VENV/bin:/usr/local/bin:/usr/bin:/bin"
export CONDA_DEFAULT_ENV=genesis-p1 CONDA_PREFIX="$VENV"      # Utsav's scripts check these

want_pynmr=1 want_microwaves=1 want_phoebus=1
for arg in "$@"; do
    case "$arg" in
        --no-pynmr) want_pynmr=0 ;;
        --no-microwaves) want_microwaves=0 ;;
        --no-phoebus) want_phoebus=0 ;;
        *) echo "unknown option $arg"; exit 2 ;;
    esac
done

cd "$ROOT" || { echo "no Genesis repository at $ROOT (run setup.sh)"; exit 1; }
mkdir -p logs
step() { printf '\n===== %s =====\n' "$*"; }
running() { screen -ls 2>/dev/null | grep -qE "[0-9]+\.$1[[:space:]]"; }

step "0. Network"
reach() {   # host label
    if ping -c1 -W2 "$1" >/dev/null 2>&1; then echo "[OK]   $2 ($1)"; else echo "[WARN] $2 ($1) not answering"; fi
}
reach 192.168.1.12 "fridge LS224"
reach 192.168.1.250 "magnet serial server (TXI017)"
reach 192.168.1.19 "UCA supply"
reach 192.168.1.23 "microwave Pi"
reach 192.168.1.24 "cryo Pi"
reach 192.168.0.72 "JLab NMR FPGA (needs the 192.168.0.x/24 address)"

step "1. Laptop-only settings (not committed; this lab's hardware)"
# The LS455 reports in tesla (UNIT? = T): its tesla_scale must be 1.0 until the
# driver takes the scale from UNIT? (2026-10-23 finding).
sed -i 's/^  tesla_scale: 0.0001$/  tesla_scale: 1.0     # LIVE: the LS455 reports tesla (UNIT? = T); not committed/' \
    configs/target_physical/ls455/settings.yaml
grep -q "tesla_scale: 1.0" configs/target_physical/ls455/settings.yaml && echo "[OK]   LS455 tesla_scale 1.0"
if grep -q "^  write_enabled: false" configs/magnet_physical/ips120/settings.yaml; then
    read -r -p "Enable magnet supply CONTROL from Phoebus (writes)? Type YES to enable, Enter for read-only: " ans
    if [[ "$ans" == "YES" ]]; then
        sed -i 's/^  write_enabled: false$/  write_enabled: true               # LIVE: control from Phoebus; not committed/' \
            configs/magnet_physical/ips120/settings.yaml
        echo "[OK]   magnet writes ENABLED"
    else
        echo "[OK]   magnet read-only"
    fi
else
    echo "[OK]   magnet writes already enabled in this copy"
fi

step "2. Cryo stack (19 IOCs, Pi bridges, cryo archiver)"
timeout 400 ./scripts/cryo_physical.sh start 2>&1 | grep -E "PASS|FAIL|WARN|READY|UP\]" | tail -8

step "3. Trend history (CA 5130)"
if running genesis-hist; then echo "[UP] already running"; else
    screen -dmS genesis-hist env EPICS_CAS_SERVER_PORT=5130 EPICS_PVAS_SERVER_PORT=5230 \
        bash -c "$PY -u master_ioc.py -s configs/target_physical/trend_history -i trend_history >> logs/trend_history_live.log 2>&1"
    echo "[STARTED]"
fi

step "4. Magnet IOC (CA 5131)"
if running genesis-magnet; then echo "[UP] already running"; else
    screen -dmS genesis-magnet env EPICS_CAS_SERVER_PORT=5131 EPICS_PVAS_SERVER_PORT=5231 \
        bash -c "$PY -u master_ioc.py -s configs/magnet_physical/ips120 -i ips120 >> logs/magnet_ips120_live.log 2>&1"
    echo "[STARTED] (if it stays disconnected, something else holds the supply's serial port 5001)"
fi

step "5. Online NMR fitter (CA 5100, sweeps on 8777)"
if running genesis-fitter; then echo "[UP] already running"; else
    screen -dmS genesis-fitter env EPICS_CAS_SERVER_PORT=5100 EPICS_PVA_SERVER_PORT=5101 EPICS_CA_MAX_ARRAY_BYTES=262144 \
        EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST=127.0.0.1:5071 PYTHONPATH="$ROOT" \
        bash -c "$PY -u -m online_fitter.epics_run --material d-butanol --mode equilibrium >> logs/genesis-fitter-live.log 2>&1"
    echo "[STARTED]"
fi

step "6. Master archiver (read-only, every subsystem's PVs)"
"$PY" -m archive.master_service start 2>&1 | tail -1

if ((want_pynmr)); then
    step "7. PyNMR (Windows, profile ${PYNMR_PROFILE:-Deuteron})"
    if powershell.exe -NoProfile -Command "if (Get-Process python -ErrorAction SilentlyContinue | Where-Object { \$_.MainWindowTitle -eq 'JLab Polarization Display' }) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
        echo "[UP] already open"
    elif [[ -n "${PYNMR_WIN_DIR:-}" && -n "${CONDA_WIN_EXE:-}" ]]; then
        powershell.exe -NoProfile -Command "Start-Process -FilePath '$CONDA_WIN_EXE' -ArgumentList 'run','-n','${PYNMR_CONDA_ENV:-pynmr}','--no-capture-output','python','pynmr_main.py','-p','${PYNMR_PROFILE:-Deuteron}' -WorkingDirectory '$PYNMR_WIN_DIR' -WindowStyle Hidden" \
            && echo "[STARTED] the 'JLab Polarization Display' window opens in ~20 s"
    else
        echo "[SKIP] set PYNMR_WIN_DIR and CONDA_WIN_EXE in $ENV_FILE"
    fi
fi

if ((want_phoebus)); then
    step "8. Phoebus: the Panopticon"
    if pgrep -f "product-5.0.5.jar" >/dev/null; then echo "[UP] already open"; else
        # in a screen session so it outlives this terminal
        screen -dmS genesis-phoebus bash -c "cd '$ROOT' && java -jar '$PHOEBUS_JAR' -settings configs/phoebus/panopticon_live.ini -resource 'file://$ROOT/gui/genesis_panopticon.bob' >> '$HOME/phoebus_live.log' 2>&1"
        echo "[STARTED] the Panopticon opens in ~20 s"
    fi
fi

if ((want_microwaves)); then
    step "9. Microwaves"
    if running ioc-manager; then
        echo "[UP] the microwave IOC manager is already running"
    else
        echo "The microwave start checks the Vaunix shim (plug the Vaunix USB in first), sets the UCA"
        echo "to 5.0 V / 0.05 A / OVP 5.1 V / OCP 0.11 A, then asks you to type READY."
        read -r -p "Press Enter to start the microwaves now, or type skip: " ans
        if [[ "$ans" != "skip" ]]; then
            ./scripts/microwave_physical.sh start
        else
            echo "[SKIP] start later with: $ROOT/scripts/live_laptop/start.sh (it fills in what is missing)"
        fi
    fi
fi

step "Running now"
screen -ls | grep -oE "[0-9]+\.[a-z0-9_-]+" | sed 's/^[0-9]*\./  /' | sort
echo
echo "Stop everything (and copy the data to Windows) with: $ROOT/scripts/live_laptop/stop.sh"
