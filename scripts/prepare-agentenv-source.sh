#!/usr/bin/env bash
# Prepare a new source checkout only; no install, service, device, or data changes.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET=${1:?usage: prepare-agentenv-source.sh NEW_DIRECTORY [--from LOCAL_REPOSITORY]}
SOURCE=https://github.com/kvcache-ai/AgentENV.git
if [[ $# -gt 1 ]]; then
  [[ $# -eq 3 && $2 == --from ]] || { echo 'expected: NEW_DIRECTORY [--from LOCAL_REPOSITORY]' >&2; exit 2; }
  SOURCE=$3
fi
[[ ! -e "$TARGET" ]] || { echo 'target must not exist' >&2; exit 2; }
BASE=2f48c9c78ec58e4d73c49aa07cb81d984a4a4184
git clone --no-hardlinks --no-checkout -- "$SOURCE" "$TARGET"
git -C "$TARGET" checkout --detach "$BASE"
git -C "$TARGET" apply --check "$ROOT/patches/agentenv-v0.1-measurement.patch"
git -C "$TARGET" apply "$ROOT/patches/agentenv-v0.1-measurement.patch"
printf 'status=PASS source_dir=%s base_commit=%s\n' "$TARGET" "$BASE"
