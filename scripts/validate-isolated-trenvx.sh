#!/usr/bin/env bash
# One owned temporary storage validation; explicit confirmed window required.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout -k 120s 900s "$ROOT/runtime/trenvx-venv/bin/python" -B "$ROOT/scripts/validate_isolated_trenvx.py" "$@"
