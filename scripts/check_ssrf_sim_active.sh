#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

cd "${ROOT}"

PROFILE="${ROOT}/configs/commissioning/ssrf_sim_active"

echo
echo "============================================================"
echo " Genesis ssRF Simulator Active Guard"
echo "============================================================"

echo
echo "===== PROFILE ====="

python tools/validate_ssrf_sim_active.py \
    --profile "${PROFILE}"

echo
echo "===== SIMULATOR ENDPOINTS ====="

python - <<'PY'
import socket


ENDPOINTS = (
    ("SMC100A", "127.0.0.1", 5025),
    ("Keysight WaveGen", "127.0.0.1", 5027),
    ("NMR switch", "127.0.0.1", 8081),
    ("ssRF switch", "127.0.0.1", 8082),
)

failed = []

for name, host, port in ENDPOINTS:
    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    sock.settimeout(0.5)

    try:
        sock.connect(
            (host, port)
        )

    except OSError as exc:
        failed.append(
            (
                name,
                host,
                port,
                str(exc),
            )
        )

        print(
            f"[FAIL] {name}: "
            f"{host}:{port}"
        )

    else:
        print(
            f"[OK] {name}: "
            f"{host}:{port}"
        )

    finally:
        sock.close()

if failed:
    raise SystemExit(2)
PY

echo
echo "===== CONFLICTING GENESIS SESSIONS ====="

CONFLICTS=(
    "ioc-manager"
    "archiver"
    "ssrf_smc100a"
    "ssrf_keysight_wavegen"
    "ssrf_nmr_switch"
    "ssrf_rf_switch"
    "ssrf_interlock"
    "ssrf_sequencer"
    "ssrf-sim-manager"
    "sim_ssrf_smc100a"
    "sim_ssrf_keysight_wavegen"
    "sim_ssrf_nmr_switch"
    "sim_ssrf_rf_switch"
    "sim_ssrf_interlock"
    "sim_ssrf_sequencer"
)

SCREEN_LIST="$(
    screen -ls 2>/dev/null || true
)"

FOUND=0

for name in "${CONFLICTS[@]}"; do
    if printf '%s\n' "${SCREEN_LIST}" \
        | grep -Eq \
        "[.]${name}[[:space:]]"
    then
        echo "[BLOCKED] ${name}"
        FOUND=1
    fi
done

if [[ "${FOUND}" -ne 0 ]]; then
    echo
    echo "Simulator-active startup is blocked."
    echo "Conflicting Genesis sessions are still running."
    echo
    echo "No process was stopped."
    echo "No IOC was started."
    echo "No EPICS write was performed."
    exit 3
fi

echo "[OK] no conflicting Genesis sessions"

echo
echo "============================================================"
echo " Simulator-active runtime guard PASSED"
echo "============================================================"
echo
echo "No IOC was started."
echo "No EPICS write was performed."
