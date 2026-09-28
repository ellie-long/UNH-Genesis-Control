#!/usr/bin/env bash
set -euo pipefail

# Genesis single-instance Phoebus launcher.
#
# Usage:
#   ./scripts/start_genesis_phoebus.sh SETTINGS_FILE RESOURCE_FILE
#
# Phoebus -clean intentionally ignores -resource. Startup therefore uses
# a two-stage sequence: start a clean server, then forward the resource.
# A fresh process can listen on TCP 4918 before its resource handler is ready;
# the settle/retry logic below handles that observed startup race.

PHOEBUS_BIN="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
PHOEBUS_SERVER_PORT="${GENESIS_PHOEBUS_SERVER_PORT:-4918}"
PHOEBUS_LOG="${GENESIS_PHOEBUS_LOG:-/tmp/genesis_phoebus.log}"
PHOEBUS_CLIENT_LOG="${GENESIS_PHOEBUS_CLIENT_LOG:-/tmp/genesis_phoebus_client.log}"
PHOEBUS_UI_SETTLE_SECONDS="${GENESIS_PHOEBUS_UI_SETTLE_SECONDS:-8}"
PHOEBUS_RESOURCE_RETRIES="${GENESIS_PHOEBUS_RESOURCE_RETRIES:-5}"
PHOEBUS_RESOURCE_RETRY_SECONDS="${GENESIS_PHOEBUS_RESOURCE_RETRY_SECONDS:-2}"

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
    echo "ERROR: settings file not found: ${SETTINGS_FILE}" >&2
    exit 1
}
[[ -f "${RESOURCE_FILE}" ]] || {
    echo "ERROR: resource file not found: ${RESOURCE_FILE}" >&2
    exit 1
}

phoebus_process_running() {
    pgrep -f '/Applications/CSS_Phoebus.app/.*product-.*\.jar' >/dev/null 2>&1
}

server_listening() {
    lsof -nP -iTCP:"${PHOEBUS_SERVER_PORT}" -sTCP:LISTEN 2>/dev/null \
        | grep -q '127\.0\.0\.1'
}

start_clean_server() {
    echo "[START] Clean Genesis Phoebus server on port ${PHOEBUS_SERVER_PORT}"
    : > "${PHOEBUS_LOG}"
    : > "${PHOEBUS_CLIENT_LOG}"

    "${PHOEBUS_BIN}" \
        -server "${PHOEBUS_SERVER_PORT}" \
        -settings "${SETTINGS_FILE}" \
        -clean \
        >>"${PHOEBUS_LOG}" 2>&1 &

    local ready=0
    for _ in $(seq 1 40); do
        if server_listening; then
            ready=1
            break
        fi
        sleep 0.5
    done

    if [[ "${ready}" -ne 1 ]]; then
        echo "ERROR: Genesis Phoebus server did not open port ${PHOEBUS_SERVER_PORT}." >&2
        echo "See ${PHOEBUS_LOG}" >&2
        return 1
    fi

    echo "[WAIT] Phoebus UI initialization (${PHOEBUS_UI_SETTLE_SECONDS}s)"
    sleep "${PHOEBUS_UI_SETTLE_SECONDS}"
}

forward_resource_once() {
    local before=0
    local new_log

    if [[ -f "${PHOEBUS_LOG}" ]]; then
        before=$(wc -c < "${PHOEBUS_LOG}" | tr -d ' ')
    fi

    "${PHOEBUS_BIN}" \
        -server "${PHOEBUS_SERVER_PORT}" \
        -resource "${RESOURCE_FILE}" \
        >>"${PHOEBUS_CLIENT_LOG}" 2>&1

    sleep 1

    if [[ -f "${PHOEBUS_LOG}" ]]; then
        new_log="$(tail -c "+$((before + 1))" "${PHOEBUS_LOG}" 2>/dev/null || true)"
        if grep -Fq 'No argument handler installed to handle [-resource' <<<"${new_log}"; then
            return 2
        fi
    fi
    return 0
}

if server_listening; then
    echo "[INFO] Reusing Genesis Phoebus server on port ${PHOEBUS_SERVER_PORT}"
elif phoebus_process_running; then
    echo "ERROR: Phoebus is already running but not as the Genesis server." >&2
    echo "Close the existing Phoebus instance, then rerun." >&2
    exit 1
else
    start_clean_server
fi

echo "[OPEN] ${RESOURCE_FILE}"
opened=0
for attempt in $(seq 1 "${PHOEBUS_RESOURCE_RETRIES}"); do
    if forward_resource_once; then
        opened=1
        break
    else
        rc=$?
        if [[ "${rc}" -eq 2 ]]; then
            echo "[WAIT] Phoebus resource handler not ready; retry ${attempt}/${PHOEBUS_RESOURCE_RETRIES}"
            sleep "${PHOEBUS_RESOURCE_RETRY_SECONDS}"
            continue
        fi
        echo "ERROR: Phoebus resource forward failed on attempt ${attempt}." >&2
        echo "See ${PHOEBUS_CLIENT_LOG}" >&2
        exit 1
    fi
done

if [[ "${opened}" -ne 1 ]]; then
    echo "ERROR: Phoebus server is running, but the resource handler never became ready." >&2
    echo "Server log: ${PHOEBUS_LOG}" >&2
    echo "Client log: ${PHOEBUS_CLIENT_LOG}" >&2
    exit 1
fi

echo "[PASS] Resource opened in Genesis Phoebus"
