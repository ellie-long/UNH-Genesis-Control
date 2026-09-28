#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
REMOTE="${GENESIS_KEYSIGHT_HOST:-genesis-keysight}"
REMOTE_ROOT="${GENESIS_KEYSIGHT_ROOT:-/home/unhpoltarg/genesis_keysight/runtime}"

echo "============================================================"
echo " DEPLOY FINAL SSRF KEYSIGHT RUNTIME (NO HARDWARE START)"
echo "============================================================"

ssh "$REMOTE" "mkdir -p '$REMOTE_ROOT/configs/ssrf_physical/keysight_wavegen'"
rsync -az master_ioc.py "$REMOTE:$REMOTE_ROOT/master_ioc.py"
rsync -az genesis_common/ "$REMOTE:$REMOTE_ROOT/genesis_common/"
rsync -az ssrf_ioc/ "$REMOTE:$REMOTE_ROOT/ssrf_ioc/"
rsync -az configs/access_security/ "$REMOTE:$REMOTE_ROOT/configs/access_security/"
rsync -az configs/ssrf_physical/keysight_wavegen/ "$REMOTE:$REMOTE_ROOT/configs/ssrf_physical/keysight_wavegen/"

ssh "$REMOTE" "bash -lc 'source /home/unhpoltarg/genesis_keysight/.venv/bin/activate && cd $REMOTE_ROOT && python -m py_compile master_ioc.py ssrf_ioc/keysight_wavegen.py genesis_common/*.py'"

echo "[PASS] Keysight runtime deployed and compile-checked."
echo "[INFO] No IOC was started and no hardware command was sent."
