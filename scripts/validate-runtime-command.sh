#!/usr/bin/env bash
# Short command diagnosis in one owned 8 GiB volume; no full trajectory repeat.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
extra=()
if [[ $# == 1 && $1 == --find-diagnostic ]]; then
  extra=(--python-find-diagnostic)
elif (($#)); then printf 'usage: bash scripts/validate-runtime-command.sh [--find-diagnostic]\n' >&2; exit 2; fi
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
result_dir="$ROOT/.artifacts/runtime-command-validation/$run_id"
cache_dir="$ROOT/.artifacts/sphinx-backbone/command-cache-$run_id"
mkdir -p "$result_dir"
if ! python3 - "$ROOT" > "$result_dir/capacity-check.json" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,'scripts')
from isolated_storage import capacity_budget,available,GIB
value=capacity_budget(available(Path(sys.argv[1])),8*GIB)
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
  --source-backup-directory .artifacts/storage-isolated-service/20261009T050703Z-3934019 \
  --task-profile sphinx7590 > "$result_dir/cache-prepare.log" 2>&1
set +e
bash scripts/validate-isolated-storage.sh --automatic-window \
  --python-task-profile sphinx7590 \
  --python-backbone-inputs .artifacts/sphinx-backbone/prepare-20261009T041720Z \
  --python-readonly-replay --python-persistent-sampling --python-command-diagnostic \
  "${extra[@]}" \
  --python-cache-reference .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone \
  --immutable-cache-seed "$cache_dir" > "$result_dir/live.log" 2>&1
code=$?
set -e
printf '%s\n' "$code" > "$result_dir/exit-code"
if ((code)); then
  printf 'status=FAIL stage=command-diagnostic result_dir=%s\n' "$result_dir"
  exit "$code"
fi
printf 'status=PASS result_dir=%s\n' "$result_dir"
