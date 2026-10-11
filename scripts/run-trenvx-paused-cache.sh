#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=110s 600s \
    bash "$ROOT/scripts/45-replay-trenvx-exp1.sh" \
    --record-phases --fixed-git-identity --guest-collector persistent \
    --guest-memory-sampling --guest-sample-interval .1 --host-sample-interval .1 \
    --file-origin-diagnostic --guest-file-physical-diagnostic --base-file-physical-diagnostic \
    --monitoring-acceptance --reference "$ROOT/.artifacts/trenvx-service-smoke/20261001T134743Z-3005780" \
    --official-task "$ROOT/.artifacts/v1-closeout-inputs/official-task" \
    --discard-success-images --whole-cache-diagnostic --paused-cache-boundary "$@"
