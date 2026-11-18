#!/usr/bin/env bash
# Explicit non-VM subset. Temporary collector files/processes are owned by tests.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mkdir -p .artifacts
export MPLCONFIGDIR="$ROOT/.artifacts/matplotlib"
for name in host_copy_summary release_light_sampler light_monitor_overhead multi_host_overhead grpo_monitor_off grpo_light_pair grpo_light_repeats host_query_control host_cold_file_read host_grpo_cold_read host_grpo_cold_repeats host_private_files host_full_rootfs_control host_bpo_rootfs_control host_rootfs_layers; do
  python3 -B -m unittest discover -s tests -p "test_${name}.py" -v
done
