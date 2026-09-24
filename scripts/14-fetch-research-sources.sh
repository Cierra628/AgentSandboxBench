#!/usr/bin/env bash
# Fetch pinned sources without replacing existing checkouts or initializing nested gitlinks.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
while IFS=$'\t' read -r name url commit; do
  dest="$ROOT/$name"
  if [[ ! -e $dest ]]; then
    timeout -k 3s 120s git clone --no-recurse-submodules "$url" "$dest"
    git -C "$dest" checkout --detach "$commit"
  fi
  [[ $(git -C "$dest" rev-parse HEAD) == "$commit" ]] || {
    echo "version mismatch: $dest; existing checkout left unchanged" >&2; exit 1;
  }
done < <(python3 - "$ROOT/configs/source-revisions.json" <<'PY'
import json,sys
for name,entry in json.load(open(sys.argv[1])).items():
    print(name,entry['url'],entry['commit'],sep='\t')
PY
)
timeout -k 3s 120s git -C "$ROOT/IncrementalDAX_moti" \
  -c submodule.baselines/AgentENV.url=https://github.com/132ash/AgentENV_forked.git \
  -c submodule.baselines/TrEnv-X.url=https://github.com/132ash/TrEnv-X_forked.git \
  submodule update --init -- baselines/AgentENV baselines/TrEnv-X
patch="$ROOT/patches/incrementaldax-platform.patch"
if git -C "$ROOT/IncrementalDAX_moti" apply --reverse --check "$patch" 2>/dev/null; then
  echo 'platform patch already applied'
else
  git -C "$ROOT/IncrementalDAX_moti" apply --check "$patch"
  git -C "$ROOT/IncrementalDAX_moti" apply "$patch"
fi
