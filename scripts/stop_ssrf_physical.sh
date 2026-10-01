#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! python -m logic_devices.ssrf_lifecycle --held; then
    exec python -m logic_devices.ssrf_lifecycle "$ROOT/scripts/stop_ssrf_physical.sh" "$@"
fi
REMOTE="${GENESIS_KEYSIGHT_HOST:-genesis-keysight}"

session_exists() {
    local name="$1"
    local listing

    listing="$(screen -ls 2>&1 || true)"

    grep -E "\\.${name}[[:space:]]" <<< "${listing}" >/dev/null
}

CA_LIST="127.0.0.1:5068 192.168.1.24:5064 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074"

SEQUENCER_PRESENT=0
if session_exists genesis-ssrf-sequencer || pgrep -f '[m]aster_ioc.py.*-i ssrf_sequencer' >/dev/null 2>&1; then
    SEQUENCER_PRESENT=1
fi

echo "[INFO] Establishing verified safe baseline before terminating any owners."
env GENESIS_SSRF_SEQ_PRESENT="$SEQUENCER_PRESENT" EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST="$CA_LIST" EPICS_CA_MAX_ARRAY_BYTES=262144 python - <<'PY_STOP'
import asyncio
import os
from logic_devices.ssrf_shutdown import establish_safe_shutdown
from logic_devices.ssrf_epics_io import EpicsIO
from pathlib import Path
from tools.phoebus_shutdown import close_instance

async def main():
    gui_error = None
    root = Path.cwd()
    try:
        await close_instance(root / "configs/phoebus/ssrf_physical.ini",
                             root / "gui/genesis_ssrf.bob")
    except Exception as exc:
        gui_error = exc
        print(f"[WARN] ssRF Phoebus close failed: {exc}; still attempting hardware safety")
    io = EpicsIO(execution_enabled=True)
    # Requests sequencer ABORT_CMD, or guarded OFF fallback if it is absent.
    try:
        await establish_safe_shutdown(io, sequencer_expected=os.environ["GENESIS_SSRF_SEQ_PRESENT"] == "1", lifecycle_held=True)
    except Exception as exc:
        if gui_error:
            raise RuntimeError(f"ssRF Phoebus close failed: {gui_error}; hardware safe-off failed: {exc}") from exc
        raise
    # Final explicit baseline verification before owner termination.
    rf=int(await io.read_safety("UNH:GEN:SSRF:GEN:RF_RBV"))
    fm=int(await io.read_safety("UNH:GEN:SSRF:GEN:FM_RBV"))
    wg=int(await io.read_safety("UNH:GEN:SSRF:WGEN:OUTPUT_RBV"))
    sr=int(await io.read_safety("UNH:GEN:SSRF:RF_SW:MASK_RBV"))
    nm=int(await io.read_safety("UNH:GEN:SSRF:NMR_SW:MASK_RBV"))
    if not (rf,fm,wg,sr,nm)==(0,0,0,3,0):
        raise RuntimeError("safe baseline not confirmed; refusing to stop IOCs")
    print("[PASS] safe baseline confirmed")
    if gui_error:
        raise RuntimeError(f"ssRF Phoebus close failed: {gui_error}; safe baseline confirmed")

try:
    asyncio.run(main())
except Exception as exc:
    raise SystemExit(f"[FAIL] {exc}; retaining physical owners for recovery")
PY_STOP

for s in genesis-ssrf-archiver genesis-ssrf-recipe genesis-ssrf-fitter genesis-ssrf-sequencer genesis-ssrf-interlock genesis-ssrf-rf-sw genesis-ssrf-nmr-sw genesis-ssrf-smc; do
    screen -S "$s" -X quit >/dev/null 2>&1 || true
done

# STALE_FITTER_CLEANUP_V1
FITTER_PATTERN='[p]ython.*-m online_fitter\.epics_run'

pkill -TERM -f "$FITTER_PATTERN" >/dev/null 2>&1 || true
sleep 1

if pgrep -f "$FITTER_PATTERN" >/dev/null 2>&1; then
    pkill -KILL -f "$FITTER_PATTERN" >/dev/null 2>&1 || true
    sleep 1
fi

if pgrep -f "$FITTER_PATTERN" >/dev/null 2>&1; then
    echo "[FAIL] online fitter survived shutdown" >&2
    pgrep -af "$FITTER_PATTERN" >&2 || true
    exit 1
fi

ssh "$REMOTE" "screen -S genesis_ssrf_wgen -X quit >/dev/null 2>&1 || true; pkill -TERM -f '[p]ython -u master_ioc.py -s configs/ssrf_physical/keysight_wavegen -i keysight_wavegen' >/dev/null 2>&1 || true"
sleep 2

echo "[PASS] Final ssRF software stack stopped."
echo "[CHECK] Direct physical safe baseline:"
"$ROOT/scripts/check_ssrf_physical_readonly.sh"
