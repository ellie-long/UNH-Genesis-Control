#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$ROOT/scripts/start_phoebus_instance.sh" \
    "Microwave" \
    "$ROOT/configs/phoebus/genesis_mac_settings.ini" \
    "$ROOT/gui/genesis_microwave.bob" \
    "/tmp/genesis_microwave_phoebus.log"
