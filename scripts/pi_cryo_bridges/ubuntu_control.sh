#!/usr/bin/env bash
set -euo pipefail

OPERATION="${1:-}"

case "$OPERATION" in
    start|status|stop) ;;
    *)
        echo "Usage: $0 {start|status|stop}" >&2
        exit 2
        ;;
esac

KEY="$HOME/.ssh/genesis_cryo_pi_ed25519"

if [[ ! -r "$KEY" ]]; then
    echo "[FAIL] Genesis Cryo Pi SSH identity missing: $KEY" >&2
    exit 1
fi

exec ssh -T \
    -i "$KEY" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    -o ConnectTimeout=5 \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=2 \
    unhpoltarg@192.168.1.24 \
    "$OPERATION"
