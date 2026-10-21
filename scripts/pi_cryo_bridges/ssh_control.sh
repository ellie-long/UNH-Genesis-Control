#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/unhpoltarg/genesis_cryo_bridges"
PYTHON="$ROOT/env/bin/python"

FLOW_SESSION="genesis-cryo-flow-right"
MAN_SESSION="genesis-cryo-manometer-low"

FLOW_DEVICE="/dev/serial/by-id/usb-Microchip_Technology_Inc._Teledyne_Hastings_Digital-if00"

session_exists() {
    screen -ls 2>/dev/null |
        grep -Eq "[0-9]+\\.$1[[:space:]]"
}

port_listening() {
    [[ -n "$(ss -H -ltn "( sport = :$1 )")" ]]
}

start_one() {
    local name="$1"
    local port="$2"
    local session="$3"

    if session_exists "$session"; then
        if port_listening "$port"; then
            echo "[UP] $name ($port)"
            return 0
        fi

        echo "[FAIL] $name session exists but port $port is not listening" >&2
        return 1
    fi

    if port_listening "$port"; then
        echo "[FAIL] Port $port already occupied by another process" >&2
        return 1
    fi

    case "$name" in
        flow-right)
            screen -dmS "$session" \
                bash -c '
                    cd "$1" || exit 1
                    exec "$2" -u flowmeter_pi_headless.py \
                        --port "$3" \
                        --baud 19200 \
                        --tcp-port 5010 \
                        >> logs/flowmeter_right.log 2>&1
                ' bash "$ROOT" "$PYTHON" "$FLOW_DEVICE"
            ;;

        manometer-low)
            screen -dmS "$session" \
                bash -c '
                    cd "$1" || exit 1
                    exec "$2" -u pi_gateway.py \
                        --serial /dev/ttyUSB0 \
                        --baud 115200 \
                        --listen 5030 \
                        >> logs/manometer_low.log 2>&1
                ' bash "$ROOT" "$PYTHON"
            ;;
    esac

    for ((i=0; i<8; i++)); do
        if session_exists "$session" && port_listening "$port"; then
            echo "[STARTED] $name ($port)"
            return 0
        fi
        sleep 1
    done

    echo "[FAIL] $name did not become ready" >&2
    return 1
}

stop_one() {
    local name="$1"
    local session="$2"

    if session_exists "$session"; then
        screen -S "$session" -X quit
        echo "[STOP REQUESTED] $name"

        for ((i=0; i<8; i++)); do
            if ! session_exists "$session"; then
                echo "[STOPPED] $name"
                return 0
            fi
            sleep 1
        done

        echo "[FAIL] $name session remains active" >&2
        return 1
    else
        echo "[DOWN] $name"
    fi
}

status_all() {
    echo "===== GENESIS CRYO RASPBERRY PI ====="
    hostname

    for item in \
        "flow-right:5010:$FLOW_SESSION" \
        "manometer-low:5030:$MAN_SESSION"
    do
        IFS=: read -r name port session <<< "$item"

        if session_exists "$session" && port_listening "$port"; then
            echo "[UP] $name TCP $port"
        elif session_exists "$session"; then
            echo "[DEGRADED] $name session running, TCP $port unavailable"
        elif port_listening "$port"; then
            echo "[CONFLICT] $name TCP $port occupied by unmanaged process"
        else
            echo "[DOWN] $name"
        fi
    done

    echo "===== SERIAL DEVICES ====="
    ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
}

case "${SSH_ORIGINAL_COMMAND:-}" in
    status)
        status_all
        ;;

    start)
        [[ -x "$PYTHON" ]] || {
            echo "[FAIL] Genesis Cryo Python environment missing" >&2
            exit 1
        }

        failures=0

        start_one flow-right 5010 "$FLOW_SESSION" ||
            failures=$((failures + 1))

        start_one manometer-low 5030 "$MAN_SESSION" ||
            failures=$((failures + 1))

        status_all

        if (( failures > 0 )); then
            echo "[FAIL] $failures Genesis Cryo bridge(s) failed" >&2
            exit 1
        fi
        ;;

    stop)
        failures=0

        stop_one manometer-low "$MAN_SESSION" ||
            failures=$((failures + 1))

        stop_one flow-right "$FLOW_SESSION" ||
            failures=$((failures + 1))

        (( failures == 0 ))
        ;;

    *)
        echo "[DENIED] Unsupported Genesis Cryo operation" >&2
        exit 126
        ;;
esac
