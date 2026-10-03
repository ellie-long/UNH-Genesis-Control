#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "Usage:"
    echo "  $0 <name> <settings.ini> <display.bob> <logfile>"
    exit 2
fi

NAME="$1"
SETTINGS="$2"
RESOURCE="$3"
LOGFILE="$4"

resolve_executable()
{
    local candidate="$1"

    if [[ "$candidate" == */* ]]; then
        [[ -x "$candidate" ]] || return 1
        printf "%s\\n" "$candidate"
        return 0
    fi

    command -v "$candidate" 2>/dev/null
}

resolve_phoebus()
{
    local candidate

    if [[ -n "${PHOEBUS_BIN:-}" ]]; then
        resolve_executable "$PHOEBUS_BIN"
        return
    fi

    if [[ "$(uname -s)" == "Darwin" ]]; then
        candidate="/Applications/CSS_Phoebus.app/Contents/MacOS/main.command"
        if [[ -x "$candidate" ]]; then
            printf "%s\\n" "$candidate"
            return 0
        fi
    fi

    for candidate in phoebus phoebus.sh; do
        if resolve_executable "$candidate"; then
            return 0
        fi
    done

    return 1
}

if ! PHOEBUS="$(resolve_phoebus)"; then
    echo "ERROR: Phoebus launcher not found."
    echo "Set PHOEBUS_BIN to the Phoebus launcher path or add phoebus/phoebus.sh to PATH."
    exit 1
fi

[[ -f "$SETTINGS" ]] || {
    echo "ERROR: Phoebus settings file not found:"
    echo "  $SETTINGS"
    exit 1
}

[[ -f "$RESOURCE" ]] || {
    echo "ERROR: Phoebus display not found:"
    echo "  $RESOURCE"
    exit 1
}

SETTINGS="$(cd "$(dirname "$SETTINGS")" && pwd)/$(basename "$SETTINGS")"
RESOURCE="$(cd "$(dirname "$RESOURCE")" && pwd)/$(basename "$RESOURCE")"

phoebus_running()
{
    ps axww -o command= \
        | grep -F -- "-settings $SETTINGS" \
        | grep -F -- "-resource $RESOURCE" \
        | grep -v grep \
        >/dev/null 2>&1
}

if phoebus_running; then
    echo "[OK] $NAME Phoebus already running"
    echo "     settings: $SETTINGS"
    echo "     display : $RESOURCE"
    exit 0
fi

echo "[START] Independent $NAME Phoebus"
echo "        settings: $SETTINGS"
echo "        display : $RESOURCE"

nohup "$PHOEBUS" \
    -settings "$SETTINGS" \
    -resource "$RESOURCE" \
    >"$LOGFILE" 2>&1 </dev/null &

disown 2>/dev/null || true

for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1

    if phoebus_running; then
        echo "[PASS] $NAME Phoebus running independently"
        exit 0
    fi
done

echo "[FAIL] $NAME Phoebus did not remain running"
echo "===== LOG ====="
tail -100 "$LOGFILE" 2>/dev/null || true
exit 1
