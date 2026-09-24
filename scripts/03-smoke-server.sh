#!/usr/bin/env bash
# Start one isolated Docker server; retain it for the sandbox smoke.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $# -ne 1 && $# -ne 3 ]]; then echo 'usage: sudo bash scripts/03-smoke-server.sh PREPARE_RESULT_DIR [PROXY_URL API_PORT]'; exit 2; fi
PROXY_URL=${2:-}
API_PORT=${3:-}
PREP=$(realpath "$1")
RESULT="$ROOT/.artifacts/server-smoke-$(date -u +%Y%m%dT%H%M%SZ)-$$"
umask 077
mkdir -p "$RESULT"
stage=preflight
CID=''
trap 'exit 143' INT TERM HUP
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if [[ -n $CID ]]; then
    timeout -k 2s 10s docker logs --tail 200 "$CID" > "$RESULT/server.log" 2>&1 || true
    timeout -k 2s 10s docker inspect "$CID" > "$RESULT/container.json" 2>&1 || true
    if ((rc != 0)); then
      timeout -k 3s 20s docker stop -t 10 "$CID" > "$RESULT/stop.log" 2>&1 || true
    fi
  fi
  if ((rc == 0)); then status=PASS; fi
  printf 'status=%s stage=%s rc=%s\n' "$status" "$stage" "$rc" > "$RESULT/result.txt"
  if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    chown -R "$SUDO_UID:$SUDO_GID" "$RESULT" || { status=FAIL; stage=result-ownership; rc=1; }
  fi
  printf 'status=%s stage=%s result_dir=%s\n' "$status" "$stage" "$RESULT"
  exit "$rc"
}
trap finish EXIT
run() {
  [[ $EUID -eq 0 ]] || { echo 'Run with sudo bash.'; return 1; }
  grep -qx 'status=PASS stage=runtime-prerequisites rc=0' "$PREP/result.txt"
  local image name code ready=0 deadline address port key
  image=$(<"$PREP/image-id.txt")
  [[ $image =~ ^sha256:[a-f0-9]{64}$ ]]
  [[ -c /dev/kvm && -c /dev/ublk-control ]]
  timeout -k 2s 10s docker image inspect "$image" > "$RESULT/image.json"
  name="agentenv-smoke-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  printf '%s\n' "$name" > "$RESULT/container-name.txt"
  stage=server-start
  local -a network_args=(-p 127.0.0.1::8000 -e API_ADDR=0.0.0.0:8000)
  if [[ -n "$PROXY_URL" ]]; then
    [[ "$PROXY_URL" =~ ^http://127\.0\.0\.1:[0-9]+$ ]]
    [[ "$API_PORT" =~ ^[0-9]+$ ]]
    python3 - "$API_PORT" <<'PORT'
import socket, sys
with socket.socket() as s:
    s.bind(('127.0.0.1', int(sys.argv[1])))
PORT
    network_args=(--network host -e "API_ADDR=127.0.0.1:$API_PORT"
      -e "HTTP_PROXY=$PROXY_URL" -e "HTTPS_PROXY=$PROXY_URL"
      -e "http_proxy=$PROXY_URL" -e "https_proxy=$PROXY_URL"
      -e NO_PROXY=localhost,127.0.0.1,::1,10.11.0.0/16,10.12.0.0/16,169.254.0.20/30 -e no_proxy=localhost,127.0.0.1,::1,10.11.0.0/16,10.12.0.0/16,169.254.0.20/30)
  fi
  if [[ ${AENV_SMOKE_DIAGNOSTICS:-0} == 1 ]]; then
    network_args+=(-e RUST_LOG=info,agentenv::sandbox::envd=trace
      -e AENV_FIRECRACKER_SERIAL_DIR=/workspace/env/logs/serial)
  fi
  # PID namespace and container data are private; proxy mode shares host networking.
  CID=$(timeout -k 3s 30s docker create --name "$name" \
    --label agentenv.experiment=initial-smoke \
    --privileged --mount type=bind,src=/dev,dst=/dev \
    --ulimit memlock=-1:-1 "${network_args[@]}" "$image")
  printf '%s\n' "$CID" > "$RESULT/container-id.txt"
  if [[ ${AENV_SMOKE_DIAGNOSTICS:-0} == 1 ]]; then
    timeout -k 2s 10s docker cp "$CID:/workspace/config/default.toml" "$RESULT/config.original.toml"
    python3 - "$RESULT/config.original.toml" "$RESULT/config.diagnostic.toml" <<'CONFIG'
from pathlib import Path
import sys, tomllib
s = Path(sys.argv[1]).read_text()
assert '# log_level = "Info"' in s
s = s.replace('# log_level = "Info"', 'log_level = "Info"', 1)
assert tomllib.loads(s)['firecracker']['log_level'] == 'Info'
Path(sys.argv[2]).write_text(s)
CONFIG
    timeout -k 2s 10s docker cp "$RESULT/config.diagnostic.toml" "$CID:/workspace/config/default.toml"
  fi
  timeout -k 3s 30s docker start "$CID"
  if [[ -n "$PROXY_URL" ]]; then
    printf '127.0.0.1:%s\n' "$API_PORT" > "$RESULT/address.txt"
  else
    timeout -k 2s 10s docker port "$CID" 8000/tcp > "$RESULT/address.txt"
  fi
  address=$(<"$RESULT/address.txt")
  [[ "$address" == 127.0.0.1:* ]]
  port=${address##*:}
  [[ "$port" =~ ^[0-9]+$ ]]
  stage=server-health
  deadline=$((SECONDS + 90))
  while ((SECONDS < deadline)); do
    [[ $(timeout -k 2s 5s docker inspect -f '{{.State.Running}}' "$CID") == true ]] || return 1
    if code=$(timeout -k 2s 8s curl -sS --max-time 5 \
      -o "$RESULT/health.json" -w '%{http_code}' "http://127.0.0.1:$port/health" \
      2>> "$RESULT/health-attempts.log"); then
      if [[ $code == 200 || $code == 204 ]]; then
        printf '%s\n' "http_status=$code" > "$RESULT/health-status.txt"
        ready=1
        break
      fi
    fi
    sleep 2
  done
  [[ $ready == 1 ]]
  stage=authenticated-api
  key=$(timeout -k 2s 10s docker exec "$CID" cat /workspace/env/secrets/api-key)
  [[ -n "$key" ]]
  timeout -k 2s 15s curl --fail --silent --show-error --max-time 10 \
    -H "X-API-Key: $key" "http://127.0.0.1:$port/sandboxes" > "$RESULT/sandboxes.json"
  python3 -m json.tool "$RESULT/sandboxes.json" > /dev/null
  stage=server-ready
}
run > "$RESULT/console.log" 2>&1
