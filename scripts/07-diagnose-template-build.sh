#!/usr/bin/env bash
# Collect server-side evidence for a failed AgentENV template build.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $# -ne 2 ]]; then
  echo 'usage: sudo bash scripts/07-diagnose-template-build.sh SERVER_RESULT_DIR TEMPLATE_RESULT_DIR'
  exit 2
fi
SERVER_RESULT=$(realpath "$1")
TEMPLATE_RESULT=$(realpath "$2")
RESULT="$ROOT/.artifacts/template-build-diagnostic-$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUNTIME="$ROOT/runtime"
CONFIG_DIR="$RUNTIME/config"
CLI="$RUNTIME/bin/aenv"
umask 077
mkdir -p "$RESULT"
stage=preflight
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if ((rc == 0)); then status=PASS; fi
  printf 'status=%s stage=%s rc=%s\n' "$status" "$stage" "$rc" > "$RESULT/result.txt"
  if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    chown -R "$SUDO_UID:$SUDO_GID" "$RESULT" || {
      status=FAIL
      stage=result-ownership
      rc=1
    }
  fi
  printf 'status=%s stage=%s result_dir=%s\n' "$status" "$stage" "$RESULT"
  exit "$rc"
}
trap 'exit 143' INT TERM HUP
trap finish EXIT

run() {
  [[ $EUID -eq 0 ]] || { echo 'Run with sudo bash.'; return 1; }
  [[ -f "$SERVER_RESULT/result.txt" && -f "$SERVER_RESULT/container-id.txt" ]]
  [[ -f "$TEMPLATE_RESULT/result.txt" && -f "$TEMPLATE_RESULT/template-watch.log" ]]
  grep -qx 'status=PASS stage=server-ready rc=0' "$SERVER_RESULT/result.txt"
  local cid
  cid=$(<"$SERVER_RESULT/container-id.txt")
  [[ "$cid" =~ ^[a-f0-9]{64}$ ]]
  timeout -k 3s 10s docker inspect -f '{{.State.Running}}' "$cid" | grep -qx true

  stage=collect-template-result
  cp -f "$TEMPLATE_RESULT/result.txt" "$RESULT/template-result.txt"
  cp -f "$TEMPLATE_RESULT/template-create.log" "$RESULT/template-create.log" 2>/dev/null || true
  cp -f "$TEMPLATE_RESULT/template-watch.log" "$RESULT/template-watch.log"

  stage=collect-server-container
  timeout -k 3s 30s docker inspect "$cid" > "$RESULT/server-container.json"
  timeout -k 3s 30s docker logs --tail 4000 "$cid" > "$RESULT/server-container.log" 2>&1 || true

  stage=collect-server-env-logs
  timeout -k 3s 30s docker exec "$cid" sh -c '
    find /workspace/env/logs -maxdepth 2 -type f -print 2>/dev/null || true
    for f in /workspace/env/logs/*; do
      [ -f "$f" ] || continue
      printf "\\n===== %s =====\\n" "$f"
      tail -n 300 "$f" || true
    done
  ' > "$RESULT/server-env-logs.txt" 2>&1 || true

  stage=collect-template-list
  timeout -k 3s 20s env XDG_CONFIG_HOME="$CONFIG_DIR" "$CLI" template list --output json \
    > "$RESULT/template-list.json" 2> "$RESULT/template-list.err"
  python3 -m json.tool "$RESULT/template-list.json" > /dev/null
  stage=diagnostic
}
run > "$RESULT/console.log" 2>&1
