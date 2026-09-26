#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "$root/scripts/calibrate_snapshot_siblings.py" "${1:-1}"
