#!/usr/bin/env bash
# Let the server convert Exp1's fixed OCI image outside the CLI's 120 s cold-start request.
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$root/IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/config.env"
export XDG_CONFIG_HOME="$root/runtime/config"
export PATH="$root/runtime/bin:$PATH"
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
result_dir="$root/.artifacts/exp1-image-prewarm/$run_id"
umask 077
mkdir -p "$result_dir"
name="exp1-prewarm-$run_id"
owned=0
cleanup() {
  if ((owned)); then
    timeout -k 3s 25s aenv template delete "$name" >"$result_dir/cleanup.log" 2>&1 || return 1
    owned=0
  fi
}
trap 'cleanup || true' EXIT
test "$(aenv list --output json | jq length)" = 0
test "$(aenv template list --output json | jq length)" = 0
printf '%s\n' "$WORKLOAD_IMAGE" > "$result_dir/image.txt"
printf 'status=RUNNING result_dir=%s\n' "$result_dir"
if timeout -k 3s 25s aenv pull --detach --name "$name" --cpu "$SANDBOX_CPU" --memory "$SANDBOX_MEMORY_MIB" "$WORKLOAD_IMAGE" >"$result_dir/submit.log" 2>&1; then
  owned=1
else
  if aenv template list --output json | jq -e --arg name "$name" 'any(.[]; any(.names // []; . == $name))' >/dev/null; then owned=1; fi
  printf 'status=FAIL stage=submit result_dir=%s\n' "$result_dir"
  exit 1
fi
if ! timeout -k 3s 480s aenv template watch "$name" >"$result_dir/watch.log" 2>&1; then
  printf 'status=FAIL stage=image-prewarm result_dir=%s\n' "$result_dir"
  exit 1
fi
aenv template list --output json >"$result_dir/templates-before-cleanup.json" 2>"$result_dir/list-error.log" || true
if ! cleanup || ! aenv template list --output json >"$result_dir/templates-after-cleanup.json" ||
  jq -e --arg name "$name" 'any(.[]; any(.names // []; . == $name))' "$result_dir/templates-after-cleanup.json" >/dev/null; then
  printf 'status=FAIL stage=cleanup result_dir=%s\n' "$result_dir"
  exit 1
fi
printf 'status=PASS stage=image-prewarm result_dir=%s\n' "$result_dir"
