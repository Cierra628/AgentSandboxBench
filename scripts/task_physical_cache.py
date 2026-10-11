"""Reuse the calibrated paused file-LRU probe at explicit task boundaries."""
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import signal
import subprocess
import time

from prettier6604_inputs import ROOT
from agentenv_whole_cache import bind_vm, ram_snapshot
from trenvx_whole_cache import dump


def source_fingerprint(files):
    return 'cd /testbed && git rev-parse HEAD && git diff --binary | sha256sum; sha256sum '+ ' '.join(shlex.quote('/testbed/'+p) for p in files)


class TaskPhysicalCache:
    def __init__(self, out, container, cli, *, source_files=('src/language-js/needs-parens.js',)):
        self.out, self.cli = out, cli
        self.source_files = source_files
        self.directory = out/'physical-cache'; self.directory.mkdir()
        self.tracer = None
        self.points = []
        info = json.loads(subprocess.check_output(['docker', 'inspect', container], timeout=15))[0]
        assert info['Config']['Labels']['agentenv.experiment'] == 'isolated-storage-check'
        assert info['Config']['Labels']['agentsandboxbench.run_id'] == out.parents[2].name
        self.cgroup = Path(f'/proc/{info["State"]["Pid"]}/cgroup').read_text().strip().split('::')[1]
        self.cgpath = Path('/sys/fs/cgroup')/self.cgroup.lstrip('/')
        program = '''struct region { unsigned int slot; unsigned int flags; unsigned long guest; unsigned long size; unsigned long host; };
BEGIN { printf("READY\\n"); }
tracepoint:syscalls:sys_enter_ioctl /cgroup == CGID && args->cmd == 0x4020ae46/ {
 $r=(struct region *)uptr(args->arg); @active[tid]=1; @slot[tid]=$r->slot; @guest[tid]=$r->guest; @size[tid]=$r->size; @host[tid]=$r->host;
}
tracepoint:syscalls:sys_exit_ioctl /@active[tid]/ {
 printf("%d\\t%lu\\t%lx\\t%lx\\t%lx\\t%ld\\n",pid,@slot[tid],@guest[tid],@size[tid],@host[tid],args->ret);
 delete(@active[tid]);delete(@slot[tid]);delete(@guest[tid]);delete(@size[tid]);delete(@host[tid]);
}
interval:s:240 { exit(); }
END { clear(@active);clear(@slot);clear(@guest);clear(@size);clear(@host); }
'''.replace('CGID', str(self.cgpath.stat().st_ino))
        (self.directory/'capture.bt').write_text(program)
        with (self.directory/'kvm-slots.tsv').open('x') as stdout, (self.directory/'capture.stderr').open('x') as stderr:
            self.tracer = subprocess.Popen(['bpftrace', '-q', '-B', 'line', str(self.directory/'capture.bt')], stdout=stdout, stderr=stderr,
                                           env=dict(os.environ, BPFTRACE_PERF_RB_PAGES='8'))
        try:
            deadline = time.monotonic()+20
            while 'READY' not in (self.directory/'kvm-slots.tsv').read_text():
                if self.tracer.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('owned KVM RAM-slot capture did not become ready')
                time.sleep(.1)
        except BaseException:
            self.close(); raise

    def close(self):
        if self.tracer and self.tracer.poll() is None:
            self.tracer.send_signal(signal.SIGINT)
            try: self.tracer.wait(timeout=10)
            except subprocess.TimeoutExpired: self.tracer.kill(); self.tracer.wait(timeout=5)

    def ready(self):
        self.close()
        assert self.tracer.returncode == 0
        import re
        assert not re.search(r'lost\s+[1-9]|ERROR', (self.directory/'capture.stderr').read_text(), re.I)

    def measure(self, sid, segment, global_step, *, retain_fixture_pages=False):
        context = self.prepare(sid, segment, global_step, retain_fixture_pages=retain_fixture_pages)
        pause = context['pause']
        try:
            pause.pause()
            self.capture(context)
            pause.snapshot_recheck(); pause.resume(); pause.save(True)
        finally:
            if pause.paused: pause.resume(); pause.save(False)
        return self.finish(context)

    def prepare(self, sid, segment, global_step, *, retain_fixture_pages=False):
        from cached_file_inventory import start
        from paused_agentenv_boundary import PausedBoundary
        out = self.directory/segment; out.mkdir(); cache = out/'whole-cache'; cache.mkdir()
        shutil.copy2(self.directory/'kvm-slots.tsv', cache/'kvm-slots.tsv')
        tools = out/'probe-tools'; tools.mkdir()
        for name in ('guest_whole_cache_probe.py', 'guest_cache_marker.py', 'guest_cached_file_inventory.py', 'guest_named_file_probe.py'):
            shutil.copy2(ROOT/'scripts'/name, tools/name)
        def aenv(label, *args, timeout=90):
            return self.cli('physical-'+segment+'-'+label, *args, timeout=timeout)
        guest = '/dev/shm/asb-cache-'+sid
        # Each boundary uses a fresh probe directory. Task files remain untouched.
        aenv('reset-probe', 'exec', sid, 'bash', '-lc', 'test ! -d '+guest+' || { test -f '+guest+'/done && rm -rf -- '+guest+'; }')
        aenv('upload-probe', 'upload', sid, str(tools), '/workspace/asb-cache-tools')
        fingerprint = source_fingerprint(self.source_files)
        before = aenv('state-before', 'exec', sid, 'bash', '-lc', fingerprint)
        started = time.monotonic_ns(); usage = resource.getrusage(resource.RUSAGE_SELF)
        aenv('probe-start', 'exec', sid, 'bash', '-lc', f'nohup timeout 600 env ASB_CACHE_WAIT_SECONDS=300 PYTHONDONTWRITEBYTECODE=1 python3 /workspace/asb-cache-tools/guest_cache_marker.py {guest} {sid} --heartbeat >/dev/shm/asb-cache.log 2>&1 </dev/null &')
        def wait(name):
            aenv('wait-'+name, 'exec', sid, 'bash', '-lc', f'for i in $(seq 1 850); do test -f {guest}/{name} && exit 0; test -f {guest}/failure.txt && break; sleep 0.1; done; cat {guest}/failure.txt /dev/shm/asb-cache.log; exit 1')
        wait('marker.json')
        for name in ('before.json', 'before-flags.bin', 'iomem.txt', 'marker.json'):
            aenv('download-'+name, 'download', sid, guest+'/'+name, str(cache/name))
        pids = [int(pid) for pid in self.cgpath.joinpath('cgroup.procs').read_text().split()
                if Path('/proc', pid, 'comm').read_text().strip() == 'firecracker']
        assert pids
        vm, proof = bind_vm(cache, pids, self.cgroup, os.readlink(f'/proc/{pids[0]}/exe'), sid)
        args = Path(f'/proc/{vm["pid"]}/cmdline').read_bytes().split(b'\0')
        workdir = str(Path(args[args.index(b'--api-sock')+1].decode()).parent)
        file_dir, file_guest = start(aenv, sid, out, 'fixture', True, retain_fixture_pages=retain_fixture_pages)
        pause = PausedBoundary(cache, vm, proof, workdir)
        return dict(sid=sid, segment=segment, global_step=global_step, out=out, cache=cache,
                    aenv=aenv, guest=guest, wait=wait, fingerprint=fingerprint, before=before,
                    started=started, usage=usage, vm=vm, proof=proof,
                    file_dir=file_dir, file_guest=file_guest, pause=pause)

    def capture(self, context):
        from cached_file_inventory import host_capture
        from kernel_file_cache import classify
        context['frozen_timestamp_ns'] = time.time_ns()
        host_capture(context['file_dir'], context['vm'], context['proof'])
        classify(context['file_dir'], context['vm'], context['proof'], context['pause'])
        ram_snapshot(context['cache'], context['vm'], context['proof'], 'before')

    def finish(self, context):
        from cached_file_inventory import finish
        from report_file_inventory import kernel_audit, audit_frozen_fixture
        sid, segment, global_step = (context[k] for k in ('sid','segment','global_step'))
        out, file_dir, file_guest, aenv, guest, wait = (context[k] for k in ('out','file_dir','file_guest','aenv','guest','wait'))
        pause, proof, before, fingerprint, started, usage = (context[k] for k in ('pause','proof','before','fingerprint','started','usage'))
        finish(aenv, sid, file_dir, file_guest, 'frozen', require_complete_cache=False)
        aenv('probe-after-request', 'exec', sid, 'touch', guest+'/capture-after'); wait('after-ready')
        aenv('probe-release', 'exec', sid, 'touch', guest+'/release'); wait('done')
        after = aenv('state-after', 'exec', sid, 'bash', '-lc', fingerprint)
        assert before == after, 'physical analysis changed task source or patch'
        kernel = kernel_audit(file_dir, proof); fixture = audit_frozen_fixture(file_dir, require_complete_cache=False)
        assert kernel['classification_status'] == 'PASS' and kernel['frozen_cache_physical_status'] in ('PASS','PARTIAL')
        assert fixture['status'] == 'PASS'
        aenv('release-fixture', 'exec', sid, 'bash', '-lc', 'test -f '+file_guest+'/done && rm -rf -- '+file_guest+' /workspace/asb-inventory-fixture')
        last = resource.getrusage(resource.RUSAGE_SELF)
        point = dict(status=kernel['frozen_cache_physical_status'], sandbox_id=sid, after_segment=segment, after_global_step=global_step,
                     frozen_timestamp_ns=context['frozen_timestamp_ns'], analysis_elapsed_ns=time.monotonic_ns()-started,
                     paused_interval_ns=pause.receipt['paused_interval_ns'], kernel_cache=kernel,
                     confirmed_physical_bytes=kernel['frozen_guest_file_cache_physical_bytes'],
                     unknown_host_pages=kernel['host_unknown_pages'],
                     possible_physical_upper_bytes=kernel['frozen_guest_file_cache_physical_bytes']+kernel['host_unknown_pages']*4096,
                     known_fixture=fixture, calibration_regular_content_included_bytes=3*4096,
                     task_state_unchanged=True, collector_cpu_user_seconds=last.ru_utime-usage.ru_utime,
                     collector_cpu_system_seconds=last.ru_stime-usage.ru_stime,
                     scope='paused guest SystemRAM file-LRU; regular content and metadata typed separately; host PFNs deduplicated within this sandbox; not complete host backing cache or cross-VM sharing')
        self.record_point(out, point)
        return point

    def record_point(self, out, point):
        dump(out/'point.json', point)
        self.points.append(point)
        # Only this run's growing aggregate is updated; completed points stay immutable.
        (self.directory/'summary.json').write_text(json.dumps(dict(status='PARTIAL' if any(p.get('status')=='PARTIAL' for p in self.points) else 'PASS', unit='bytes; timing ns', points=self.points,
            scope=point['scope'], cache_control='no host or guest cache drop; calibrated three regular pages included'),indent=2)+'\n')
