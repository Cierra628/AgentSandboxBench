"""Opt-in controller timings and boundary-only VMM memory observations."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time


def vmm_snapshot(sandbox_id, group_name, executable, *, proc_root=Path('/proc'),
                 cgroup_root=Path('/sys/fs/cgroup')):
    started = time.monotonic_ns()
    socket = f'/tmp/vmm-{sandbox_id}.socket'
    candidates = []
    for proc in proc_root.glob('[0-9]*'):
        try:
            argv = (proc/'cmdline').read_bytes().split(b'\0')
            if socket.encode() in argv and Path(os.readlink(proc/'exe')) == executable.resolve():
                candidates.append(proc)
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(candidates) != 1:
        raise RuntimeError(f'expected one VMM for {sandbox_id}, found {len(candidates)}')
    proc = candidates[0]
    start_ticks = (proc/'stat').read_text().rsplit(')', 1)[1].split()[19]
    membership = (proc/'cgroup').read_text()
    expected = f'/{group_name}/{sandbox_id}'
    if membership.splitlines() != [f'0::{expected}']:
        raise RuntimeError(f'VMM cgroup mismatch: expected {expected}, got {membership!r}')
    members = (cgroup_root/group_name/sandbox_id/'cgroup.procs').read_text().split()
    if proc.name not in members:
        raise RuntimeError('VMM PID absent from sandbox cgroup.procs')
    raw = (proc/'smaps_rollup').read_text()
    values = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == 'kB':
            values[fields[0].rstrip(':')] = int(fields[1]) * 1024
    if 'Pss' not in values or 'Rss' not in values:
        raise RuntimeError('smaps_rollup is missing Pss or Rss')
    if (proc/'stat').read_text().rsplit(')', 1)[1].split()[19] != start_ticks:
        raise RuntimeError('VMM process identity changed during scan')
    if (proc/'cgroup').read_text() != membership:
        raise RuntimeError('VMM cgroup changed during scan')
    return dict(status='PASS', sandbox_id=sandbox_id, pid=int(proc.name),
        pid_scope='local PID in the private service PID namespace',
        start_time_ticks=int(start_ticks), cgroup=expected,
        cgroup_membership_verified=True, memory_bytes=values,
        raw_smaps_rollup=raw, timestamp_ns=time.time_ns(),
        scan_elapsed_ns=time.monotonic_ns()-started,
        scope='host VMM process smaps_rollup; not guest memory or complete DAX residency')


class DiagnosticMetrics:
    def __init__(self, out, enabled):
        self.out = out
        self.enabled = enabled

    @contextmanager
    def phase(self, name):
        if not self.enabled:
            yield
            return
        begin = time.monotonic_ns()
        row = dict(phase=name, start_monotonic_ns=begin, timestamp_ns=time.time_ns(), status='FAIL')
        try:
            yield
            row['status'] = 'PASS'
        except BaseException as exc:
            row['error'] = repr(exc)
            raise
        finally:
            row['end_monotonic_ns'] = time.monotonic_ns()
            row['elapsed_ns'] = row['end_monotonic_ns']-begin
            with (self.out/'diagnostic-events.jsonl').open('a') as stream:
                stream.write(json.dumps(row)+'\n')

    def snapshot(self, label, sandbox_id, group_name, executable):
        if not self.enabled:
            return
        with self.phase('vmm-scan:'+label):
            row = vmm_snapshot(sandbox_id, group_name, executable)
            (self.out/('vmm-'+label+'.json')).write_text(json.dumps(row,indent=2)+'\n')
