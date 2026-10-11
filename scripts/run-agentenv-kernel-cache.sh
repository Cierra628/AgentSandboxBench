#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case "${1:-fixture}" in
    fixture) workload=() ;;
    task) workload=(--exp1) ;;
    *) echo 'usage: bash scripts/run-agentenv-kernel-cache.sh fixture|task' >&2; exit 2 ;;
esac
exec timeout --signal=TERM --kill-after=110s 600s \
    python3 -B "$ROOT/scripts/smoke_agentenv_whole_cache.py" "${workload[@]}" \
    --paused-boundary --file-inventory fixture --kernel-file-types
