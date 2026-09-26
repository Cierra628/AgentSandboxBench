"""One snapshot, two restored VMs, and archived known-file PFN comparisons."""
import array
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import time
import tomllib
import traceback

ROOT = Path(__file__).resolve().parent.parent
SENIOR = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
SERVER = ROOT / '.artifacts/server-smoke-20260922T114006Z-1387374'
CLI = str(ROOT / 'runtime/bin/aenv')
ENV = dict(os.environ, XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))


def main():
    mib = int(sys.argv[1])
    assert mib in (1, 16)
    os.umask(0o077)
    out = ROOT / '.artifacts/snapshot-sibling-pfn' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    (out / 'host').mkdir(parents=True)
    sources = [Path(__file__), ROOT / 'scripts/cross_vm_guest_worker.py', SENIOR / 'physical_cache.py',
               SENIOR / 'workloads/prettier-6604-rl-fork/physical-cache-probe.py']
    for source in sources:
        (out / source.name).write_bytes(source.read_bytes())
    cfg = json.loads((ROOT / 'configs/exp1-monitor-calibration-smoke.json').read_text())
    alias = 'asb-pfn-' + out.name
    owned = []
    snapshot_attempted = False
    tracer = None
    checks = {}
    stage = 'preflight'
    status = 'FAIL'

    def run(label, args, timeout=30):
        try:
            p = subprocess.run(args, env=ENV, cwd=ROOT, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (out / f'{label}.stdout').write_bytes(exc.stdout or b'')
            (out / f'{label}.stderr').write_bytes((exc.stderr or b'') + b'\nTIMEOUT\n')
            raise
        (out / f'{label}.stdout').write_bytes(p.stdout)
        (out / f'{label}.stderr').write_bytes(p.stderr)
        (out / f'{label}.rc').write_text(str(p.returncode) + '\n')
        p.check_returncode()
        return p.stdout

    def aenv(label, *args, timeout=30):
        return run(label, [CLI, *args], timeout)

    def owned_start(label, target, cold=False):
        args = ['start']
        if cold:
            args += ['--cold']
        args += [target, '--detach', '--timeout', '300']
        if cold:
            args += ['--cpu', '2', '--memory', '4096']
        sid = aenv(label, *args, timeout=180).decode().strip()
        assert re.fullmatch(r'[0-9a-f-]{36}', sid)
        owned.append(sid)
        return sid

    def guest_state(label, sid, seq=None, operation='inspect'):
        if seq is not None:
            payload = json.dumps(dict(seq=seq, operation=operation), separators=(',', ':'))
            aenv(label + '-command', 'exec', sid, 'bash', '-lc',
                 f"printf '%s' '{payload}' > /workspace/asb-cross-vm/command.json")
        expected = seq if seq is not None else 0
        stdout = aenv(label + '-wait', 'exec', sid, 'bash', '-lc',
            f'for i in $(seq 1 80); do grep -q \'"seq": {expected}\' /workspace/asb-cross-vm/state.json && '
            'cat /workspace/asb-cross-vm/state.json && exit 0; sleep 0.1; done; '
            'cat /workspace/asb-cross-vm/worker.log; exit 1', timeout=25)
        state = json.loads(stdout)
        assert state['seq'] == expected and state['page_count'] == mib*256
        assert state['operation'] == operation
        return state

    def probe(label, sid):
        guest_dir = f'/dev/shm/mixfs-exp5-{sid}'
        aenv(label + '-probe-clear', 'exec', sid, 'bash', '-lc',
             f'rm -f {guest_dir}/marker.json {guest_dir}/file-pfns.bin')
        aenv(label + '-probe-start', 'exec', sid, 'bash', '-lc',
             f'nohup python3 /workspace/asb-tools/physical-cache-probe.py {sid} '
             f'>/workspace/asb-cross-vm/probe.log 2>&1 </dev/null & echo $! >/workspace/asb-cross-vm/probe.pid')
        aenv(label + '-probe-ready', 'exec', sid, 'bash', '-lc',
             f'for i in $(seq 1 80); do test -s {guest_dir}/marker.json && exit 0; sleep 0.1; done; '
             'cat /workspace/asb-cross-vm/probe.log; exit 1', timeout=20)

    def stop_probes(siblings):
        for name, sid in siblings.items():
            try:
                aenv('stop-probe-' + name, 'exec', sid, 'bash', '-lc',
                     'test -f /workspace/asb-cross-vm/probe.pid && '
                     'kill "$(cat /workspace/asb-cross-vm/probe.pid)" || true')
            except Exception:
                pass

    def sample(label, siblings, states, regions):
        directory = out / label
        probes = directory / 'probes'
        probes.mkdir(parents=True)
        try:
            for name, sid in siblings.items():
                probe(label + '-' + name, sid)
            for name, sid in siblings.items():
                aenv(label + '-' + name + '-download', 'download', sid,
                     f'/dev/shm/mixfs-exp5-{sid}', str(probes))
                (directory / f'{name}-state.json').write_text(json.dumps(states[name], indent=2) + '\n')
            run(label + '-collector', ['python3', str(SENIOR / 'physical_cache.py'), str(out),
                '2', str(probes), str(directory / 'physical-memory.json')], timeout=45)
            measured = json.loads((directory / 'physical-memory.json').read_text())
            mapping = measured['sandbox_to_vmm_pid']
            assert set(mapping) == set(siblings.values())
            host_pfns = {}
            file_pages = {}
            with (directory / 'known-pages.tsv').open('w') as table:
                table.write('branch\tpage_index\tguest_pfn\tguest_flags\thost_pid\thost_address\thost_pagemap_entry\n')
                for name, sid in siblings.items():
                    state = states[name]
                    pid = mapping[sid]
                    assert Path(f'/proc/{pid}/cgroup').read_text() == service_cgroup_text
                    slots = regions[pid]
                    assert sorted((g, size) for g,size,_ in slots.values()) == [(0,0xc0000000),(0x100000000,0x40000000)]
                    pfns = array.array('I')
                    pfns.frombytes((probes / f'mixfs-exp5-{sid}' / 'file-pfns.bin').read_bytes())
                    enumerated = set(pfns)
                    branch_host = []
                    branch_file = []
                    fd = os.open(f'/proc/{pid}/pagemap', os.O_RDONLY)
                    try:
                        for index, page in enumerate(state['pages']):
                            guest_pfn = page['pfn']
                            flags = page['flags']
                            is_anon = label == 'after-write' and name == 'A' and index % 2 == 0
                            if is_anon:
                                assert flags & (1 << 12) and guest_pfn not in enumerated
                            else:
                                assert not flags & ((1 << 12) | (1 << 14)) and guest_pfn in enumerated
                            matches = [h + guest_pfn*4096-g for g,size,h in slots.values()
                                       if g <= guest_pfn*4096 < g+size]
                            assert len(matches) == 1
                            entry_raw = os.pread(fd, 8, matches[0]//4096*8)
                            assert len(entry_raw) == 8
                            entry = struct.unpack('Q', entry_raw)[0]
                            host_pfn = entry & ((1 << 55)-1)
                            assert entry & (1 << 63) and host_pfn
                            table.write(f'{name}\t{index}\t{guest_pfn}\t{flags:x}\t{pid}\t{matches[0]:x}\t{entry:x}\n')
                            branch_host.append(host_pfn)
                            branch_file.append(not is_anon)
                    finally:
                        os.close(fd)
                    assert len(set(branch_host)) == mib*256
                    host_pfns[name] = branch_host
                    file_pages[name] = branch_file
            same_index = sum(a == b for a,b in zip(host_pfns['A'],host_pfns['B']))
            shared = len(set(host_pfns['A']) & set(host_pfns['B']))
            summary = dict(label=label, per_branch_pages=mib*256, same_index_host_pfns=same_index,
                           intersecting_host_pfns=shared, total_unique_host_pfns=len(set(host_pfns['A']) | set(host_pfns['B'])),
                           file_pages={name:sum(values) for name,values in file_pages.items()},
                           anonymous_pages={name:len(values)-sum(values) for name,values in file_pages.items()},
                           collector=measured)
            (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            return summary
        finally:
            stop_probes(siblings)

    service_cgroup_text = None
    try:
        assert os.geteuid() == 0 and os.sysconf('SC_PAGE_SIZE') == 4096
        cid = (SERVER / 'container-id.txt').read_text().strip()
        info = json.loads(run('server', ['docker', 'inspect', cid]))[0]
        assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment') == 'initial-smoke'
        credentials = tomllib.loads((ROOT / 'runtime/config/aenv/credentials').read_text())
        assert credentials['url'] == 'http://' + (SERVER / 'address.txt').read_text().strip()
        assert json.loads(aenv('list-before', 'list', '--output', 'json')) == []
        assert json.loads(aenv('templates-before', 'template', 'list', '--output', 'json')) == []
        service_pid = info['State']['Pid']
        service_cgroup_text = Path(f'/proc/{service_pid}/cgroup').read_text()
        cg = Path('/sys/fs/cgroup') / service_cgroup_text.strip().split('::')[1].lstrip('/')
        assert not list(cg.glob('*/cgroup.procs'))
        (out / 'config.json').write_text(json.dumps(dict(image=cfg['workload_image'], mib=mib,
            cpu=2, memory_mib=4096, snapshot_alias=alias, host_kernel=os.uname().release,
            server_image=info['Image'], service_cgroup=str(cg), service_cgroup_id=cg.stat().st_ino,
            sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}), indent=2) + '\n')
        checks[stage] = 'PASS'
        stage = 'trace_attach'
        program = '''struct region { unsigned int slot; unsigned int flags; unsigned long guest; unsigned long size; unsigned long host; };
BEGIN { printf("READY\\n"); }
tracepoint:syscalls:sys_enter_ioctl /cgroup == CGID && args->cmd == 0x4020ae46/ {
  $r = (struct region *)uptr(args->arg);
  @active[tid] = 1; @slot[tid] = $r->slot; @guest[tid] = $r->guest;
  @size[tid] = $r->size; @host[tid] = $r->host;
}
tracepoint:syscalls:sys_exit_ioctl /@active[tid]/ {
  printf("%d\\t%lu\\t%lx\\t%lx\\t%lx\\t%ld\\n", pid, @slot[tid], @guest[tid], @size[tid], @host[tid], args->ret);
  delete(@active[tid]); delete(@slot[tid]); delete(@guest[tid]); delete(@size[tid]); delete(@host[tid]);
}
interval:s:240 { exit(); }
END { clear(@active); clear(@slot); clear(@guest); clear(@size); clear(@host); }
'''.replace('CGID', str(cg.stat().st_ino))
        (out / 'scoped-kvm-slots.bt').write_text(program)
        with (out / 'host/ioctl.tsv').open('wb') as stdout, (out / 'host/trace.stderr').open('wb') as stderr:
            tracer = subprocess.Popen(['bpftrace', '-q', '-B', 'line', str(out / 'scoped-kvm-slots.bt')], stdout=stdout, stderr=stderr)
        deadline = time.monotonic()+20
        while 'READY' not in (out / 'host/ioctl.tsv').read_text():
            assert tracer.poll() is None and time.monotonic()<deadline, 'trace attach failed'
            time.sleep(.1)
        checks[stage] = 'PASS'
        stage = 'parent_prepare'
        parent_sid = owned_start('start-parent', cfg['workload_image'], cold=True)
        tools_dir = out / 'guest-tools'
        tools_dir.mkdir()
        (tools_dir / 'cross_vm_guest_worker.py').write_bytes(sources[1].read_bytes())
        (tools_dir / 'physical-cache-probe.py').write_bytes(sources[3].read_bytes())
        aenv('upload-tools', 'upload', parent_sid, str(tools_dir), '/workspace/asb-tools')
        aenv('start-worker', 'exec', parent_sid, 'bash', '-lc',
             f'nohup timeout 300 python3 /workspace/asb-tools/cross_vm_guest_worker.py {mib} '
             '>/workspace/asb-worker.log 2>&1 </dev/null &')
        before = guest_state('parent-initial', parent_sid)
        before = guest_state('parent-inspect', parent_sid, 1)
        (out / 'parent-state.json').write_text(json.dumps(before, indent=2) + '\n')
        checks[stage] = 'PASS'
        stage = 'snapshot'
        snapshot_attempted = True
        aenv('create-snapshot', 'snapshot', 'create', parent_sid, '--name', alias, timeout=150)
        checks[stage] = 'PASS'
        stage = 'delete_parent'
        aenv('delete-parent', 'delete', parent_sid, timeout=90)
        owned.remove(parent_sid)
        checks[stage] = 'PASS'
        stage = 'restore_siblings'
        siblings = {name:owned_start('restore-'+name, alias) for name in ('A','B')}
        initial = {name:guest_state(name+'-initial', sid, 1) for name,sid in siblings.items()}
        for state in initial.values():
            assert all(state[k] == before[k] for k in ('pid','start_ticks','boot_id','mapped_sha256'))
        (out / 'restore-continuity.json').write_text(json.dumps(initial, indent=2) + '\n')
        checks[stage] = 'PASS'
        stage = 'slot_capture'
        assert tracer.poll() is None
        tracer.send_signal(signal.SIGINT)
        tracer.wait(timeout=10)
        assert tracer.returncode == 0
        rows = [line.split('\t') for line in (out / 'host/ioctl.tsv').read_text().splitlines() if '\t' in line]
        assert rows and all(len(r)==6 and int(r[5])==0 and int(r[3],16)>0 for r in rows)
        (out / 'host/kvm-slots.tsv').write_text(''.join('\t'.join(r[:5])+'\n' for r in rows))
        regions = {}
        for row in rows:
            regions.setdefault(int(row[0]), {})[int(row[1])] = tuple(int(value,16) for value in row[2:5])
        checks[stage] = 'PASS'
        stage = 'before_write'
        states_before = {name:guest_state(name+'-before', sid, 2) for name,sid in siblings.items()}
        pre = sample('before-write', siblings, states_before, regions)
        checks[stage] = 'PASS'
        stage = 'after_write'
        states_after = {name:guest_state(name+'-after', sid, 3, 'write-half' if name=='A' else 'inspect')
                        for name,sid in siblings.items()}
        for name in siblings:
            for key in ('pid','start_ticks','boot_id'):
                assert states_after[name][key] == initial[name][key]
        assert states_after['A']['mapped_sha256'] == before['altered_sha256']
        assert states_after['B']['mapped_sha256'] == before['backing_sha256']
        post = sample('after-write', siblings, states_after, regions)
        assert post['file_pages'] == {'A':mib*128,'B':mib*256}
        assert post['anonymous_pages'] == {'A':mib*128,'B':0}
        checks[stage] = 'PASS'
        (out / 'comparison.json').write_text(json.dumps(dict(before=pre, after=post), indent=2) + '\n')
        status = 'PASS'
    except Exception:
        checks[stage] = 'FAIL'
        (out / 'failure.txt').write_text(traceback.format_exc())
    finally:
        if tracer is not None and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            try:
                tracer.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tracer.kill()
                tracer.wait(timeout=5)
        checks['trace_detached'] = 'PASS' if tracer is None or tracer.poll() is not None else 'FAIL'
        cleanup = True
        for sid in reversed(owned):
            try:
                aenv('cleanup-'+sid, 'delete', sid, timeout=90)
            except Exception:
                cleanup = False
                (out / f'cleanup-{sid}.failure.txt').write_text(traceback.format_exc())
        if snapshot_attempted:
            try:
                aenv('cleanup-snapshot', 'template', 'delete', alias, timeout=90)
            except Exception:
                cleanup = False
                (out / 'cleanup-snapshot.failure.txt').write_text(traceback.format_exc())
        try:
            listed = json.loads(aenv('list-after', 'list', '--output', 'json'))
            templates = json.loads(aenv('templates-after', 'template', 'list', '--output', 'json'))
            assert not any(row.get('sandboxID') in owned for row in listed)
            assert not any(row.get('name')==alias for row in templates)
        except Exception:
            cleanup = False
            (out / 'cleanup-list.failure.txt').write_text(traceback.format_exc())
        checks['cleanup'] = 'PASS' if cleanup else 'FAIL'
        if not cleanup:
            status = 'FAIL'
        (out / 'result.json').write_text(json.dumps(dict(status=status, last_stage=stage, checks=checks), indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}', flush=True)
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
