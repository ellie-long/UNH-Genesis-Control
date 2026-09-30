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

PHOEBUS="${PHOEBUS_BIN:-/Applications/CSS_Phoebus.app/Contents/MacOS/main.command}"

[[ -x "$PHOEBUS" ]] || {
    echo "ERROR: Phoebus launcher not found:"
    echo "  $PHOEBUS"
    exit 1
}

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
