#!/usr/bin/env bash
# Synthetic inputs and owned temporary processes only; no AgentENV service or VM.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mkdir -p .artifacts
export MPLCONFIGDIR="$ROOT/.artifacts/matplotlib"
export PYTHONDONTWRITEBYTECODE=1
for name in agentenv_cold_start release_light_sampler external_workload external_cython \
  external_five external_call_timing external_guest_relay_rule schedule_smoke_metrics host_file_monitor \
  host_current_tasks prettier_no_proxy host_persistent_input host_persistent_tasks \
  host_expanded_files c4_density_input; do
  [[ -f "tests/test_${name}.py" ]] || { printf 'missing test: %s\n' "$name" >&2; exit 1; }
  python3 -B -m unittest discover -s tests -p "test_${name}.py" -v
done
printf 'status=PASS scope=platform-offline\n'
