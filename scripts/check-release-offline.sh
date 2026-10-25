#!/usr/bin/env bash
# Explicit non-VM subset. Temporary collector files/processes are owned by tests.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mkdir -p .artifacts
export MPLCONFIGDIR="$ROOT/.artifacts/matplotlib"
for name in host_copy_summary release_light_sampler host_full_rootfs_control host_bpo_rootfs_control host_rootfs_layers; do
  python3 -B -m unittest discover -s tests -p "test_${name}.py" -v
done
