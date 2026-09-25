#!/bin/bash

set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
    pwd
)"

PORT="${GENESIS_ARCHIVE_VIEWER_PORT:-8501}"

cd "$ROOT/archive"

echo "Starting Genesis Archive Viewer..."
echo "PV telemetry + ssRF session visualization"
echo "URL: http://127.0.0.1:${PORT}"
echo "Press Ctrl+C to stop"
echo

PYTHONPATH="$ROOT" \
python -m streamlit run \
    archive_viewer.py \
    --server.port "$PORT" \
    --server.address 127.0.0.1 \
    --server.headless true \
    --browser.gatherUsageStats false
