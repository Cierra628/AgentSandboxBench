#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 -B "$ROOT/scripts/summarize_local_no_proxy_pairs.py" "$@"
