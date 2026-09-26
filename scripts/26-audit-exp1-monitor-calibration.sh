#!/usr/bin/env bash
set -Eeuo pipefail
if (($# != 1)); then
  echo "usage: $0 CALIBRATION_SERIES_DIR" >&2
  exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "$root/scripts/audit_exp1_monitor_calibration.py" "$1"
