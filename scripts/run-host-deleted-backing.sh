#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=2s 20s python3 -B "$ROOT/scripts/validate_host_deleted_backing.py" "$@"
