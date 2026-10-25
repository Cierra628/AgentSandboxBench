#!/usr/bin/env bash
# Copy a frozen allowlist only. No Git or service operations.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 -B "$ROOT/scripts/prepare_release.py" "$@"
