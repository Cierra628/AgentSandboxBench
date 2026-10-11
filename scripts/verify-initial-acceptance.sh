#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout 240s python3 -B "$ROOT/scripts/verify_initial_acceptance.py" "$@"
