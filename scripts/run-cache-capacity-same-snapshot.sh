#!/usr/bin/env bash
# One common checkpoint, then 64 and 128 MiB targets in the same private service.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
EXTRA=()
if (( $# )); then
    [[ $# == 1 && $1 == --damon-before-checkpoint ]]
    EXTRA+=(--prettier6604-damon-before-checkpoint)
fi
OUT="$ROOT/.artifacts/cache-capacity-same-snapshot-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$OUT/code"
cp scripts/{run-cache-capacity-same-snapshot.sh,run-prettier-snapshot-cache-pair.sh,run_prettier6604_grpo.py,validate_isolated_agentenv.py,report_cache_capacity_same_snapshot.py,report_guest_cache_capacity_task.py,report_provider_pair_timing.py,guest_cache_capacity.py} "$OUT/code/"
python3 -B - "$OUT" <<'PY'
import json,shutil,sys
from pathlib import Path
p=Path(sys.argv[1]);free=shutil.disk_usage('.').free
(p/'preflight.json').write_text(json.dumps(dict(host_available_bytes=free,required_more_than_bytes=50*2**30),indent=2)+'\n')
assert free>50*2**30
PY
finish() { local code=$?; trap - EXIT; printf 'exit_code=%s result_dir=%s\n' "$code" "$OUT"; exit "$code"; }
trap finish EXIT
bash scripts/run-prettier-snapshot-cache-pair.sh \
  --diagnostic-guest-kernel .artifacts/agentenv-cache-reclaim-kernel-20261018/vmlinux.bin \
  --snapshot-provider-binary .artifacts/provider-segments-build-20261016/uvm-ublk-daemon \
  --snapshot-provider-get-data --snapshot-bpf-batch \
  --prettier6604-cache-target-mib 64 --prettier6604-capacity-sequence \
  "${EXTRA[@]}" \
  >"$OUT/run.log" 2>&1
python3 -B - "$OUT" <<'PY'
import json,re,subprocess,sys
from pathlib import Path
out=Path(sys.argv[1]);matches=re.findall(r'status=PASS result_dir=(\S+)',(out/'run.log').read_text())
assert len(matches)==1,matches
wrapper=Path(matches[0]);state=json.loads((wrapper/'result.json').read_text())
assert state['status']=='PASS' and state['capacity_sequence']==[64,128]
storage=Path(state['storage_result_dir'])
(out/'runs.json').write_text(json.dumps(dict(wrapper=str(wrapper),storage=str(storage)),indent=2)+'\n')
subprocess.run([sys.executable,'-B','scripts/report_cache_capacity_same_snapshot.py',
 '--storage',str(storage),'--wrapper',str(wrapper),'--output',str(out/'report')],check=True,timeout=120)
PY
printf 'status=PASS result_dir=%s\n' "$OUT"
