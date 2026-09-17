#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

python - <<'PY'
from tools.ioc_cli import start_manager
print(start_manager())
PY
