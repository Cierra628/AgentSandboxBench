#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export ONLINE_CH_QUIESCE=1 ONLINE_CH_OVERLAY=1 ONLINE_CH_CGROUP=1
exec python3 "$root/scripts/check_online_ch_upper.py"
