#!/usr/bin/env bash
# One private service, one native restore, no guest workload during observation.
set -euo pipefail
cd "$(dirname "$0")/.."; root=$PWD
if [[ $# == 0 ]]; then
  unit=asbvmmpaused$(date -u +%Y%m%dt%H%M%S)
  out=$root/.artifacts/$unit; mkdir "$out"
  rc=0
  systemd-run --quiet --wait --pipe --collect --unit="${unit}control" --slice="$unit.slice" \
    -p WorkingDirectory="$root" -p MemoryAccounting=yes -p IOAccounting=yes \
    bash "$root/scripts/observe_paused_host_layer.sh" "$unit" "$out" >"$out/launch.log" 2>&1 || rc=$?
  PYTHONPATH=scripts python3 -B -c 'from pathlib import Path; import sys; from run_live_child_calibration import release_empty_slice;print(release_empty_slice(Path("/sys/fs/cgroup")/(sys.argv[1]+".slice")))' "$unit"
  echo "exit=$rc result_dir=$out"; exit "$rc"
fi
unit=$1; out=$2; cache="$root/.artifacts/prettier6604/$unit"
export ASB_VMM_CHILD_CGROUP_CHECK=$out ASB_VMM_CGROUP_PARENT=/sys/fs/cgroup/$unit.slice
export ASB_VMM_REQUIRE_IO=on ASB_ISOLATED_UNUSED_NETWORK_POOLS=on PYTHONDONTWRITEBYTECODE=1
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY='*' no_proxy='*'
kernel=$root/.artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin
server=$root/.artifacts/light-restore-pid-build-20261117T083141Z/server
provider=$root/.artifacts/provider-segments-build-20261016/uvm-ublk-daemon
for f in "$kernel" "$server" "$provider"; do test -r "$f"; done
PYTHONPATH=scripts python3 -B -c 'from run_live_child_calibration import *; b=capacity_budget(available(ROOT),24*GIB);print(b);assert b["ready"];s=json.loads(subprocess.check_output(["docker","inspect",SOURCE],timeout=10))[0];assert s["Id"]==SOURCE_ID and not s["State"]["Running"] and s["HostConfig"]["RestartPolicy"]["Name"]=="no"'
test ! -e "$cache"
started=0
finish() {
  local rc=$?; trap - EXIT
  if (( started )); then timeout 60 docker stop -t 30 "$ASB_AGENTENV_SOURCE_SERVICE" || rc=1; fi
  if [[ -f "$cache/resources.json" ]]; then timeout 90 python3 -B scripts/prepare_prettier6604_cache.py "$cache" --release || rc=1; fi
  for g in "$ASB_VMM_CGROUP_PARENT"/vmm-*; do if [[ -d "$g" ]]; then rmdir "$g" || rc=1; fi; done
  exit "$rc"
}
trap finish EXIT
gcc -O2 -Wall -Wextra scripts/vmm_cgroup_launcher.c -o "$out/vmm-cgroup-launcher"
timeout 300 python3 -B scripts/prepare_prettier6604_cache.py "$cache"
timeout 30 docker start "$ASB_AGENTENV_SOURCE_SERVICE"; started=1
curl --noproxy '*' -fsS --retry 40 --retry-connrefused --retry-delay 1 --max-time 2 --retry-max-time 60 http://127.0.0.1:39681/health
timeout 600 python3 -B scripts/validate_isolated_agentenv.py --guest-cache-child-calibration --guest-cache-child-count 2 \
 --guest-cache-rollout-sessions --guest-cache-restored-lower --snapshot-provider-binary "$provider" --snapshot-provider-get-data \
 --diagnostic-server-binary "$server" --diagnostic-guest-kernel "$kernel" --immutable-cache-seed "$cache" --paused-host-cache
