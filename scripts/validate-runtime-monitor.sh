#!/usr/bin/env bash
# Default: restore smoke. Full-task modes use one normal 4096 MiB VM in 8 GiB.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mode=${1:-smoke}
if [[ "$mode" != smoke && "$mode" != --full-task && "$mode" != --full-xarray && "$mode" != --full-sphinx-diagnostic && "$mode" != --find-diagnostic ]] || (($# > 1)); then
  printf 'usage: bash scripts/validate-runtime-monitor.sh [--full-task|--full-xarray|--full-sphinx-diagnostic|--find-diagnostic]\n' >&2
  exit 2
fi
capacity=16
task_args=(--python-runtime-smoke --python-cache-reference .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone)
if [[ "$mode" == --find-diagnostic ]]; then task_args+=(--python-find-diagnostic); fi
profile=sphinx7590
inputs=.artifacts/sphinx-backbone/prepare-20261009T041720Z
backup=.artifacts/storage-isolated-service/20261009T050703Z-3934019
cache_root=sphinx-backbone
if [[ "$mode" == --full-task || "$mode" == --full-xarray || "$mode" == --full-sphinx-diagnostic ]]; then
  capacity=8
  task_args=()
fi
if [[ "$mode" == --full-sphinx-diagnostic ]]; then task_args=(--python-sphinx-pipeline-diagnostic); fi
if [[ "$mode" == --full-xarray ]]; then
  profile=xarray6992
  inputs=.artifacts/python-backbone/shell-environment-20261009
  backup=.artifacts/storage-isolated-service/20261008T165515Z-3834076
  cache_root=python-backbone
  task_args=(--python-diagnostic-continuation)
fi
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
result_dir="$ROOT/.artifacts/runtime-monitor-validation/$run_id"
cache_dir="$ROOT/.artifacts/$cache_root/runtime-cache-$run_id"
mkdir -p "$result_dir"
if ! python3 - "$ROOT" "$capacity" > "$result_dir/capacity-check.json" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,'scripts')
from isolated_storage import capacity_budget,available,GIB
value=capacity_budget(available(Path(sys.argv[1])),int(sys.argv[2])*GIB)
print(json.dumps(value,indent=2))
if not value['ready']:raise SystemExit('host space budget insufficient; preparation not started')
PY
then
  printf 'status=BLOCKED stage=capacity-check result_dir=%s\n' "$result_dir"
  exit 2
fi
release_cache() {
  if [[ -f "$cache_dir/resources.json" ]]; then
    python3 -B scripts/prepare_prettier6604_cache.py "$cache_dir" --release > "$result_dir/cache-release.log" 2>&1
  fi
}
trap release_cache EXIT
printf 'status=PREPARING result_dir=%s\n' "$result_dir"
timeout -k 30s 300s python3 -B scripts/prepare_prettier6604_cache.py "$cache_dir" \
  --source-backup-directory "$backup" \
  --task-profile "$profile" > "$result_dir/cache-prepare.log" 2>&1
set +e
bash scripts/validate-isolated-storage.sh --automatic-window \
  --python-task-profile "$profile" \
  --python-backbone-inputs "$inputs" \
  --python-readonly-replay --python-persistent-sampling \
  --immutable-cache-seed "$cache_dir" "${task_args[@]}" \
  > "$result_dir/live.log" 2>&1
code=$?
set -e
printf '%s\n' "$code" > "$result_dir/exit-code"
if ((code)); then
  printf 'status=FAIL stage=runtime-monitor-validation result_dir=%s\n' "$result_dir"
  exit "$code"
fi
printf 'status=PASS result_dir=%s\n' "$result_dir"
