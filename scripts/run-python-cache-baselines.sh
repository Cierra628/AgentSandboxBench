#!/usr/bin/env bash
# One no-reclaim baseline per Python task; no capacity-target runs.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
if (( $# )); then
    [[ $# == 2 && $1 == --resume ]]
    OUT=$(realpath "$2")
    [[ $OUT == "$ROOT/.artifacts/python-cache-baseline-"* && -d $OUT ]]
else
    OUT="$ROOT/.artifacts/python-cache-baseline-$(date -u +%Y%m%dT%H%M%SZ)-$$"
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
for TASK in sphinx7590 xarray6992; do
    STAGE="$TASK"
    if [[ ! -e "$OUT/$TASK.log" ]]; then
        bash scripts/use-platform.sh run "configs/platform-$TASK-cache-baseline.json" >"$OUT/$TASK.log" 2>&1
    fi
    python3 -B - "$OUT" "$TASK" <<'PY'
import json,re,sys
from pathlib import Path
out=Path(sys.argv[1]);task=sys.argv[2]
paths=[p for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(task+'.log')).read_text()) if not Path(p).name.startswith('report-')]
assert len(paths)==1,paths
run=Path(paths[0]);state=json.loads((run/'result.json').read_text());assert state['status']=='PASS'
p=out/'runs.json';runs=json.loads(p.read_text()) if p.exists() else {}
binding=dict(platform=str(run),storage=state['storage_result_dir'],report=state['report_dir'])
if task in runs:assert runs[task]==binding
runs[task]=binding
p.write_text(json.dumps(runs,indent=2)+'\n')
PY
done
STAGE=complete
printf 'status=PASS result_dir=%s\n' "$OUT"
