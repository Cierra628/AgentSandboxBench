"""One AgentENV VM: pair a declared guest file cache with host residency, no pause.

Uses the existing cached image, CLI, KVM-slot attestation and pagemap reader.
The 1 MiB locked fixture is a calibration, not a whole-guest page-cache collector.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib

from agentenv_whole_cache import bind_vm
from experiment_disk_guard import writer_lock
from live_cache_metadata import PAGE, SIZE, decode, summarize
from measure_file_physical import identity, ram_address, read_entry
from probe_storage_boundary import identities
from trenvx_whole_cache import same_physical_mapping

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / '.artifacts/server-smoke-20260922T114006Z-1387374'
CLI = ROOT / 'runtime/bin/aenv'
ENV = dict(os.environ, XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / '.artifacts'):
        parser.error('output must be a new project .artifacts directory')
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    result = dict(status='FAIL', scope='one declared 1 MiB file in one running AgentENV guest',
                  vm_pause=False, whole_guest_physical_cache=False, observations=[],
                  guest_metadata='resident file-page PFNs sampled into a separate locked page; not kernel events')
    sid = tracer = None
    stage = 'preflight'
    before = identities()
    (out / 'firecracker-before.json').write_text(json.dumps(before) + '\n')
    minimum_free = shutil.disk_usage(out).free
    sources = ['probe_agentenv_live_cache.py','guest_live_cache_probe.py','live_cache_metadata.py',
               'guest_named_file_probe.py','agentenv_whole_cache.py','measure_file_physical.py','trenvx_whole_cache.py']
    (out / 'code').mkdir()
    for name in sources:
        (out / 'code' / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
    result['source_sha256'] = {name: hashlib.sha256((out/'code'/name).read_bytes()).hexdigest() for name in sources}

    def call(label, argv, timeout=30):
        nonlocal minimum_free
        minimum_free = min(minimum_free, shutil.disk_usage(out).free)
        if minimum_free < 32 * 2**30:
            raise RuntimeError('less than 32 GiB free during short owned-VM probe')
        p = subprocess.run(list(map(str, argv)), env=ENV, cwd=ROOT, capture_output=True, timeout=timeout)
        (out / (label + '.stdout')).write_bytes(p.stdout)
        (out / (label + '.stderr')).write_bytes(p.stderr)
        (out / (label + '.rc')).write_text(str(p.returncode) + '\n')
        p.check_returncode()
        return p.stdout.decode().strip()

    def aenv(label, *argv, timeout=30):
        return call(label, [CLI, *argv], timeout)

    try:
        assert os.geteuid() == 0 and os.sysconf('SC_PAGE_SIZE') == PAGE
        assert minimum_free > 32 * 2**30
        credentials = tomllib.loads((ROOT / 'runtime/config/aenv/credentials').read_text())
        assert credentials['url'] == 'http://' + (SERVER / 'address.txt').read_text().strip()
        assert json.loads(aenv('list-before', 'list', '--output', 'json')) == [], 'project service is in use'
        cid = (SERVER / 'container-id.txt').read_text().strip()
        service_pid, service_image, running = call('service', ['docker', 'inspect', '--format',
            '{{.State.Pid}} {{.Image}} {{.State.Running}}', cid]).split()
        assert running == 'true'
        group = Path('/proc', service_pid, 'cgroup').read_text().strip().split('::')[1]
        cg = Path('/sys/fs/cgroup') / group.lstrip('/')
        cfg = json.loads((ROOT / 'configs/exp1-monitor-calibration-smoke.json').read_text())
        image = cfg['workload_image']
        digest = image.rsplit('@sha256:', 1)[1]
        service_root = Path('/proc', service_pid, 'root')
        cache = service_root / f'workspace/env/image-cache/configs/sha256-{digest}-image.json'
        lowers = json.loads(cache.read_text())['lowers']
        assert lowers and all((service_root / row['file'].lstrip('/')).is_file() for row in lowers), 'cached layers missing; no download in this probe'
        result['config'] = dict(image=image, cpu=2, guest_memory_mib=4096, host_kernel=os.uname().release,
                                service_image=service_image, fixture_bytes=SIZE, fixture_mlock=True,
                                metadata_locked_bytes=PAGE, host_interval_seconds=.1)
        program = '''struct region { unsigned int slot; unsigned int flags; unsigned long guest; unsigned long size; unsigned long host; };
BEGIN { printf("READY\\n"); }
tracepoint:syscalls:sys_enter_ioctl /cgroup == CGID && args->cmd == 0x4020ae46/ {
  $r = (struct region *)uptr(args->arg);
  @active[tid]=1; @slot[tid]=$r->slot; @guest[tid]=$r->guest; @size[tid]=$r->size; @host[tid]=$r->host;
}
tracepoint:syscalls:sys_exit_ioctl /@active[tid]/ {
  printf("%d\\t%lu\\t%lx\\t%lx\\t%lx\\t%ld\\n", pid,@slot[tid],@guest[tid],@size[tid],@host[tid],args->ret);
  delete(@active[tid]); delete(@slot[tid]); delete(@guest[tid]); delete(@size[tid]); delete(@host[tid]);
}
interval:s:180 { exit(); }
END { clear(@active); clear(@slot); clear(@guest); clear(@size); clear(@host); }
'''.replace('CGID', str(cg.stat().st_ino))
        (out / 'kvm-slots.bt').write_text(program)
        stage = 'attach_kvm'
        with (out / 'kvm-slots.tsv').open('w') as stdout, (out / 'trace.stderr').open('w') as stderr:
            tracer = subprocess.Popen(['bpftrace','-q','-B','line',str(out/'kvm-slots.bt')], stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 20
        while 'READY' not in (out / 'kvm-slots.tsv').read_text():
            assert tracer.poll() is None and time.monotonic() < deadline, 'KVM capture readiness failure'
            time.sleep(.1)
        stage = 'cold_start'
        sid = aenv('start','start','--cold',image,'--detach','--timeout','120','--cpu','2','--memory','4096',timeout=90)
        assert re.fullmatch('[a-f0-9-]{36}',sid), 'unexpected sandbox id'
        (out / 'sandbox-id.txt').write_text(sid + '\n')
        aenv('ready','exec',sid,'true',timeout=15)
        tools = out / 'guest-tools'
        tools.mkdir()
        for name in ['guest_live_cache_probe.py','live_cache_metadata.py','guest_named_file_probe.py']:
            (tools/name).write_bytes((ROOT/'scripts'/name).read_bytes())
        aenv('upload','upload',sid,tools,'/workspace/asb-live-tools')
        guest = '/dev/shm/asb-live-' + sid
        stage = 'guest_metadata'
        aenv('guest-start','exec',sid,'bash','-lc',
             f'nohup timeout 50 python3 /workspace/asb-live-tools/guest_live_cache_probe.py {sid} {guest} >/tmp/asb-live.log 2>&1 </dev/null &')
        aenv('bootstrap-ready','exec',sid,'bash','-lc',
             f'for i in $(seq 1 100); do test -f {guest}/bootstrap.json && exit 0; test ! -f {guest}/failure.txt || break; sleep 0.1; done; cat /tmp/asb-live.log; exit 1',timeout=15)
        bootstrap = json.loads(aenv('bootstrap','exec',sid,'cat',guest+'/bootstrap.json'))
        (out/'bootstrap.json').write_text(json.dumps(bootstrap,indent=2)+'\n')
        (out/'marker.json').write_text(json.dumps(bootstrap)+'\n')
        (out/'iomem.txt').write_text(bootstrap['iomem'])
        tracer.send_signal(signal.SIGINT)
        tracer.wait(timeout=10)
        assert tracer.returncode == 0
        assert not re.search(r'\b(lost|dropped)\b',(out/'trace.stderr').read_text(),re.I)
        pids = sorted({int(fields[0]) for line in (out/'kvm-slots.tsv').read_text().splitlines()
                       if len(fields:=line.split('\t'))==6 and fields[0].isdigit()})
        assert len(pids)==1, 'expected one captured owned cold VMM'
        exe = os.readlink(Path('/proc')/str(pids[0])/'exe')
        vm, proof = bind_vm(out,pids,group,exe,sid)
        result['guest_kernel'] = bootstrap['kernel']
        meta_address = ram_address(bootstrap['pfn'],proof['slots'])
        proc = identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        stage = 'continuous_measurement'
        aenv('begin','exec',sid,'touch',guest+'/start')
        samples = []
        skipped = 0
        deadline = time.monotonic() + 16
        with (proc/'pagemap').open('rb',buffering=0) as pm, (proc/'mem').open('rb',buffering=0) as memory, (out/'samples.jsonl').open('x') as raw:
            while time.monotonic() < deadline:
                assert shutil.disk_usage(out).free > 32 * 2**30
                started = time.monotonic_ns()
                meta_first = read_entry(pm.fileno(),meta_address)
                assert meta_first & (1<<63), 'metadata page is no longer host-resident'
                frame = os.pread(memory.fileno(),PAGE,meta_address)
                try:
                    snapshot = decode(frame)
                except ValueError:
                    skipped += 1
                    time.sleep(.01)
                    continue
                rows = []
                for pfn in sorted(set(snapshot['file_pfns'])-{0}):
                    address = ram_address(pfn,proof['slots'])
                    rows.append(dict(guest_pfn=pfn,first=read_entry(pm.fileno(),address)))
                for row in rows:
                    row['last']=read_entry(pm.fileno(),ram_address(row['guest_pfn'],proof['slots']))
                last_frame = os.pread(memory.fileno(),PAGE,meta_address)
                meta_last = read_entry(pm.fileno(),meta_address)
                if frame != last_frame or not same_physical_mapping(meta_first,meta_last):
                    skipped += 1
                    continue
                accounted = summarize(snapshot,rows)
                observation = {k:v for k,v in snapshot.items() if k!='file_pfns'}
                observation.update(accounted,host_timestamp_ns=time.time_ns(),host_scan_ns=time.monotonic_ns()-started)
                raw.write(json.dumps(dict(metadata_hex=frame.hex(),host_rows=rows,observation=observation))+'\n')
                raw.flush()
                samples.append(observation)
                if snapshot['phase']=='complete':
                    break
                time.sleep(.1)
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        expected = dict(empty=0,half=SIZE//2,full=SIZE,reread=SIZE,released=0)
        for phase, size in expected.items():
            rows = [r for r in samples if r['phase']==phase]
            assert len(rows)>=2, 'missing repeated samples for '+phase
            assert all(r['declared_file_guest_cache_bytes']==r['host_confirmed_cache_bytes']==size for r in rows), 'cache accounting differs in '+phase
        assert all(r['host_mapping_changed_pages']==r['host_nonresident_pages']==r['host_swapped_pages']==0 for r in samples)
        assert samples[-1]['phase']=='complete' and samples[-1]['heartbeat']>samples[0]['heartbeat']+20, 'guest progress not confirmed'
        aenv('guest-result-ready','exec',sid,'bash','-lc',f'for i in $(seq 1 30); do test -f {guest}/result.json && exit 0; sleep 0.1; done; exit 1',timeout=5)
        guest_result=json.loads(aenv('guest-result','exec',sid,'cat',guest+'/result.json'))
        assert guest_result['status']=='PASS'
        aenv('release','exec',sid,'touch',guest+'/release')
        aenv('guest-done','exec',sid,'bash','-lc',f'for i in $(seq 1 30); do test -f {guest}/done && exit 0; sleep 0.1; done; cat /tmp/asb-live.log; exit 1',timeout=5)
        result.update(status='PASS',observations=samples,sample_count=len(samples),skipped_publications=skipped,
                      guest_result=guest_result,limitations=['Declared 1 MiB file only; whole-guest Cached is separately recorded.',
                      'Fixture populated ranges and 4 KiB metadata are mlocked; this is not normal-task overhead validation.',
                      'Metadata is sampled, not allocation-event complete; intervening reuse is not excluded by repeated reads.',
                      'No cross-VM sharing or full trajectory validation.'])
    except Exception as exc:
        import traceback
        result.update(failed_stage=stage,error=str(exc))
        (out/'failure.txt').write_text(traceback.format_exc())
        if sid:
            try:aenv('guest-failure','exec',sid,'cat','/tmp/asb-live.log',timeout=5)
            except Exception:pass
    finally:
        if tracer is not None and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            try:tracer.wait(timeout=10)
            except subprocess.TimeoutExpired:tracer.kill();tracer.wait(timeout=5)
        cleanup = dict(tracer_stopped=tracer is None or tracer.poll() is not None)
        if sid:
            try:
                # Cleanup must remain possible even if the launch budget was exceeded.
                p=subprocess.run([str(CLI),'delete',sid],env=ENV,capture_output=True,timeout=60)
                (out/'delete.stdout').write_bytes(p.stdout);(out/'delete.stderr').write_bytes(p.stderr)
                p.check_returncode()
                rows=json.loads(subprocess.check_output([str(CLI),'list','--output','json'],env=ENV,timeout=10))
                cleanup['owned_sandbox_removed']=not any(r.get('sandboxID')==sid for r in rows)
            except Exception as exc:cleanup.update(owned_sandbox_removed=False,error=str(exc))
        after=identities()
        cleanup['preexisting_vmm_identities_unchanged']=all(row in after for row in before)
        if 'vm' in locals():cleanup['owned_vmm_gone']=not any(row['pid']==vm['pid'] and row['start_ticks']==str(vm['start_time_ticks']) for row in after)
        if not all(v for k,v in cleanup.items() if isinstance(v,bool)):result['status']='FAIL'
        result.update(cleanup=cleanup,observed_min_free_bytes=min(minimum_free,shutil.disk_usage(out).free),
                      final_free_bytes=shutil.disk_usage(out).free)
        (out/'firecracker-after.json').write_text(json.dumps(after)+'\n')
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],failed_stage=result.get('failed_stage'),result_dir=str(out))))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    with writer_lock():
        raise SystemExit(main())
