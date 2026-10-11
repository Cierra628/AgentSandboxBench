#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:-fixture}
case "$mode" in
    fixture) workload=() ;;
    task) workload=(--exp1) ;;
    *) echo 'usage: bash scripts/run-agentenv-file-inventory.sh fixture|task' >&2; exit 2 ;;
esac
exec timeout --signal=TERM --kill-after=110s 600s \
    python3 -B "$ROOT/scripts/smoke_agentenv_whole_cache.py" \
    "${workload[@]}" --paused-boundary --file-inventory "$mode"
