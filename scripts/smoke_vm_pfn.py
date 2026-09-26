"""One owned 4 GiB VM: capture successful KVM slots and join guest file PFNs."""
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
ENV = dict(os.environ, XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))
CLI = str(ROOT / 'runtime/bin/aenv')


def main():
    fixture_mib = int(sys.argv[1]) if len(sys.argv) > 1 else None
    assert fixture_mib in (None, 1, 16)
    os.umask(0o077)
    out = ROOT / '.artifacts/vm-pfn-smoke' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    (out / 'host/probes').mkdir(parents=True)
    sources = [Path(__file__), SENIOR / 'physical_cache.py',
               SENIOR / 'workloads/prettier-6604-rl-fork/physical-cache-probe.py']
    if fixture_mib:
        sources += [ROOT / 'scripts/vm_pfn_fixture.py', ROOT / 'scripts/guest_pfn_fixture.py']
    for source in sources:
        (out / source.name).write_bytes(source.read_bytes())
    cfg = json.loads((ROOT / 'configs/exp1-monitor-calibration-smoke.json').read_text())
    checks = {}
    sid = None
    tracer = None
    stage = 'preflight'
    status = 'FAIL'

    def run(name, args, timeout=30):
        try:
            p = subprocess.run(args, env=ENV, cwd=ROOT, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (out / f'{name}.stdout').write_bytes(exc.stdout or b'')
            (out / f'{name}.stderr').write_bytes((exc.stderr or b'') + b'\nTIMEOUT\n')
            raise
        (out / f'{name}.stdout').write_bytes(p.stdout)
        (out / f'{name}.stderr').write_bytes(p.stderr)
        (out / f'{name}.rc').write_text(str(p.returncode) + '\n')
        p.check_returncode()
        return p.stdout

    def aenv(name, *args, timeout=30):
        return run(name, [CLI, *args], timeout)

    try:
        assert os.geteuid() == 0 and os.sysconf('SC_PAGE_SIZE') == 4096
        credentials = tomllib.loads((ROOT / 'runtime/config/aenv/credentials').read_text())
        assert credentials['url'] == 'http://' + (SERVER / 'address.txt').read_text().strip()
        cid = (SERVER / 'container-id.txt').read_text().strip()
        info = json.loads(run('server', ['docker', 'inspect', cid]))[0]
        assert info['State']['Running'] and info['Config']['Labels']['agentenv.experiment'] == 'initial-smoke'
        assert json.loads(aenv('list-before', 'list', '--output', 'json')) == []
        cg = Path('/sys/fs/cgroup') / Path(f"/proc/{info['State']['Pid']}/cgroup").read_text().strip().split('::')[1].lstrip('/')
        assert not list(cg.glob('*/cgroup.procs')), 'nested service cgroups require explicit trace scope'
        (out / 'config.json').write_text(json.dumps(dict(image=cfg['workload_image'], cpu=2, memory_mib=4096,
            host_kernel=os.uname().release, fixture_mib=fixture_mib, cgroup=str(cg), cgroup_id=cg.stat().st_ino,
            server_image=info['Image'], source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}), indent=2))
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
        trace_path = out / 'scoped-kvm-slots.bt'
        trace_path.write_text(program)
        with (out / 'host/ioctl.tsv').open('wb') as stdout, (out / 'host/trace.stderr').open('wb') as stderr:
            tracer = subprocess.Popen(['bpftrace', '-q', '-B', 'line', str(trace_path)], stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 20
        while 'READY' not in (out / 'host/ioctl.tsv').read_text():
            assert tracer.poll() is None, 'trace attach failed; see host/trace.stderr'
            assert time.monotonic() < deadline, 'trace readiness deadline'
            time.sleep(.1)
        checks[stage] = 'PASS'
        stage = 'cold_start'
        sid = aenv('start', 'start', '--cold', cfg['workload_image'], '--detach', '--timeout', '300',
                   '--cpu', '2', '--memory', '4096', timeout=180).decode().strip()
        assert re.fullmatch(r'[a-f0-9-]{36}', sid)
        (out / 'sandbox-id.txt').write_text(sid + '\n')
        aenv('guest-environment', 'exec', sid, 'bash', '-lc', 'uname -a; getconf PAGESIZE; cat /proc/iomem; cat /proc/meminfo')
        checks[stage] = 'PASS'
        stage = 'guest_probe'
        aenv('upload', 'upload', sid, str(sources[2]), '/tmp/asb-physical-cache-probe.py')
        aenv('probe-start', 'exec', sid, 'bash', '-lc',
             f'nohup timeout 180 python3 /tmp/asb-physical-cache-probe.py {sid} >/tmp/asb-probe.log 2>&1 </dev/null & echo $! >/tmp/asb-probe.pid')
        aenv('probe-ready', 'exec', sid, 'bash', '-lc',
             f'for i in $(seq 1 40); do test -s /dev/shm/mixfs-exp5-{sid}/marker.json && exit 0; sleep 0.25; done; cat /tmp/asb-probe.log; exit 1')
        aenv('download', 'download', sid, f'/dev/shm/mixfs-exp5-{sid}', str(out / 'host/probes'))
        checks[stage] = 'PASS'
        stage = 'slot_capture'
        assert tracer.poll() is None, 'tracer stopped early'
        tracer.send_signal(signal.SIGINT)
        tracer.wait(timeout=10)
        assert tracer.returncode == 0, 'tracer exited unsuccessfully'
        rows = [line.split('\t') for line in (out / 'host/ioctl.tsv').read_text().splitlines() if '\t' in line]
        assert rows, 'no KVM region events captured; pre-existing pool or unsupported ioctl needs investigation'
        assert all(len(r) == 6 and int(r[5]) == 0 and int(r[3], 16) > 0 for r in rows), 'failed ioctl or slot removal requires explicit handling'
        (out / 'host/kvm-slots.tsv').write_text(''.join('\t'.join(r[:5]) + '\n' for r in rows))
        checks[stage] = 'PASS'
        stage = 'physical_join'
        begin = time.monotonic_ns()
        run('physical-join', ['python3', str(sources[1]), str(out), '1'], timeout=30)
        scan_ns = time.monotonic_ns() - begin
        measured = json.loads((out / 'host/physical-memory.json').read_text())
        assert measured['guest_file_cache_logical_pages'] > 0
        assert 0 < measured['guest_file_cache_resident_mappings'] <= measured['guest_file_cache_logical_pages']
        pid = measured['sandbox_to_vmm_pid'][sid]
        assert Path(f'/proc/{pid}/cgroup').read_text() == Path(f"/proc/{info['State']['Pid']}/cgroup").read_text()
        # Independent second live read, archived for offline recomputation.
        # This is not an atomic snapshot of the preceding collector scan.
        latest_slots = {int(r[1]): tuple(int(x, 16) for x in r[2:5]) for r in rows if int(r[0]) == pid}
        assert sorted((g, size) for g, size, _ in latest_slots.values()) == [(0, 0xc0000000), (0x100000000, 0x40000000)]
        def address(pfn):
            matches = [host + pfn * 4096 - guest for guest, size, host in latest_slots.values()
                       if guest <= pfn * 4096 < guest + size]
            assert len(matches) == 1, 'guest PFN missing or overlapping slots'
            return matches[0]
        probe = out / 'host/probes' / f'mixfs-exp5-{sid}'
        marker = json.loads((probe / 'marker.json').read_text())
        fd = os.open(f'/proc/{pid}/mem', os.O_RDONLY)
        try:
            marker_bytes = os.pread(fd, len(marker['marker_ascii']), address(marker['marker_pfn']))
        finally:
            os.close(fd)
        assert marker_bytes == marker['marker_ascii'].encode()
        (out / 'host/marker-read.bin').write_bytes(marker_bytes)
        pfns = array.array('I')
        pfns.frombytes((probe / 'file-pfns.bin').read_bytes())
        unique = set()
        resident = 0
        fd = os.open(f'/proc/{pid}/pagemap', os.O_RDONLY)
        try:
            with (out / 'host/page-map.tsv').open('w') as table:
                table.write('guest_pfn\thost_virtual_address\tpagemap_entry\n')
                for pfn in pfns:
                    host_addr = address(pfn)
                    raw = os.pread(fd, 8, host_addr // 4096 * 8)
                    assert len(raw) == 8
                    entry = struct.unpack('Q', raw)[0]
                    table.write(f'{pfn}\t{host_addr:x}\t{entry:x}\n')
                    if entry & (1 << 63):
                        host_pfn = entry & ((1 << 55) - 1)
                        assert host_pfn, 'host PFNs hidden'
                        unique.add(host_pfn)
                        resident += 1
        finally:
            os.close(fd)
        assert resident == measured['guest_file_cache_resident_mappings'], 'live residency changed between scans'
        assert len(unique) * 4096 / 1024**2 == measured['guest_file_cache_host_physical_mib'], 'live unique page count changed between scans'
        (out / 'coverage.json').write_text(json.dumps(dict(logical_pages=measured['guest_file_cache_logical_pages'],
            mapped_pages=measured['guest_file_cache_logical_pages'], unmapped_pages=0,
            resident_mappings=measured['guest_file_cache_resident_mappings'],
            nonresident_mappings=measured['guest_file_cache_logical_pages']-measured['guest_file_cache_resident_mappings'],
            scan_ns=scan_ns, independent_unique_host_pages=len(unique),
            note='two live non-atomic scans agree in counts; no shared/private/CoW or DAX validation'), indent=2))
        checks[stage] = 'PASS'
        if fixture_mib:
            stage = 'known_file_fixture'
            from vm_pfn_fixture import run_fixture
            run_fixture(run, aenv, sid, out, pid, latest_slots, fixture_mib, SENIOR)
            checks[stage] = 'PASS'
        status = 'PASS'
    except Exception:
        checks[stage] = 'FAIL'
        (out / 'failure.txt').write_text(traceback.format_exc())
        if sid and fixture_mib:
            try:
                aenv('fixture-failure-logs', 'exec', sid, 'bash', '-lc',
                     'cat /tmp/asb-fixture.log /workspace/asb-pfn-fixture/*/probe.log')
            except Exception:
                pass
    finally:
        if tracer is not None and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            try:
                tracer.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tracer.kill()
                tracer.wait(timeout=5)
        checks['trace_detached'] = 'PASS' if tracer is None or tracer.poll() is not None else 'FAIL'
        try:
            if sid:
                aenv('delete', 'delete', sid, timeout=90)
                rows = json.loads(aenv('list-after', 'list', '--output', 'json'))
                assert not any(r.get('sandboxID') == sid for r in rows)
            checks['cleanup'] = 'PASS'
        except Exception:
            checks['cleanup'] = 'FAIL'
            status = 'FAIL'
            (out / 'cleanup-failure.txt').write_text(traceback.format_exc())
        (out / 'result.json').write_text(json.dumps(dict(status=status, last_stage=stage, checks=checks), indent=2) + '\n')
        print(f'status={status} stage={stage} result_dir={out}', flush=True)
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
