#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec "$ROOT/runtime/trenvx-venv/bin/python" "$ROOT/scripts/smoke_trenvx_service.py" "$@"
