#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec timeout --signal=TERM --kill-after=90s 600 python3 "$root/scripts/closeout_exp1.py"
