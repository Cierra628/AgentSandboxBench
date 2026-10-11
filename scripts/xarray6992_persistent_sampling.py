"""Exp3 runner variant: one reaped sampler; frozen commands remain unchanged."""
import argparse
import json
from pathlib import Path
import resource
import signal
import threading
import time


def instrument(text):
    begin=text.index('sample_memory() {');end=text.index('sample_memory baseline 000',begin)
    functions='''sample_memory() { printf '%s\\t%s\\n' "$1" "$2" > "$out/.memory-phase"; }
sample_vmstat() { :; }
collector_pid=''
stop_collector() {
  local rc=$?
  trap - EXIT
  if [[ -n $collector_pid ]]; then
    kill -TERM "$collector_pid" 2>/dev/null || true
    if ! wait "$collector_pid"; then rc=1; fi
  fi
  exit "$rc"
}
sample_memory baseline 000
python3 /workspace/xarray6992-persistent-sampling.py --directory "$out" --interval "$sample_interval" \\
  --sandbox "$sandbox_label" --branch "$branch" --round "$round" &
collector_pid=$!
trap stop_collector EXIT
for ((attempt=0; attempt<100; attempt++)); do
  test -s "$out/.memory-ready" && break
  kill -0 "$collector_pid" || exit 1
  sleep 0.01
done
test -s "$out/.memory-ready" || exit 1
'''
    start='  monitor_step "$step" & monitor_pid=$!'
    stop='  kill "$monitor_pid" 2>/dev/null || true\n  wait "$monitor_pid" 2>/dev/null || true\n'
    assert text.count(start)==text.count(stop)==1
    return (text[:begin]+functions+text[end:]).replace(start,'  sample_memory running "$step"').replace(stop,'')


def collect(args):
    # Reuse the validated reader; retain Exp3's original columns and units.
    from guest_memory_collector import counters,VMFIELDS
    fields='MemTotal MemFree Buffers Cached Shmem AnonPages Active(file) Inactive(file) Dirty'.split()
    stop=threading.Event();started=time.monotonic_ns();count=0
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    def sample(memory,vmstat):
        nonlocal count
        phase=(args.directory/'.memory-phase').read_text().strip().split('\t')
        if len(phase)!=2:phase=['unknown','NA']
        prefix=[time.time_ns(),phase[0],args.sandbox,args.branch,args.round,phase[1]]
        memory.write('\t'.join(map(str,[*prefix,*counters(args.proc_root/'meminfo',fields,True)]))+'\n')
        vmstat.write('\t'.join(map(str,[*prefix,*counters(args.proc_root/'vmstat',VMFIELDS)]))+'\n')
        count+=1
    assert args.interval>=.1 and args.directory.is_dir()
    with (args.directory/'guest-memory.tsv').open('a',buffering=1) as memory,(args.directory/'guest-vmstat.tsv').open('a',buffering=1) as vmstat:
        sample(memory,vmstat);(args.directory/'.memory-ready').write_text('ready\n')
        while not stop.wait(args.interval):sample(memory,vmstat)
        sample(memory,vmstat)
    usage=resource.getrusage(resource.RUSAGE_SELF)
    (args.directory/'collector-status.json').write_text(json.dumps(dict(status='PASS',samples=count,
        elapsed_ns=time.monotonic_ns()-started,cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime,
        maxrss_kib=usage.ru_maxrss,scope='one sampler per segment; asynchronous phase markers may miss short boundaries'),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--interval',type=float,required=True)
    for name in ('sandbox','branch','round'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--proc-root',type=Path,default=Path('/proc'))
    collect(parser.parse_args())
