#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../../.."
record=$(mktemp -d "$PWD/.artifacts/variance-three-XXXXXXXX")
echo "evidence=$record"
for i in 1 2 3; do
    free=$(df -B1 --output=avail . | tail -1)
    echo "$i before $free" | tee -a "$record/space.txt"
    ((free>90*1024**3)) || exit 1
    rc=0
    bash scripts/observe_paused_process.sh variance-one >"$record/$i.log" 2>&1 || rc=$?
    echo "$i after $(df -B1 --output=avail .|tail -1) exit=$rc" | tee -a "$record/space.txt"
    cat "$record/$i.log"
    ((rc==0)) || exit "$rc"
done
