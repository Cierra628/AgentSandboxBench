#!/usr/bin/env bash
# Verify one real AgentENV cold sandbox through the released aenv CLI.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $# -ne 1 ]]; then
  echo 'usage: sudo bash scripts/04-smoke-cold-sandbox.sh SERVER_RESULT_DIR'
  exit 2
fi
SERVER_RESULT=$(realpath "$1")
RESULT="$ROOT/.artifacts/cold-sandbox-smoke-$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUNTIME="$ROOT/runtime"
CLI_DIR="$RUNTIME/bin"
CONFIG_DIR="$RUNTIME/config"
CLI="$CLI_DIR/aenv"
CREDENTIALS="$CONFIG_DIR/aenv/credentials"
umask 077
mkdir -p "$RESULT" "$CLI_DIR" "$CONFIG_DIR/aenv"
stage=preflight
SANDBOX_ID=''
CLEANUP_RC=0
trap 'exit 143' INT TERM HUP
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if [[ -n "$SANDBOX_ID" && -x "$CLI" && -s "$CREDENTIALS" ]]; then
    set +e
    timeout -k 3s 60s env XDG_CONFIG_HOME="$CONFIG_DIR" \
      "$CLI" delete "$SANDBOX_ID" > "$RESULT/delete.log" 2>&1
    CLEANUP_RC=$?
    set -e
  fi
  if ((rc == 0 && CLEANUP_RC == 0)); then status=PASS; fi
  if ((rc == 0 && CLEANUP_RC != 0)); then stage=cleanup; rc=$CLEANUP_RC; fi
  printf 'status=%s stage=%s rc=%s cleanup_rc=%s\n' \
    "$status" "$stage" "$rc" "$CLEANUP_RC" > "$RESULT/result.txt"
  if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    chown -R "$SUDO_UID:$SUDO_GID" "$RESULT" "$RUNTIME" || {
      status=FAIL; stage=result-ownership; rc=1
    }
  fi
  printf 'status=%s stage=%s cleanup=%s result_dir=%s\n' \
    "$status" "$stage" "$([[ $CLEANUP_RC -eq 0 ]] && echo PASS || echo FAIL)" "$RESULT"
  exit "$rc"
}
trap finish EXIT
run() {
  [[ $EUID -eq 0 ]] || { echo 'Run with sudo bash.'; return 1; }
  grep -qx 'status=PASS stage=server-ready rc=0' "$SERVER_RESULT/result.txt"
  local server_cid api_url api_key start_output
  server_cid=$(<"$SERVER_RESULT/container-id.txt")
  api_url="http://$(<"$SERVER_RESULT/address.txt")"
  [[ "$server_cid" =~ ^[a-f0-9]{64}$ ]]
  [[ "$api_url" =~ ^http://127\.0\.0\.1:[0-9]+$ ]]
  timeout -k 2s 10s docker inspect -f '{{.State.Running}}' "$server_cid" | grep -qx true

  stage=cli-install
  if [[ ! -x "$CLI" ]]; then
    timeout -k 5s 240s env INSTALL_DIR="$CLI_DIR" \
      bash "$ROOT/AgentENV/scripts/install-cli.sh" > "$RESULT/cli-install.log" 2>&1
  fi
  [[ -x "$CLI" ]]
  env XDG_CONFIG_HOME="$CONFIG_DIR" "$CLI" --version > "$RESULT/cli-version.txt"

  stage=credentials
  api_key=$(timeout -k 2s 10s docker exec "$server_cid" \
    cat /workspace/env/secrets/api-key)
  [[ "$api_key" =~ ^[^[:space:]]+$ ]]
  printf 'url = "%s"\napi_key = "%s"\n' "$api_url" "$api_key" > "$CREDENTIALS"
  chmod 600 "$CREDENTIALS"

  stage=cold-start
  timeout -k 10s 600s env XDG_CONFIG_HOME="$CONFIG_DIR" \
    "$CLI" start --cold ubuntu:24.04 --detach --timeout 180 \
    > "$RESULT/start.log" 2>&1
  start_output=$(<"$RESULT/start.log")
  SANDBOX_ID=$(printf '%s\n' "$start_output" | tail -n 1)
  [[ "$SANDBOX_ID" =~ ^[0-9a-fA-F-]{36}$ ]]
  printf '%s\n' "$SANDBOX_ID" > "$RESULT/sandbox-id.txt"

  stage=command-exec
  timeout -k 3s 60s env XDG_CONFIG_HOME="$CONFIG_DIR" \
    "$CLI" exec "$SANDBOX_ID" /bin/sh -c \
    'printf "agentenv-smoke\\n" > /tmp/agentenv-smoke; cat /tmp/agentenv-smoke' \
    > "$RESULT/exec.log" 2>&1
  grep -qx 'agentenv-smoke' "$RESULT/exec.log"
  stage=functional-smoke
}
run > "$RESULT/console.log" 2>&1
