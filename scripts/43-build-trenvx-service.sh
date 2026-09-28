#!/usr/bin/env bash
# Build pinned service code into project-local runtime; do not start services.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
umask 077
case "${1:-all}" in
  all) ASB_BUILD_PACKAGES=(orchestrator template-manager shared);;
  template-manager) ASB_BUILD_PACKAGES=(template-manager);;
  *) echo 'usage: bash scripts/43-build-trenvx-service.sh [all|template-manager]' >&2; exit 2;;
esac
ASB_BUILD_OUT="$ROOT/.artifacts/trenvx-service-build/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$ASB_BUILD_OUT" "$ROOT/runtime/trenvx-service-bin"
stage=preflight
finish() {
  local rc=$?
  trap - EXIT
  python3 - "$ASB_BUILD_OUT/result.json" "$stage" "$rc" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(dict(status='PASS' if sys.argv[3]=='0' else 'FAIL',
    last_stage=sys.argv[2],exit_code=int(sys.argv[3]),scope='service build only'),indent=2)+'\n')
PY
  printf 'exit_code=%s stage=%s result_dir=%s\n' "$rc" "$stage" "$ASB_BUILD_OUT"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export GOPATH="$ROOT/runtime/go" GOCACHE="$ROOT/runtime/go-cache"
# The service modules require Go 1.23; envd has a separate older module.
export GOTOOLCHAIN=go1.23.0 GOMAXPROCS=4
python3 - "$ROOT" > "$ASB_BUILD_OUT/revisions.json" <<'PY'
import json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]);fork=root/'IncrementalDAX_moti/baselines/TrEnv-X'
expected=json.loads((root/'configs/source-revisions.json').read_text())['IncrementalDAX_moti']['submodules']['baselines/TrEnv-X']
actual=subprocess.check_output(['git','-c',f'safe.directory={fork}','-C',str(fork),'rev-parse','HEAD'],text=True).strip()
assert actual==expected
print(json.dumps(dict(fork_commit=actual,toolchain='go1.23.0',buildvcs=False)))
PY
for stage in "${ASB_BUILD_PACKAGES[@]}"; do
  target=.
  name=$stage
  if [[ $stage == shared ]]; then target=./utils/cmd/bind_mount.go; name=bind_mount; fi
  printf 'running=%s log=%s\n' "$stage" "$ASB_BUILD_OUT/$stage.log"
  timeout 300s env CGO_ENABLED=0 go -C "$ROOT/IncrementalDAX_moti/baselines/TrEnv-X/packages/$stage" \
    build -buildvcs=false -p 4 -o "$ROOT/runtime/trenvx-service-bin/$name" "$target" > "$ASB_BUILD_OUT/$stage.log" 2>&1
  if [[ $stage == template-manager ]]; then
    stage=template-copy-test
    timeout 60s go -C "$ROOT/IncrementalDAX_moti/baselines/TrEnv-X/packages/template-manager" \
      test ./build -run '^TestTemplateCopyPreservesLargeSparseTail$' -v -count=1 -timeout=30s > "$ASB_BUILD_OUT/$stage.log" 2>&1
  fi
done
sha256sum "$ROOT/runtime/trenvx-service-bin/"* > "$ASB_BUILD_OUT/binaries.sha256"
