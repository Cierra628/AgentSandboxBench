#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "$root/scripts/run_exp1_monitor_series.py" "$root/configs/exp1-monitor-series.json"
