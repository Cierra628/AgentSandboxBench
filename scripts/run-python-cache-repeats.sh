#!/usr/bin/env bash
# Fixed 36-run comparison; uses the existing isolated task entries.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 -B "$ROOT/scripts/run_python_cache_repeats.py" "$@"
