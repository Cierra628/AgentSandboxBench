#!/usr/bin/env bash
# Execute the pinned upstream Python example against this experiment's server.
set -Eeuo pipefail
[[ $# -eq 1 ]] || { echo 'usage: bash scripts/10-run-official-python-example.sh SERVER_RESULT_DIR'; exit 2; }
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec "$ROOT/runtime/e2b-venv/bin/python" "$ROOT/scripts/official_python_example.py" "$1"
