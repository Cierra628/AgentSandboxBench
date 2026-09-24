#!/usr/bin/env bash
# Fetch the official image and load ublk for this boot only. No service changes.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESULT="$ROOT/.artifacts/runtime-prepare-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$RESULT"
chmod 700 "$RESULT"
umask 077
IMAGE_REF=ghcr.io/kvcache-ai/aenv-server@sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03
stage=preflight
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if ((rc == 0)); then status=PASS; fi
  printf 'status=%s stage=%s rc=%s\n' "$status" "$stage" "$rc" > "$RESULT/result.txt"
  if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    if ! chown -R "$SUDO_UID:$SUDO_GID" "$RESULT"; then
      status=FAIL; stage=result-ownership; rc=1
    fi
  fi
  printf 'status=%s stage=%s result_dir=%s\n' "$status" "$stage" "$RESULT"
  exit "$rc"
}
trap finish EXIT
prepare() {
  [[ $EUID -eq 0 ]] || { echo 'Run with sudo bash.'; return 1; }
  timeout -k 3s 15s docker info --format '{{.ServerVersion}}'
  python3 - <<'PY'
import os, fcntl
fd = os.open('/dev/kvm', os.O_RDWR)
assert fcntl.ioctl(fd, 0xAE00, 0) == 12
os.close(fd)
PY
  /sbin/modinfo -F filename ublk_drv
  printf '%s\n' "$IMAGE_REF" > "$RESULT/image-ref.txt"
  stage=image-pull
  timeout -k 5s 300s docker pull "$IMAGE_REF"
  timeout -k 3s 15s docker image inspect "$IMAGE_REF" \
    --format '{{json .}}' > "$RESULT/image.json"
  python3 - "$RESULT/image.json" "$RESULT/image-id.txt" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    image = json.load(f)
assert image['Id'].startswith('sha256:')
with open(sys.argv[2], 'w') as f:
    f.write(image['Id'] + '\n')
PY
  stage=ublk-load
  if [[ -d /sys/module/ublk_drv ]]; then
    echo 'ublk already loaded; leaving parameters unchanged'
  else
    timeout -k 3s 15s /sbin/modprobe ublk_drv
    echo 'ublk loaded temporarily; no persistent config written'
  fi
  [[ -c /dev/ublk-control ]]
  ls -l /dev/kvm /dev/ublk-control
  stage=runtime-prerequisites
}
prepare > "$RESULT/console.log" 2>&1
