#!/usr/bin/env bash
# One existing step-11 prefix and two original suffixes; private service only.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
RUN_KEY=$(date -u +%Y%m%dT%H%M%SZ)-$$
PAIR_OUT="$ROOT/.artifacts/prettier-snapshot-cache-pair/$RUN_KEY"
PAIR_SEED="$ROOT/.artifacts/prettier6604/pair-cache-$RUN_KEY"
mkdir -p "$PAIR_OUT"
cleanup_seed() {
    local code=$?
    trap - EXIT
    if [[ -f "$PAIR_SEED/resources.json" ]]; then
        if ! python3 -B scripts/prepare_prettier6604_cache.py "$PAIR_SEED" --release >"$PAIR_OUT/cache-release.log" 2>&1; then
            code=1
        fi
    fi
    python3 -B - "$PAIR_OUT" "$code" <<'PYRECEIPT'
import json,sys
from pathlib import Path
out=Path(sys.argv[1]);path=out/'result.json'
value=json.loads(path.read_text()) if path.exists() else {}
value.update(status='PASS' if int(sys.argv[2])==0 else 'FAIL',exit_code=int(sys.argv[2]))
path.write_text(json.dumps(value,indent=2)+'\n')
PYRECEIPT
    if (( code == 0 )); then
        printf 'status=PASS result_dir=%s\n' "$PAIR_OUT"
    else
        printf 'status=FAIL result_dir=%s\n' "$PAIR_OUT"
    fi
    exit "$code"
}
trap cleanup_seed EXIT
python3 -B scripts/run_prettier6604_grpo.py \
    --frozen-export .artifacts/prettier6604/grpo-input-audit-20261008T120626Z \
    --official-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
    --expected-official-audit .artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json \
    --schedule bpo --snapshot-cache-pair --check-only >"$PAIR_OUT/input-check.log" 2>&1
python3 -B scripts/prepare_prettier6604_cache.py "$PAIR_SEED" >"$PAIR_OUT/cache-prepare.log" 2>&1
bash scripts/validate-isolated-storage.sh --automatic-window --immutable-cache-seed "$PAIR_SEED" \
    --prettier6604-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
    --expected-official-audit .artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json \
    --prettier6604-export .artifacts/prettier6604/grpo-input-audit-20261008T120626Z \
    --prettier6604-schedule bpo --prettier6604-snapshot-cache-pair \
    --diagnostic-guest-kernel .artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin "$@" \
    >"$PAIR_OUT/isolated-runtime.log" 2>&1
python3 -B - "$PAIR_OUT" <<'PY'
import json,re,sys
from pathlib import Path
root=Path.cwd();out=Path(sys.argv[1])
matches=re.findall(r'status=PASS result_dir=(\S+)',(out/'isolated-runtime.log').read_text())
children=[Path(p) for p in matches if Path(p).parent==root/'.artifacts/storage-isolated-service']
assert len(set(children))==1,children
source=children[0]/'prettier6604'
receipt=json.loads((children[0]/'result.json').read_text());assert receipt['status']=='PASS'
sys.path.insert(0,str(root/'scripts'))
task=json.loads((source/'result.json').read_text());assert task['status']=='PASS'
if task.get('cache_sampling','on')=='off':
    summary=dict(phase_pairs={},task_checks=task['checks'],child_samples={})
else:
    from report_snapshot_cache_pair import report
    summary=report(source,out/'report')
    assert summary['status']=='PASS'
sequence=receipt.get('capacity_sequence_results')
if sequence:
    second=report(children[0]/'prettier6604-128',out/'report-128')
    assert second['status']=='PASS'
(out/'result.json').write_text(json.dumps(dict(status='PASS',storage_result_dir=str(children[0]),
    phases=summary['phase_pairs'],checks=summary['task_checks'],child_samples=summary['child_samples'],
    cache_sampling=task.get('cache_sampling','on'),
    capacity_sequence=[64,128] if sequence else None,
    physical_report='NOT_REQUESTED' if task.get('cache_sampling','on')=='off' else 'PASS'),indent=2)+'\n')
PY
