#!/usr/bin/env bash
# Recreate only the AgentENV smoke container with host networking so its
# registry client can use a loopback-only host proxy. No host service changes.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $# -ne 2 ]]; then
  echo 'usage: sudo bash scripts/05-restart-server-host-proxy.sh SERVER_RESULT_DIR PROXY_URL'
  exit 2
fi
OLD_RESULT=$(realpath "$1")
PROXY_URL=$2
RESULT="$ROOT/.artifacts/server-smoke-host-proxy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
umask 077
mkdir -p "$RESULT"
stage=preflight
CID=''
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if [[ -n "$CID" ]]; then
    timeout -k 3s 10s docker logs --tail 240 "$CID" > "$RESULT/server.log" 2>&1 || true
    timeout -k 3s 10s docker inspect "$CID" > "$RESULT/container.json" 2>&1 || true
    if ((rc != 0)); then
      timeout -k 3s 20s docker rm -f "$CID" > "$RESULT/stop.log" 2>&1 || true
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
  grep -qx 'status=PASS stage=server-ready rc=0' "$OLD_RESULT/result.txt"
  local old_cid image name address port code ready=0 deadline key
  old_cid=$(<"$OLD_RESULT/container-id.txt")
  image=$(<"$ROOT/.artifacts/runtime-prepare-20260922T064000Z-3098078/image-id.txt")
  [[ "$old_cid" =~ ^[a-f0-9]{64}$ ]]
  [[ "$PROXY_URL" =~ ^https?://[^[:space:]]+$ ]]
  timeout -k 2s 10s docker inspect -f '{{index .Config.Labels "agentenv.experiment"}}' "$old_cid" | grep -qx initial-smoke
  timeout -k 2s 10s docker inspect -f '{{.Name}}' "$old_cid" | grep -Eq '^/agentenv-smoke-'
  timeout -k 3s 30s docker rm -f "$old_cid" > "$RESULT/old-container-remove.log"
  printf '%s\n' "$old_cid" > "$RESULT/replaced-container-id.txt"
  timeout -k 2s 10s docker image inspect "$image" > "$RESULT/image.json"

  name="agentenv-smoke-host-proxy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  stage=server-start
  CID=$(timeout -k 3s 30s docker create --name "$name" \
    --label agentenv.experiment=initial-smoke-host-proxy \
    --privileged --network host --mount type=bind,src=/dev,dst=/dev \
    --ulimit memlock=-1:-1 \
    -e API_ADDR=127.0.0.1:8000 \
    -e HTTP_PROXY="$PROXY_URL" -e HTTPS_PROXY="$PROXY_URL" \
    -e http_proxy="$PROXY_URL" -e https_proxy="$PROXY_URL" \
    -e NO_PROXY=localhost,127.0.0.1,::1 -e no_proxy=localhost,127.0.0.1,::1 \
    "$image")
  printf '%s\n' "$CID" > "$RESULT/container-id.txt"
  timeout -k 3s 30s docker start "$CID"
  printf '%s\n' '127.0.0.1:8000' > "$RESULT/address.txt"
  stage=server-health
  deadline=$((SECONDS + 90))
  while ((SECONDS < deadline)); do
    [[ $(timeout -k 2s 5s docker inspect -f '{{.State.Running}}' "$CID") == true ]] || return 1
    if code=$(timeout -k 2s 8s curl -sS --max-time 5 -o "$RESULT/health.json" \
      -w '%{http_code}' http://127.0.0.1:8000/health 2>>"$RESULT/health-attempts.log"); then
      if [[ "$code" == 200 || "$code" == 204 ]]; then
        printf 'http_status=%s\n' "$code" > "$RESULT/health-status.txt"
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
    -H "X-API-Key: $key" http://127.0.0.1:8000/sandboxes > "$RESULT/sandboxes.json"
  python3 -m json.tool "$RESULT/sandboxes.json" > /dev/null
  stage=server-ready
}
run > "$RESULT/console.log" 2>&1
