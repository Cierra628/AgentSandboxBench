#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=15s 115s python3 -B "$ROOT/scripts/host_existing_cache_owner.py" "$@"
