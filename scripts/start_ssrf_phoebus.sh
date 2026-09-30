#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$ROOT/scripts/start_phoebus_instance.sh" \
    "ssRF" \
    "$ROOT/configs/phoebus/ssrf_physical.ini" \
    "$ROOT/gui/genesis_ssrf.bob" \
    "/tmp/genesis_ssrf_phoebus.log"
