#!/usr/bin/env bash
# Seven fresh 2 CPU / 4096 MiB roots; converted cache copied into a fixed 16 GiB volume.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
result_dir="$ROOT/.artifacts/runtime-grpo-validation/$run_id"
cache_dir="$ROOT/.artifacts/prettier6604/runtime-cache-$run_id"
mkdir -p "$result_dir"
python3 - "$ROOT" > "$result_dir/capacity-check.json" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,'scripts')
from isolated_storage import capacity_budget,available,GIB
value=capacity_budget(available(Path(sys.argv[1])),16*GIB)
print(json.dumps(value,indent=2))
if not value['ready']:raise SystemExit('16 GiB volume requires >42 GiB host available; preparation not started')
PY
python3 -B scripts/run_prettier6604_grpo.py --check-only \
  --frozen-export .artifacts/prettier6604/grpo-input-audit-20261008T120626Z \
  --official-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
  --expected-official-audit .artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json \
  > "$result_dir/input-check.log" 2>&1
release_cache() {
  if [[ -f "$cache_dir/resources.json" ]]; then
    python3 -B scripts/prepare_prettier6604_cache.py "$cache_dir" --release > "$result_dir/cache-release.log" 2>&1
  fi
}
trap release_cache EXIT
printf 'status=PREPARING result_dir=%s\n' "$result_dir"
timeout -k 30s 300s python3 -B scripts/prepare_prettier6604_cache.py "$cache_dir" \
  > "$result_dir/cache-prepare.log" 2>&1
set +e
bash scripts/validate-isolated-storage.sh --automatic-window \
  --prettier6604-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
  --prettier6604-export .artifacts/prettier6604/grpo-input-audit-20261008T120626Z \
  --expected-official-audit .artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json \
  --immutable-cache-seed "$cache_dir" --prettier6604-grpo-capacity-gib 16 \
  > "$result_dir/live.log" 2>&1
code=$?
set -e
printf '%s\n' "$code" > "$result_dir/exit-code"
if ((code)); then
  printf 'status=FAIL stage=runtime-grpo-validation result_dir=%s\n' "$result_dir"
  exit "$code"
fi
printf 'status=PASS result_dir=%s\n' "$result_dir"
