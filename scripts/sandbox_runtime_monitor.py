"""Light guest sampling from tool readiness to cleanup; stop before snapshots."""
import argparse
from contextlib import ExitStack
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid

from guest_memory_collector import counters, FIELDS, VMFIELDS

GUEST_ROOT = '/workspace/asb-runtime-monitor'
SCRIPT = '/workspace/asb-runtime-monitor.py'
MEMFIELDS = [*FIELDS, 'Active(file)', 'Inactive(file)', 'Dirty']
PREFIX = ['timestamp_ns', 'monotonic_ns', 'elapsed_ns', 'sandbox_id', 'role', 'session_id', 'phase', 'round', 'step']
DAMON_FIELDS = ['enabled', 'kdamond_pid', 'nr_reclaim_tried_regions', 'bytes_reclaim_tried_regions',
                'nr_reclaimed_regions', 'bytes_reclaimed_regions', 'nr_quota_exceeds']


def damon_values(directory):
    values = [(directory/name).read_text().strip() for name in DAMON_FIELDS]
    assert values[0] in ('Y', 'N'), 'invalid DAMON enabled state'
    assert int(values[1]) >= -1 and all(int(v) >= 0 for v in values[2:]), 'invalid DAMON counters'
    return values


def process_identity(pid):
    text = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, start_ticks=text[19], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def collect(args):
    assert math.isfinite(args.interval) and args.interval >= .1
    assert math.isfinite(args.max_seconds) and 0 < args.max_seconds <= 1800
    directory = args.directory
    directory.mkdir(parents=True, exist_ok=False)
    if getattr(args,'initial_phase',None) is not None:
        args.phase_file.write_text(args.initial_phase+'\t\t\n')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    identity = process_identity(os.getpid())
    light = getattr(args, 'light', False)
    damon_observe = getattr(args, 'damon_observe', False)
    if getattr(args, 'performance', False):
        from guest_performance import cpu, disks, CPUFIELDS
        from guest_diskstats import FIELDS as DISKFIELDS
    (directory/'identity.json').write_text(json.dumps(identity)+'\n')
    started = time.monotonic_ns(); count = 0; status = 'FAIL'; error = None
    try:
        with ExitStack() as stack:
            memory=stack.enter_context((directory/'guest-memory.tsv').open('x',buffering=1))
            memory.write('\t'.join(PREFIX+[k.replace('(', '_').replace(')', '')+'_kib' for k in MEMFIELDS])+'\n')
            if damon_observe:
                parameters=args.sys_root/'module/damon_reclaim/parameters'
                damon_values(parameters)  # Required state/counters must exist before readiness.
                names=[*DAMON_FIELDS,'min_age','sample_interval','aggr_interval','quota_ms','quota_sz',
                       'quota_reset_interval_ms','wmarks_high','wmarks_mid','wmarks_low','wmarks_interval',
                       'skip_anon','monitor_region_start','monitor_region_end']
                settings={name:(parameters/name).read_text().strip() for name in names if (parameters/name).exists()}
                (directory/'damon-settings.json').write_text(json.dumps(dict(kernel_release=os.uname().release,
                    parameters=settings,scope='read-only; no enabled, commit_inputs or reclaim writes'),indent=2)+'\n')
                damon=stack.enter_context((directory/'guest-damon.tsv').open('x',buffering=1))
                damon.write('\t'.join(PREFIX+['read_start_monotonic_ns','read_end_monotonic_ns',*DAMON_FIELDS])+'\n')
            if not light:
                vmstat=stack.enter_context((directory/'guest-vmstat.tsv').open('x',buffering=1))
                vmstat.write('\t'.join(PREFIX+VMFIELDS)+'\n')
            if getattr(args,'performance',False):
                cpu_file=stack.enter_context((directory/'guest-cpu.tsv').open('x',buffering=1))
                disk_file=stack.enter_context((directory/'guest-diskstats.tsv').open('x',buffering=1))
                cpu_file.write('\t'.join(PREFIX+[k+'_ticks' for k in CPUFIELDS]+['cpu_count'])+'\n')
                disk_file.write('\t'.join(PREFIX+['device','major','minor','is_partition',*DISKFIELDS])+'\n')
            while True:
                try:
                    phase = args.phase_file.read_text().rstrip('\n').split('\t')
                except FileNotFoundError:
                    phase = []
                if len(phase) != 3: phase = ['unknown', '', '']
                now = time.monotonic_ns()
                prefix = [time.time_ns(), now, now-started, args.sandbox_id, args.role, args.session_id, *phase]
                memory.write('\t'.join(map(str, [*prefix, *counters(args.proc_root/'meminfo', MEMFIELDS, True)]))+'\n')
                if damon_observe:
                    before=time.monotonic_ns();values=damon_values(parameters);after=time.monotonic_ns()
                    damon.write('\t'.join(map(str,[*prefix,before,after,*values]))+'\n')
                if not light:
                    vmstat.write('\t'.join(map(str, [*prefix, *counters(args.proc_root/'vmstat', VMFIELDS)]))+'\n')
                if getattr(args,'performance',False):
                    cpu_file.write('\t'.join(map(str,[*prefix,*cpu((args.proc_root/'stat').read_text())]))+'\n')
                    for row in disks((args.proc_root/'diskstats').read_text(),args.sys_root):
                        disk_file.write('\t'.join(map(str,[*prefix,*row]))+'\n')
                count += 1
                if count == 1: (directory/'ready').write_text('ready\n')
                if stop.is_set():
                    status = 'PASS'; break
                if (now-started)/1e9 >= args.max_seconds:
                    raise TimeoutError('collector lifetime budget exceeded')
                stop.wait(args.interval)
    except Exception as exc:
        error = repr(exc)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    receipt = dict(status=status, error=error, **identity, sandbox_id=args.sandbox_id, role=args.role,
                   session_id=args.session_id, samples=count, interval_seconds=args.interval,
                   elapsed_ns=time.monotonic_ns()-started, cpu_user_seconds=usage.ru_utime,
                   cpu_system_seconds=usage.ru_stime, maxrss_kib=usage.ru_maxrss,
                   scope='guest logical counters; collector maxrss is not private memory; no physical deduplication')
    if getattr(args,'performance',False):receipt.update(performance_version=1,clock_ticks=os.sysconf('SC_CLK_TCK'),
        performance_scope='guest-wide CPU (guest ticks not double counted), per-device diskstats (512-byte sectors); includes sampler and other guest work')
    if light: receipt['profile'] = 'light'
    if damon_observe: receipt['damon_observed'] = True
    (directory/'collector-status.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return 0 if status == 'PASS' else 1


def ready(directory, code_sha256=None):
    end=time.monotonic()+5
    while not (directory/'ready').exists():
        if (directory/'collector-status.json').exists():raise RuntimeError('collector exited before readiness')
        if time.monotonic()>=end:raise TimeoutError('collector readiness timeout')
        time.sleep(.01)
    identity=json.loads((directory/'identity.json').read_text())
    assert process_identity(identity['pid'])==identity
    value=dict(status='STARTED',**identity)
    if code_sha256 is not None:
        expected=json.loads(code_sha256)
        value['code_sha256']={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in expected}
        assert value['code_sha256']==expected,'restored monitor code differs from this run'
    print(json.dumps(value))


def reaped(directory):
    identity=json.loads((directory/'identity.json').read_text());end=time.monotonic()+5
    while True:
        try:present=process_identity(identity['pid'])==identity
        except FileNotFoundError:present=False
        if not present:break
        if time.monotonic()>=end:raise TimeoutError('collector still exists, including an unreaped zombie')
        time.sleep(.01)
    print(json.dumps(dict(status='REAPED',**identity)))


def process_probe():
    # A single process avoids the old shell probe's repeated cat/sleep children,
    # whose /proc directories could disappear during an original find action.
    directory=Path('/workspace/exp1-process-probe');directory.mkdir(exist_ok=True)
    (directory/'pid').write_text(str(os.getpid())+'\n')
    count=0
    while True:
        count+=1
        temporary=directory/'count.tmp';temporary.write_text(str(count)+'\n')
        temporary.replace(directory/'count');time.sleep(.1)


def stop(directory):
    identity = json.loads((directory/'identity.json').read_text())
    assert process_identity(identity['pid']) == identity, 'collector PID identity changed'
    command = Path(f'/proc/{identity["pid"]}/cmdline').read_bytes().split(b'\0')
    assert b'collect' in command and str(directory).encode() in command, 'not the owned collector'
    os.kill(identity['pid'], signal.SIGTERM)
    end = time.monotonic()+5
    while True:
        state = Path(f'/proc/{identity["pid"]}/stat')
        exited = not state.exists()
        if not exited:
            try: exited = state.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
            except (FileNotFoundError,ProcessLookupError): exited = True
        if exited: break
        if time.monotonic() >= end: raise TimeoutError('owned collector did not exit; retained for diagnosis')
        time.sleep(.01)
    receipt = json.loads((directory/'collector-status.json').read_text())
    assert receipt['status'] == 'PASS', receipt
    print(json.dumps(receipt))


def boundary_runner(text, guest_phases=False):
    """Keep original boundary samples and frozen actions; one lifecycle sampler does periodic reads."""
    start = '  monitor_step "$step" & monitor_pid=$!'
    end = '  kill "$monitor_pid" 2>/dev/null || true\n  wait "$monitor_pid" 2>/dev/null || true\n'
    assert text.count(start) == text.count(end) == 1
    marker = '\n  if [[ -d '+GUEST_ROOT+' ]]; then\n    printf "%s\\t%s\\t%s\\n" "$phase" "$round" "$step" > '+GUEST_ROOT+'/phase\n  fi'
    if guest_phases:
        # Keep the original boundary row "complete"; only the lifecycle label
        # becomes waiting while downloading/checking the finished segment.
        marker=marker.replace('"$phase"','"${phase/complete/waiting}"')
    needle = '  local phase=$1 step=$2 timestamp_ns'
    assert text.count(needle) == 2
    text = text.replace(needle, needle+marker, 1)
    text = text.replace(start, '  printf "running\\t%s\\t%s\\n" "$round" "$step" > '+GUEST_ROOT+'/phase')
    return text.replace(end, '')


class RuntimeMonitor:
    def __init__(self, raw, cli, container=None, batch_control=False, *, light=False, damon_observe=False):
        self.raw = Path(raw); self.cli = cli; self.active = {}; self.sessions = []
        self.light=light;self.interval=.1
        self.damon_observe=damon_observe
        self.container=None if light else container;self.hosts={};self.host_binding=None;self.host_lock=threading.Lock();self.workers={}
        self.lock = threading.Lock()
        code = self.raw/'runtime-code'; code.mkdir(exist_ok=True)
        names=('sandbox_runtime_monitor.py', 'guest_memory_collector.py')
        if not light:names+=('guest_performance.py','guest_diskstats.py','host_sandbox_monitor.py')
        for name in names:
            shutil.copy2(Path(__file__).parent/name, code/name)
        self.batch_control=batch_control
        if batch_control:
            self.bundle=code/'guest-bundle';self.bundle.mkdir()
            self.code_sha256={}
            for source,remote in [('sandbox_runtime_monitor.py',Path(SCRIPT).name),('guest_memory_collector.py','guest_memory_collector.py'),
                                  ('guest_performance.py','guest_performance.py'),('guest_diskstats.py','guest_diskstats.py')]:
                target=self.bundle/remote;shutil.copy2(code/source,target)
                self.code_sha256[str(Path(SCRIPT).parent/remote)]=hashlib.sha256(target.read_bytes()).hexdigest()

    def write(self):
        with self.lock:
            temporary=self.raw/'.runtime-monitor.tmp'
            temporary.write_text(json.dumps(dict(
                status='PASS' if self.sessions and all(s.get('status') == 'PASS' for s in self.sessions) else 'INCOMPLETE',
                interval_seconds=self.interval, sessions=self.sessions, profile='light' if self.light else 'full',damon_observed=self.damon_observe,
                collector_lifecycle_version=2,guest_launch_mode='envd-managed foreground command, waited for and reaped before snapshot',
                control_batching=self.batch_control,
                guest_performance_version=None if self.light else 1,host_vmm_requested=bool(self.container),host_pss_interval_seconds=None if self.light else 1,
                scope='guest tool readiness to pre-delete; sampling stopped before snapshots and restarted afterwards; no samples while VM paused'), indent=2)+'\n')
            temporary.replace(self.raw/'runtime-monitor.json')

    def mark(self, sid, phase):
        if sid in self.active:
            if sid in self.hosts:self.hosts[sid].phase=phase
            if self.batch_control:
                self.cli('monitor-phase','exec',sid,'python3','-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])',GUEST_ROOT+'/phase',phase+'\t\t\n',timeout=15)
            else:
                self.cli('monitor-phase', 'exec', sid, 'bash', '-lc', 'printf %s '+shlex.quote(phase+'\t\t\n')+' > '+GUEST_ROOT+'/phase', timeout=15)

    def start(self, sid, role, phase='ready', reuse_code=False):
        assert sid not in self.active
        token = str(uuid.uuid4())
        entry = dict(sandbox_id=sid, role=role, session_id=token, status='INCOMPLETE',
                     start_host_monotonic_ns=time.monotonic_ns(), start_host_timestamp_ns=time.time_ns())
        self.sessions.append(entry); self.active[sid] = entry; self.write()
        if self.batch_control:
            entry['code_reused_from_snapshot']=reuse_code
            if not reuse_code:self.cli('monitor-upload-bundle','upload',sid,self.bundle,Path(SCRIPT).parent,timeout=30)
        else:
            uploads=[('sandbox_runtime_monitor.py', SCRIPT), ('guest_memory_collector.py', '/workspace/guest_memory_collector.py')]
            if not self.light:uploads += [('guest_performance.py','/workspace/guest_performance.py'),('guest_diskstats.py','/workspace/guest_diskstats.py')]
            for name, remote in uploads:
                self.cli('monitor-upload', 'upload', sid, Path(__file__).parent/name, remote, timeout=30)
            self.cli('monitor-directory', 'exec', sid, 'mkdir', '-p', GUEST_ROOT, timeout=15)
            self.mark(sid, phase)
        directory = GUEST_ROOT+'/'+token
        entry['guest_directory'] = directory
        # Keep the collector as an envd-managed foreground command. Its parent
        # waits for it; guest PID 1 need not reap an orphan before a snapshot.
        worker_error=[]
        def managed_collect():
            argv=['exec',sid,'python3',SCRIPT,'collect','--directory',directory,
                '--phase-file',GUEST_ROOT+'/phase','--sandbox-id',sid,'--role',role,'--session-id',token,
                '--interval',str(self.interval)]
            argv+=['--light'] if self.light else ['--performance']
            if self.damon_observe:argv+=['--damon-observe']
            if self.batch_control:argv+=['--initial-phase',phase]
            try:self.cli('monitor-collect-'+token,*argv,timeout=1810)
            except BaseException as error:worker_error.append(error)
        worker=threading.Thread(target=managed_collect,daemon=True)
        self.workers[sid]=(worker,worker_error);worker.start()
        ready_argv=['exec',sid,'python3',SCRIPT,'ready','--directory',directory]
        if self.batch_control:ready_argv+=['--code-sha256',json.dumps(self.code_sha256)]
        content=self.cli('monitor-ready',*ready_argv,timeout=15)
        entry['identity'] = json.loads(content); assert entry['identity']['status'] == 'STARTED'
        if self.container:
            from host_sandbox_monitor import Binding,HostMonitor
            with self.host_lock:
                if self.host_binding is None:self.host_binding=Binding(self.container,self.raw)
            directory=self.raw/'host-vmm'/sid/token
            self.hosts[sid]=HostMonitor(self.host_binding,sid,role,token,directory,phase)
            entry['host_directory']=str(directory.relative_to(self.raw))
        self.write()

    def stop(self, sid, reason='cleanup'):
        if sid not in self.active: return
        entry = self.active[sid]; entry['stop_reason'] = reason
        parent = self.raw/'runtime'/sid; parent.mkdir(parents=True, exist_ok=True)
        error = None
        try:
            self.mark(sid, reason)
        except Exception as exc: error = exc
        if sid in self.hosts:
            host=self.hosts.pop(sid)
            try:entry['host_receipt']=host.stop()
            except Exception as exc:
                entry['host_receipt']=host.receipt
                if error is None:error=exc
        try:self.cli('monitor-stop', 'exec', sid, 'python3', SCRIPT, 'stop', '--directory', entry['guest_directory'], timeout=15)
        except Exception as exc:
            if error is None:error=exc
        worker,worker_error=self.workers.pop(sid)
        worker.join(timeout=10)
        try:
            assert not worker.is_alive(),'managed collector command did not finish'
            if worker_error:raise worker_error[0]
            value=json.loads(self.cli('monitor-reaped','exec',sid,'python3',SCRIPT,'reaped','--directory',entry['guest_directory'],timeout=15))
            assert value['status']=='REAPED' and all(value[k]==entry['identity'][k] for k in ('pid','start_ticks','boot_id'))
            entry['collector_reaped']=True
        except Exception as exc:
            if error is None:error=exc
        try:
            self.cli('monitor-download', 'download', sid, entry['guest_directory'], parent, timeout=30)
            path = parent/entry['session_id']
            receipt = json.loads((path/'collector-status.json').read_text())
            assert receipt['status'] == 'PASS' and receipt['sandbox_id'] == sid and receipt['session_id'] == entry['session_id']
            entry.update(raw_directory=str(path.relative_to(self.raw)), receipt=receipt, status='PASS' if error is None else 'FAIL')
        except Exception as exc:
            if error is None: error = exc
        finally:
            entry['end_host_monotonic_ns'] = time.monotonic_ns()
            if error: entry.update(status='FAIL', error=repr(error))
            self.active.pop(sid); self.write()
        if error: raise error


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ready', 'collect', 'stop', 'reaped', 'process-probe'])
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--phase-file', type=Path)
    for name in ('sandbox-id', 'role', 'session-id'): parser.add_argument('--'+name)
    parser.add_argument('--interval', type=float, default=.1)
    parser.add_argument('--max-seconds', type=float, default=1800)
    parser.add_argument('--proc-root', type=Path, default=Path('/proc'))
    parser.add_argument('--sys-root',type=Path,default=Path('/sys'))
    parser.add_argument('--performance',action='store_true')
    parser.add_argument('--light',action='store_true',help='Only guest meminfo; no vmstat, CPU, block I/O or host sampling')
    parser.add_argument('--damon-observe',action='store_true',help='Read-only DAMON state and counters alongside memory samples')
    parser.add_argument('--initial-phase',help='Initialize the phase file before the first collector sample')
    parser.add_argument('--code-sha256',help='Expected code hashes checked during readiness, including inherited files')
    args = parser.parse_args()
    if args.light and args.performance:parser.error('--light and --performance are mutually exclusive')
    if args.action=='process-probe':process_probe();raise SystemExit(0)
    if args.directory is None:parser.error('--directory required for collector actions')
    if args.action == 'collect': raise SystemExit(collect(args))
    if args.action == 'ready': ready(args.directory,args.code_sha256)
    elif args.action=='reaped':reaped(args.directory)
    else: stop(args.directory)
