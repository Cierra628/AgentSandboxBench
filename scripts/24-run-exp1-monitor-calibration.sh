#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case ${1:-} in
  smoke) config="$root/configs/exp1-monitor-calibration-smoke.json" ;;
  full) config="$root/configs/exp1-monitor-calibration.json" ;;
  *) echo "usage: $0 smoke|full" >&2; exit 2 ;;
esac
exec python3 "$root/scripts/run_exp1_monitor_calibration.py" "$config"
