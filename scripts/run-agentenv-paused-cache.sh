#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=110s 600s \
    python3 -B "$ROOT/scripts/smoke_agentenv_whole_cache.py" --exp1 --paused-boundary
