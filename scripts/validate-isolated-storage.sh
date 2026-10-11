#!/usr/bin/env bash
# One owned temporary storage validation with a bounded runtime.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout -k 120s 900s python3 -B "$ROOT/scripts/validate_isolated_agentenv.py" "$@"
