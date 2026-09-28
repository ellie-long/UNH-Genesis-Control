#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
REMOTE="${GENESIS_KEYSIGHT_HOST:-genesis-keysight}"

session_exists() {
    local name="$1"
    local listing

    listing="$(screen -ls 2>&1 || true)"

    grep -E "\\.${name}[[:space:]]" <<< "${listing}" >/dev/null
}

CA_LIST="127.0.0.1:5068 192.168.1.24:5064 127.0.0.1:5071 127.0.0.1:5072 127.0.0.1:5073 127.0.0.1:5074"

if session_exists genesis-ssrf-sequencer; then
    echo "[INFO] Requesting fail-safe ABORT before shutdown."
    env EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST="$CA_LIST" EPICS_CA_MAX_ARRAY_BYTES=262144 python - <<'PY'
import asyncio, time
from aioca import caput, caget
async def main():
    await caput("UNH:GEN:SSRF:SEQ:ABORT_CMD", 1, wait=True, timeout=3)
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        rf=int(await caget("UNH:GEN:SSRF:GEN:RF_RBV", timeout=2))
        fm=int(await caget("UNH:GEN:SSRF:GEN:FM_RBV", timeout=2))
        wg=int(await caget("UNH:GEN:SSRF:WGEN:OUTPUT_RBV", timeout=2))
        sr=int(await caget("UNH:GEN:SSRF:RF_SW:MASK_RBV", timeout=2))
        nm=int(await caget("UNH:GEN:SSRF:NMR_SW:MASK_RBV", timeout=2))
        if (rf,fm,wg,sr,nm)==(0,0,0,3,0):
            print("[PASS] safe baseline confirmed")
            return
        await asyncio.sleep(0.2)
    raise SystemExit("safe baseline not confirmed; refusing to stop IOCs")
asyncio.run(main())
PY
fi

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
