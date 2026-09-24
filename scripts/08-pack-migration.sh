#!/usr/bin/env bash
# Private project handoff; excludes service credentials and build caches.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
umask 077
OUT=$(mktemp -d "$(dirname "$ROOT")/agentenv-migration-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
finish() {
  local rc=$?
  trap - EXIT
  if [[ $EUID -eq 0 && -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
    chown -R "$SUDO_UID:$SUDO_GID" "$OUT" || rc=1
  fi
  if ((rc == 0)); then
    printf 'status=PASS package_dir=%s\n' "$OUT"
  else
    printf 'status=FAIL rc=%s package_dir=%s\n' "$rc" "$OUT"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 143' INT TERM HUP
run() {
  timeout -k 5s 300s tar -czf "$OUT/agentenv_experiment.tar.gz" \
    --exclude='./runtime/config' --exclude='./AgentENV/target' \
    --exclude='./.cache' -C "$ROOT" .
  tar -tzf "$OUT/agentenv_experiment.tar.gz" > "$OUT/contents.txt"
  if grep -Eq '^\./(runtime/config|AgentENV/target|\.cache)(/|$)' "$OUT/contents.txt"; then
    echo 'Excluded content found in archive'
    return 1
  fi
  for required in ./AGENTS.md ./MIGRATION.md ./AgentENV/.git/HEAD ./scripts/06-smoke-template-sandbox.sh; do
    grep -Fxq "$required" "$OUT/contents.txt"
  done
  cd "$OUT"
  sha256sum agentenv_experiment.tar.gz > SHA256SUMS
  sha256sum -c SHA256SUMS
}
run > "$OUT/pack.log" 2>&1
