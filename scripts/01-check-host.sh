#!/usr/bin/env bash
# Read-only host checks. Does not load modules or modify services.
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RESULT="$ROOT/.artifacts/host-check-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RESULT" || exit 1
umask 077
{
  uname -srmo
  id
  ls -l /dev/kvm /dev/ublk-control
  /sbin/modinfo -F filename ublk_drv
  timeout -k 2s 10s docker info --format '{{.ServerVersion}}'
  python3 - <<'PY'
import os, fcntl
try:
    fd = os.open('/dev/kvm', os.O_RDWR)
    print('kvm_api_version=' + str(fcntl.ioctl(fd, 0xAE00, 0)))
    os.close(fd)
except OSError as e:
    print('kvm_check_failed=' + str(e))
PY
} > "$RESULT/host.txt" 2>&1
if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
  if ! chown "$SUDO_UID:$SUDO_GID" "$RESULT" "$RESULT/host.txt"; then
    printf 'status=FAIL stage=result-ownership result_dir=%s\n' "$RESULT"
    exit 1
  fi
fi
printf 'status=INFO stage=read-only-inventory result_dir=%s\n' "$RESULT"
