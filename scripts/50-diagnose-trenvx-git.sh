#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec bash "$ROOT/scripts/45-replay-trenvx-exp1.sh" \
  --discard-success-images --record-phases --runner-phases --git-wait-diagnostic \
  --reference "$ROOT/.artifacts/trenvx-service-smoke/20261001T134743Z-3005780" \
  --official-task "$ROOT/.artifacts/v1-closeout-inputs/official-task" "$@"
