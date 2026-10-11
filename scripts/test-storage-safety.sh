#!/usr/bin/env bash
# Small owned fixtures only: no VM, mounts, service operations, or old archive removal.
set -uo pipefail
umask 077
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$ROOT/.artifacts/storage-safety"
OUT=$(mktemp -d "$ROOT/.artifacts/storage-safety/tests-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
export ASB_STORAGE_TEST_ROOT="$OUT/fixtures"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/tests:$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}"
timeout 60 python3 -B -m unittest -v test_storage_safety test_platform_use \
    test_trenvx_data_archive test_experiment_disk_guard test_trenvx_diagnostic_metrics \
    > "$OUT/tests.log" 2>&1
code=$?
python3 -B - "$OUT" "$code" <<'PY'
import hashlib, json, re, sys
from pathlib import Path
out = Path(sys.argv[1]); code = int(sys.argv[2]); log = (out/'tests.log').read_text()
count = re.search(r'^Ran (\d+) tests? in ', log, re.M)
result = dict(status='PASS' if code == 0 and count else 'FAIL', exit_code=code,
              tests=int(count.group(1)) if count else None,
              scope='small-file and owned-process regressions; real VM stop/cleanup NOT_VERIFIED')
# Use the script directory from PYTHONPATH, independent of the caller's cwd.
import storage_supervisor
root = Path(storage_supervisor.__file__).resolve().parents[1]
names = ['storage_supervisor.py','storage_lifecycle.py','storage_observer.py','use_platform.py',
         'smoke_trenvx_service.py','archive_trenvx_run_data.py','trenvx_diagnostic_metrics.py',
         'test-storage-safety.sh']
result['source_sha256'] = {name:hashlib.sha256((root/'scripts'/name).read_bytes()).hexdigest() for name in names}
with (out/'result.json').open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
print('status='+result['status']+' result_dir='+str(out))
PY
report_code=$?
if (( code != 0 )); then exit "$code"; fi
exit "$report_code"
