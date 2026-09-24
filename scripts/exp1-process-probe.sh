#!/usr/bin/env bash
# A running guest process distinguishes VM-state restore from file-only inheritance.
set -euo pipefail
dir=/workspace/exp1-process-probe
mkdir -p "$dir"
if [[ ${1:-} == start ]]; then
  printf '0\n' > "$dir/count"
  nohup bash -c 'while :; do n=$(cat /workspace/exp1-process-probe/count); printf "%s\n" "$((n+1))" > /workspace/exp1-process-probe/count; sleep 0.1; done' </dev/null >/dev/null 2>&1 &
  printf '%s\n' "$!" > "$dir/pid"
  sleep 0.3
fi
pid=$(cat "$dir/pid")
test -r "/proc/$pid/stat"
printf 'pid=%s\n' "$pid"
printf 'start_ticks=%s\n' "$(awk '{print $22}' "/proc/$pid/stat")"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'count=%s\n' "$(cat "$dir/count")"
