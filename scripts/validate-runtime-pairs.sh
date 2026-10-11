#!/usr/bin/env bash
# Five pairs by default, alternating which monitoring mode runs first.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
exec python3 -B scripts/run_runtime_monitor_pairs.py "$@"
