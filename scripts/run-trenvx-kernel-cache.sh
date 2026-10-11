#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=110s 600s \
    bash "$ROOT/scripts/run-trenvx-paused-cache.sh" --kernel-file-types "$@"
