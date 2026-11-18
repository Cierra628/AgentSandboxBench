#!/usr/bin/env bash
# One tiny default/GET_DATA device comparison, without VM or existing services.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=$(realpath "${1:?usage: run-provider-slot-protocol.sh BUILD_DIR NEW_RESULT_DIR}")
OUT=$(realpath -m "${2:?new result directory required}")
test "$(id -u)" -eq 0
test ! -e "$OUT"
test -x "$BUILD/provider-slot-protocol"
ulimit -l unlimited
printf -v command '/usr/bin/setpriv --reuid=%s --regid=%s --groups=%s --inh-caps=+sys_admin --ambient-caps=+sys_admin --nnp %s %s' \
  "$(id -u yyxie)" "$(id -g yyxie)" "$(id -g yyxie),$(getent group kvm | cut -d: -f3)" \
  "$BUILD/provider-slot-protocol" "$OUT"
timeout --signal=INT --kill-after=15s 90s bpftrace -B none \
  "$ROOT/scripts/provider_slot_copy_order.bt" -c "$command"
test "$(cat "$OUT/status")" = PASS
