#!/usr/bin/env bash
# Install pinned SDKs in checkout-local venvs; never contacts a sandbox service.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
umask 077
ASB_CLIENT_KIND=${1:-all}
case "$ASB_CLIENT_KIND" in trenvx|agentenv|all) ;; *) echo 'usage: bash scripts/42-prepare-python-clients.sh [trenvx|agentenv|all]' >&2; exit 2;; esac
ASB_CLIENT_OUT="$ROOT/.artifacts/python-clients/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$ASB_CLIENT_OUT" "$ROOT/runtime"
stage=preflight
trenvx_status=NOT_RUN
agentenv_status=NOT_RUN
finish() {
  local rc=$? status=FAIL
  trap - EXIT
  if ((rc == 0)); then status=PASS; fi
  python3 - "$ASB_CLIENT_OUT/result.json" "$status" "$stage" "$rc" "$trenvx_status" "$agentenv_status" <<'PY_RESULT'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(dict(status=sys.argv[2],last_stage=sys.argv[3],
 exit_code=int(sys.argv[4]),trenvx=sys.argv[5],agentenv=sys.argv[6],
 scope='pinned Python dependencies and SDK imports only; no service connection'),indent=2)+'\n')
PY_RESULT
  printf 'status=%s stage=%s result_dir=%s\n' "$status" "$stage" "$ASB_CLIENT_OUT"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ $ASB_CLIENT_KIND == trenvx || $ASB_CLIENT_KIND == all ]]; then
  stage=trenvx-lock
  python3 - "$ROOT" <<'PY'
import json,subprocess,sys,tomllib
from pathlib import Path
root=Path(sys.argv[1])
fork=root/'IncrementalDAX_moti/baselines/TrEnv-X'
expected=json.loads((root/'configs/source-revisions.json').read_text())['IncrementalDAX_moti']['submodules']['baselines/TrEnv-X']
assert subprocess.check_output(['git','-C',str(fork),'rev-parse','HEAD'],text=True).strip()==expected
sdk=fork/'sandbox-sdk'
lock=tomllib.loads((sdk/'poetry.lock').read_text())
packages={p['name']:p for p in lock['package']}
# Include the dependency closure of the SDK's main group. No autogen/dev extras.
pending=[n for n in tomllib.loads((sdk/'pyproject.toml').read_text())['tool']['poetry']['dependencies'] if n!='python']
seen=set()
while pending:
    name=pending.pop().lower().replace('_','-')
    if name in seen:continue
    seen.add(name)
    pending.extend(packages[name].get('dependencies',{}))
lines=['# Exported from fixed TrEnv-X sandbox-sdk/poetry.lock; includes conditional dependencies.']
for name in sorted(seen):
    p=packages[name]
    hashes=sorted({f['hash'] for f in p['files']})
    assert hashes, f'missing hashes: {name}'
    lines.append(f"{name}=={p['version']} "+' '.join('--hash='+h for h in hashes))
(root/'runtime/trenvx-sdk-requirements.txt').write_text('\n'.join(lines)+'\n')
PY
  stage=trenvx-install
  python3 -m venv "$ROOT/runtime/trenvx-venv"
  chmod 700 "$ROOT/runtime/trenvx-venv"
  "$ROOT/runtime/trenvx-venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 \
    --only-binary=:all: --require-hashes --no-deps -r "$ROOT/runtime/trenvx-sdk-requirements.txt" > "$ASB_CLIENT_OUT/trenvx-install.log" 2>&1
  "$ROOT/runtime/trenvx-venv/bin/python" - "$ROOT" <<'PY'
import site,sys
from pathlib import Path
root=Path(sys.argv[1])
Path(site.getsitepackages()[0],'asb-trenvx-sdk.pth').write_text(str(root/'IncrementalDAX_moti/baselines/TrEnv-X/sandbox-sdk')+'\n')
PY
  stage=trenvx-check
  "$ROOT/runtime/trenvx-venv/bin/python" -m pip check > "$ASB_CLIENT_OUT/trenvx-check.log" 2>&1
  "$ROOT/runtime/trenvx-venv/bin/python" -m pip freeze > "$ASB_CLIENT_OUT/trenvx-freeze.txt"
  "$ROOT/runtime/trenvx-venv/bin/python" - <<'PY' > "$ASB_CLIENT_OUT/trenvx-import.log" 2>&1
from sandbox_sdk.sandbox import Sandbox
from sandbox_sdk.api import SandboxStub
assert callable(Sandbox.create)
print('PASS: pinned SDK imports; no service connection')
PY
  trenvx_status=PASS
  printf 'status=PASS client=trenvx result_dir=%s\n' "$ASB_CLIENT_OUT"
fi
if [[ $ASB_CLIENT_KIND == agentenv || $ASB_CLIENT_KIND == all ]]; then
  stage=agentenv-install
  python3 -m venv "$ROOT/runtime/e2b-venv"
  chmod 700 "$ROOT/runtime/e2b-venv"
  "$ROOT/runtime/e2b-venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 \
    -r "$ROOT/scripts/e2b-requirements.txt" > "$ASB_CLIENT_OUT/agentenv-install.log" 2>&1
  stage=agentenv-check
  "$ROOT/runtime/e2b-venv/bin/python" -m pip check > "$ASB_CLIENT_OUT/agentenv-check.log" 2>&1
  "$ROOT/runtime/e2b-venv/bin/python" -m pip freeze > "$ASB_CLIENT_OUT/agentenv-freeze.txt"
  "$ROOT/runtime/e2b-venv/bin/python" - <<'PY' > "$ASB_CLIENT_OUT/agentenv-import.log" 2>&1
from e2b import Sandbox
from importlib.metadata import version
assert version('e2b')=='2.26.0'
assert callable(Sandbox.create)
print('PASS: pinned E2B SDK imports; no service connection')
PY
  agentenv_status=PASS
  printf 'status=PASS client=agentenv result_dir=%s\n' "$ASB_CLIENT_OUT"
fi
