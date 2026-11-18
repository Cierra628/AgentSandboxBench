#!/usr/bin/env bash
# Four approved Python targets; reuse the completed no-reclaim baselines.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
if (( $# )); then
    [[ $# == 2 && $1 == --resume ]]
    OUT=$(realpath "$2")
    [[ $OUT == "$ROOT/.artifacts/python-cache-targets-"* && -d $OUT ]]
else
    OUT="$ROOT/.artifacts/python-cache-targets-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p "$OUT"
fi
STAGE=preflight
finish() {
    local code=$?
    trap - EXIT
    printf 'exit_code=%s stage=%s result_dir=%s\n' "$code" "$STAGE" "$OUT"
    exit "$code"
}
trap finish EXIT
for CONDITION in sphinx7590-65 sphinx7590-98 xarray6992-167 xarray6992-251; do
    TASK=${CONDITION%-*}; TARGET=${CONDITION##*-}
    STAGE="$CONDITION"
    if [[ ! -e "$OUT/$CONDITION.log" ]]; then
        bash scripts/use-platform.sh run "configs/platform-$TASK-cache-$TARGET.json" >"$OUT/$CONDITION.log" 2>&1
    fi
    python3 -B - "$OUT" "$CONDITION" <<'PY'
import json,re,sys
from pathlib import Path
out=Path(sys.argv[1]);condition=sys.argv[2]
paths=[p for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(condition+'.log')).read_text()) if not Path(p).name.startswith('report-')]
assert len(paths)==1,paths
run=Path(paths[0]);state=json.loads((run/'result.json').read_text());assert state['status']=='PASS'
source=Path(state['storage_result_dir'])/'python-backbone'
child=json.loads((source/'result.json').read_text())
assert child['status']=='PASS' and child['cache_target_mib']==int(condition.rsplit('-',1)[1])
assert child['checks']['cache_target_and_damon_restored']=='PASS'
p=out/'runs.json';runs=json.loads(p.read_text()) if p.exists() else {}
binding=dict(platform=str(run),storage=state['storage_result_dir'],report=state['report_dir'])
if condition in runs:assert runs[condition]==binding
runs[condition]=binding
p.write_text(json.dumps(runs,indent=2)+'\n')
PY
done
STAGE=complete
printf 'status=PASS result_dir=%s\n' "$OUT"
