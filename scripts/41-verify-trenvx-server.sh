#!/usr/bin/env bash
# Small acceptance checks only: no shared service changes or host cgroup writes.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
umask 077
ASB_VERIFY_RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)-$$
ASB_VERIFY_OUT="$ROOT/.artifacts/trenvx-server-validation/$ASB_VERIFY_RUN_ID"
ASB_VERIFY_KERNEL=${ASB_TEST_KERNEL:-/var/lib/trenvx/kernels/ch-6.1.134/vmlinux}
mkdir -p "$ASB_VERIFY_OUT"
stage=preflight
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if ((rc == 0)); then status=PASS; fi
  python3 - "$ASB_VERIFY_OUT/result.json" "$status" "$stage" "$rc" "$ASB_VERIFY_RUN_ID" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    'status':sys.argv[2], 'last_stage':sys.argv[3], 'exit_code':int(sys.argv[4]),
    'run_id':sys.argv[5],
    'scope':'pinned Python SDK/CLI checks, offline contracts, envd build, isolated QEMU kernel/launchers',
    'full_controller_service_verified':False,
    'full_agentenv_service_verified':False,
},indent=2)+'\n')
PY
  printf 'status=%s stage=%s result_dir=%s\n' "$status" "$stage" "$ASB_VERIFY_OUT"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
phase() {
  stage=$1
  shift
  printf 'running=%s log=%s\n' "$stage" "$ASB_VERIFY_OUT/$stage.log"
  "$@" > "$ASB_VERIFY_OUT/$stage.log" 2>&1
}
for ASB_VERIFY_TOOL in python3 go gcc git file ldd qemu-system-x86_64 timeout; do
  command -v "$ASB_VERIFY_TOOL" >/dev/null || { printf 'missing tool: %s\n' "$ASB_VERIFY_TOOL" >&2; exit 1; }
done
[[ -r "$ASB_VERIFY_KERNEL" && -x /usr/bin/busybox && -x /bin/bash ]]
file /usr/bin/busybox > "$ASB_VERIFY_OUT/busybox.txt"
python3 - "$ASB_VERIFY_OUT/busybox.txt" <<'PY'
import sys
assert sys.version_info >= (3,11), 'Python 3.11+ required'
assert 'statically linked' in open(sys.argv[1]).read(), 'static BusyBox required'
PY
# All dependency caches and builds are local to this experiment checkout.
export GOCACHE="$ROOT/runtime/go-cache"
export GOPATH="$ROOT/runtime/go"
mkdir -p "$GOCACHE" "$GOPATH" "$ROOT/runtime/bin"
chmod 700 "$GOCACHE" "$GOPATH" "$ROOT/runtime/bin"
python3 - "$ROOT" "$ASB_VERIFY_KERNEL" "$ASB_VERIFY_OUT/environment.json" <<'PY'
import hashlib,json,os,subprocess,sys
from pathlib import Path
root,kernel,output=map(Path,sys.argv[1:])
def git(path):return subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
revisions=json.loads((root/'configs/source-revisions.json').read_text())
for source,commit in [(root/'AgentENV',revisions['AgentENV']['commit']),
                      (root/'IncrementalDAX_moti',revisions['IncrementalDAX_moti']['commit']),
                      (root/'IncrementalDAX_moti/baselines/AgentENV',revisions['IncrementalDAX_moti']['submodules']['baselines/AgentENV']),
                      (root/'IncrementalDAX_moti/baselines/TrEnv-X',revisions['IncrementalDAX_moti']['submodules']['baselines/TrEnv-X'])]:
    assert git(source)==commit, f'pinned version mismatch: {source}'
with kernel.open('rb') as f:kernel_sha=hashlib.file_digest(f,'sha256').hexdigest()
output.write_text(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'workspace':str(root),
 'platform_commit':git(root),'sources':revisions,'python':sys.version,
 'go':subprocess.check_output(['go','version'],text=True).strip(),
 'qemu':subprocess.check_output(['qemu-system-x86_64','--version'],text=True).splitlines()[0],
 'kernel':str(kernel),'kernel_sha256':kernel_sha},indent=2)+'\n')
PY
phase patches python3 scripts/40-apply-trenvx-task-cgroup.py --check
phase python-clients python3 - "$ROOT" <<'PY'
import subprocess,sys
from pathlib import Path
root=Path(sys.argv[1])
for name,statement in [('trenvx-venv','from sandbox_sdk.sandbox import Sandbox'),('e2b-venv','from e2b import Sandbox')]:
    python=root/'runtime'/name/'bin/python'
    assert python.is_file(), 'prepare clients first: bash scripts/42-prepare-python-clients.sh'
    subprocess.run([str(python),'-m','pip','check'],check=True)
    subprocess.run([str(python),'-c',statement+"; assert callable(Sandbox.create); print('SDK import PASS')"],check=True)
version=subprocess.check_output([str(root/'runtime/bin/aenv'),'--version'],text=True).strip()
assert version=='aenv 0.2.2', version
print(version)
PY
phase go-contracts go -C backend/trenvx/taskcgroup test -race -v -count=1 ./...
phase python-contracts python3 -m unittest discover -s tests -v
phase envd-packages go -C IncrementalDAX_moti/baselines/TrEnv-X/packages/envd test -count=1 ./internal/process ./internal/terminal ./internal/taskcgroup
phase envd-build env CGO_ENABLED=0 go -C IncrementalDAX_moti/baselines/TrEnv-X/packages/envd build -o "$ROOT/runtime/bin/envd-task-cgroup" .
phase isolated-guest python3 scripts/39-smoke-trenvx-task-cgroup.py --kernel "$ASB_VERIFY_KERNEL" --launchers
