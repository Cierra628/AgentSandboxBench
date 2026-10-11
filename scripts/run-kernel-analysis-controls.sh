#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case "${1:-}" in
    agentenv|trenvx) backend=$1 ;;
    *) echo 'usage: bash scripts/run-kernel-analysis-controls.sh agentenv|trenvx' >&2; exit 2 ;;
esac
exec python3 -B "$ROOT/scripts/run_whole_analysis_controls.py" --backend "$backend" --kernel-protocol
