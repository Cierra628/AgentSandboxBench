#!/usr/bin/env bash
# Only inactive diagnostic copies; no VM or original image repair.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout -k 60s 240s python3 -B "$ROOT/scripts/diagnose_ext4_journal.py" "$@"
