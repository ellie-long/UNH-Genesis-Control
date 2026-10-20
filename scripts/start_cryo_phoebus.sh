#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

exec "$ROOT/scripts/start_phoebus_instance.sh" \
    "Cryogenic Target" \
    "$ROOT/configs/phoebus/target_physical.ini" \
    "$ROOT/gui/genesis_cryo.bob" \
    "/tmp/genesis_cryo_phoebus.log" \
    "4920"
