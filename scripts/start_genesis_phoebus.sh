#!/usr/bin/env bash
set -euo pipefail

# Genesis single-instance Phoebus launcher.
# Usage:
#   ./scripts/start_genesis_phoebus.sh SETTINGS_FILE RESOURCE_FILE
#
# Startup is intentionally two-stage:
#   1. Start one clean Phoebus server with the requested settings.
#   2. Forward the requested .bob resource to that same server.
#
# The second invocation deliberately does NOT repeat -settings.
# This is the sequence validated manually on the Genesis Mac.

PHOEBUS_BIN="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
PHOEBUS_SERVER_PORT="${GENESIS_PHOEBUS_SERVER_PORT:-4918}"

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 SETTINGS_FILE RESOURCE_FILE" >&2
    exit 2
fi

SETTINGS_FILE="$1"
RESOURCE_FILE="$2"

[[ -x "${PHOEBUS_BIN}" ]] || {
    echo "ERROR: Phoebus launcher not found/executable:" >&2
    echo "  ${PHOEBUS_BIN}" >&2
    exit 1
}

[[ -f "${SETTINGS_FILE}" ]] || {
    echo "ERROR: settings file not found:" >&2
    echo "  ${SETTINGS_FILE}" >&2
    exit 1
}

[[ -f "${RESOURCE_FILE}" ]] || {
    echo "ERROR: resource file not found:" >&2
    echo "  ${RESOURCE_FILE}" >&2
    exit 1
}

phoebus_process_running() {
    pgrep -f '/Applications/CSS_Phoebus.app/.*product-.*\.jar' >/dev/null 2>&1
}

server_listening() {
    lsof -nP -iTCP:"${PHOEBUS_SERVER_PORT}" -sTCP:LISTEN 2>/dev/null \
        | grep -q '127\.0\.0\.1'
}

if server_listening; then
    echo "[INFO] Reusing Genesis Phoebus server on port ${PHOEBUS_SERVER_PORT}"
elif phoebus_process_running; then
    echo "ERROR: Phoebus is already running but not as the Genesis server." >&2
    echo "Close the existing Phoebus instance, then rerun." >&2
    exit 1
else
    echo "[START] Clean Genesis Phoebus server on port ${PHOEBUS_SERVER_PORT}"
    : > /tmp/genesis_phoebus.log

    "${PHOEBUS_BIN}" \
        -server "${PHOEBUS_SERVER_PORT}" \
        -settings "${SETTINGS_FILE}" \
        -clean \
        >>/tmp/genesis_phoebus.log 2>&1 &

    ready=0
    for _ in $(seq 1 30); do
        if server_listening; then
            ready=1
            break
        fi
        sleep 0.5
    done

    if [[ "${ready}" -ne 1 ]]; then
        echo "ERROR: Genesis Phoebus server did not become ready." >&2
        echo "See /tmp/genesis_phoebus.log" >&2
        exit 1
    fi
fi

echo "[OPEN] ${RESOURCE_FILE}"

"${PHOEBUS_BIN}" \
    -server "${PHOEBUS_SERVER_PORT}" \
    -resource "${RESOURCE_FILE}"

echo "[PASS] Resource opened in Genesis Phoebus"
