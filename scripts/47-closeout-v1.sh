#!/usr/bin/env bash
# Functional reproduction only; does not repeat the five monitoring groups.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $# != 2 ]]; then
  echo 'usage: bash scripts/47-closeout-v1.sh NEW_PROJECT_ARTIFACT_DIRECTORY PREPARED_OFFICIAL_TASK' >&2
  exit 2
fi
DEST=$(realpath -m "$1")
TASK=$(realpath "$2")
[[ $DEST == "$ROOT/.artifacts/"* && ! -e $DEST ]] || { echo 'destination must be new and inside project .artifacts/' >&2; exit 2; }
mkdir -p "$DEST/logs"
export ASB_WRITER_LOCK="$ROOT/.artifacts/trenvx-disk-writer.lock"
run_logged() {
  local name=$1
  shift
  if ! timeout --kill-after=90s 900s "$@" > "$DEST/logs/$name.log" 2>&1; then
    echo "status=FAIL phase=$name result_dir=$DEST"
    return 1
  fi
  echo "status=PASS phase=$name result_dir=$DEST"
}
run_logged prepare-agentenv python3 "$ROOT/scripts/prepare_exp1_clean_directory.py" "$DEST/agentenv" --v1 --backend agentenv --official-task "$TASK"
run_logged prepare-trenvx python3 "$ROOT/scripts/prepare_exp1_clean_directory.py" "$DEST/trenvx" --v1 --backend trenvx --official-task "$TASK"
CFG="$DEST/agentenv/configs/exp1-agentenv.json"
run_logged official-negative-control python3 "$DEST/agentenv/scripts/run_exp1_official_control.py" "$CFG" "$DEST/agentenv/inputs/official-task"
run_logged agentenv-full python3 "$DEST/agentenv/scripts/run_exp1.py" "$CFG"
run_logged agentenv-checkpoint python3 "$DEST/agentenv/scripts/run_exp1_checkpoint.py" "$CFG"
PYTHON="$ROOT/runtime/trenvx-venv/bin/python"
TRENVX="$DEST/trenvx/scripts/smoke_trenvx_service.py"
run_logged trenvx-full "$PYTHON" "$TRENVX" --scenario exp1 --host-sample-interval 1 --record-phases --official-task "$DEST/trenvx/inputs/official-task" --discard-success-images
REFERENCE=$(python3 - "$DEST/trenvx/.artifacts/trenvx-service-smoke" <<'PY'
from pathlib import Path
import sys
paths=list(Path(sys.argv[1]).glob('*/result.json'))
assert len(paths)==1
print(paths[0].parent)
PY
)
run_logged trenvx-checkpoint "$PYTHON" "$TRENVX" --scenario exp1 --host-sample-interval 1 --record-phases --official-task "$DEST/trenvx/inputs/official-task" --checkpoint-after 022 --reference "$REFERENCE"
python3 - "$DEST" <<'PY'
from pathlib import Path
import json,sys
p=Path(sys.argv[1]);a=p/'agentenv/.artifacts';t=p/'trenvx/.artifacts'
def one(parent):
    paths=list(parent.glob('*/result.json'));assert len(paths)==1
    return paths[0].parent
full=one(a/'exp1-clean/audit');checkpoint=one(a/'exp1-checkpoint-clean/audit')
trenvx=[]
for path in (t/'trenvx-service-smoke').glob('*/result.json'):
    raw=path.parent;cfg=json.loads((raw/'config.json').read_text())
    trenvx.append(dict(backend='trenvx',mode='checkpoint-022' if cfg['checkpoint_after'] else 'full-light',raw=str(raw)))
cfg=dict(bundles={b:str(p/b) for b in ('agentenv','trenvx')},negative_control=str(one(a/'exp1-official-control')),
    runs=[dict(backend='agentenv',mode=m,audit=str(x),raw=str(x.parent.parent/'raw'/x.name))
          for m,x in [('full',full),('checkpoint-013',checkpoint)]]+trenvx)
(p/'audit-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
PY
run_logged offline-audit python3 "$ROOT/scripts/report_v1_closeout.py" "$DEST/audit-config.json" "$DEST/summary.json" --figures
echo "status=PASS result_dir=$DEST"
