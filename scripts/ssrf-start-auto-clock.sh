#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PI="unhpoltarg@192.168.1.24"

echo "================================================"
echo " GENESIS SSRF — WSL AUTHORITATIVE CLOCK"
echo "================================================"

if [[ "$(id -un)" != "utsav" ]]; then
    echo "[FAIL] Run as utsav"
    exit 1
fi

if [[ "${CONDA_DEFAULT_ENV:-}" != "genesis-p1" ]]; then
    echo "[FAIL] Activate genesis-p1"
    exit 1
fi

SOCKDIR="$(mktemp -d)"
SOCK="$SOCKDIR/pi.sock"

cleanup() {
    ssh -S "$SOCK" -O exit "$PI" >/dev/null 2>&1 || true
    rm -rf "$SOCKDIR"
}
trap cleanup EXIT

echo
echo "===== CONNECT KEYSIGHT PI ====="

ssh -M -S "$SOCK" \
    -o ControlPersist=120 \
    -o ConnectTimeout=10 \
    -fN "$PI"

echo
echo "===== DISABLE PI AUTOMATIC TIME CORRECTION ====="

# WSL is the authoritative clock for this configuration.
# Disable Pi NTP so it cannot reverse the synchronization.
ssh -tt -S "$SOCK" "$PI" \
    'sudo timedatectl set-ntp false &&
     echo "Pi NTP configuration:" &&
     timedatectl show -p NTP -p NTPSynchronized'

echo
echo "===== SYNCHRONIZE PI CLOCK TO WSL ====="

# Never adjust the Ubuntu/WSL clock.
WSL_EPOCH="$(date -u +%s)"

ssh -tt -S "$SOCK" "$PI" \
    "sudo date -u -s '@${WSL_EPOCH}'"

echo
echo "===== VERIFY CLOCK STABILITY ====="

python - "$SOCK" <<'PY'
import subprocess
import sys
import time

sock = sys.argv[1]
remote = "unhpoltarg@192.168.1.24"

def measure():
    before = time.time_ns()

    result = subprocess.run(
        [
            "ssh", "-S", sock,
            "-o", "BatchMode=yes",
            remote, "date +%s%N"
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    after = time.time_ns()
    pi = int(result.stdout.strip())

    rtt = (after - before) / 1e9
    skew = ((before + after) / 2 - pi) / 1e9

    return skew, rtt

for i in range(1, 4):
    skew, rtt = measure()

    print(
        f"[CHECK {i}] "
        f"WSL - Pi = {skew:+.3f} s; "
        f"RTT = {rtt:.3f} s",
        flush=True,
    )

    if rtt > 2 or abs(skew) > 2:
        raise SystemExit(
            "[FAIL] Clock difference or latency too high. "
            "Refusing to start ssRF."
        )

    if i < 3:
        time.sleep(3)

print("[PASS] Pi clock follows WSL and remains stable")
PY

echo
echo "===== START SSRF OPERATOR MODE ====="

./scripts/genesisctl.sh ssrf-start --burn-mode operator
