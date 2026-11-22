"""Observe file-offset residency of one restored VMM's immutable memory lower."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from host_file_monitor import mincore_file, PAGE
from host_sandbox_monitor import Binding, UnixHTTP
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE, mapped_device
from snapshot_lower_owner import select_config, service_openers

clock = time.monotonic_ns
ROOT = Path(__file__).resolve().parents[1]


def api(vm, method, endpoint, state=None):
    conn = UnixHTTP(vm['api_socket'])
    conn.timeout = 5
    try:
        conn.request(method, endpoint, json.dumps({'state': state}) if state else None,
                     {'Content-Type': 'application/json'})
        response = conn.getresponse()
        data = response.read()
        if response.status != (204 if state else 200):
            raise RuntimeError(f'{method} {endpoint}: HTTP {response.status}')
        return json.loads(data)['state'] if data else None
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('container','service-url','config-home','output'):
        parser.add_argument('--'+key, required=True)
    for flag in ('provider-cache','restored-lower'):
        parser.add_argument('--'+flag, action='store_true', required=True)
    a = parser.parse_args()
    out = Path(a.output).resolve()
    assert out.parent.name == 'controller' and out.parent.parent.name == 'mount'
    out.mkdir()
    env = clean_environment(dict(os.environ, XDG_CONFIG_HOME=a.config_home))
    rows = []
    result = dict(status='FAIL', scope='paused restored VMM; immutable snapshot memory lower, file offsets only',
                  page_bytes=PAGE, samples=rows, events={}, cleanup={})
    events = result['events']
    def cli(*args):
        proc = subprocess.run([ROOT/'runtime/bin/aenv', *args], capture_output=True, env=env, timeout=150)
        if proc.returncode:
            (out/'cli-error.log').write_bytes(proc.stderr)
            proc.check_returncode()
        return proc.stdout.decode().strip()
    source = restored = snapshot = vm = None
    paused = False
    try:
        binding = Binding(a.container, out)
        source = create_source(Path(a.config_home), a.service_url)
        cli('exec',source,'true')
        cli('exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
        name = 'paused-layer-'+out.parents[2].name
        cli('snapshot','create',source,'--name',name)
        snapshot = name
        cli('delete',source)
        source = None
        events['restore_start'] = clock()
        restored = cli('start',snapshot,'--detach','--timeout','1800')
        events['restore_return'] = clock()
        vm = bind_vmm(binding,restored,out,'restored')
        assert api(vm,'GET','/') == 'Running'
        events['pause_request'] = clock()
        api(vm,'PATCH','/vm','Paused')
        events['pause_response'] = clock()
        paused = True
        assert api(vm,'GET','/') == 'Paused'
        dev, _, maps = mapped_device(vm)
        envdir = out.parent.parent/'env'
        cfg = select_config((envdir/'logs/server.log').read_text(),restored,dev,
                            list((envdir/'snapshot-local-cache/runtime').glob('*/memory/image.json')),envdir)
        image = json.loads(cfg.read_text())
        lower, = image['lowers']
        assert not image.get('upper',{}).get('data')
        path = envdir/lower['file'].removeprefix('/workspace/env/')
        assert path.resolve().is_relative_to((envdir/'snapshot-store/repository/managed-layers').resolve())
        assert lower['digest'].replace(':','_') in path.name
        opener = service_openers(binding,vm,path)[0]
        result['lower'] = dict(device=dev, inode=opener['inode'], file=path.name,
                               config=str(cfg.relative_to(envdir)),provider_pid=opener['pid'])
        group = Path('/sys/fs/cgroup')/vm['actual_cgroup'].lstrip('/')
        result['cgroup'] = vm['actual_cgroup']
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as pinned:
            st = os.fstat(pinned.fileno())
            assert (st.st_dev,st.st_ino,st.st_size)==(opener['device'],opener['inode'],lower['size'])
            for second in range(0,31,5):
                target = events['pause_response'] + second*10**9
                time.sleep(max(0,(target-clock())/1e9))
                check_vmm(vm,binding.cgroup)
                assert api(vm,'GET','/') == 'Paused'
                start = clock()
                r = mincore_file(path,file_descriptor=pinned.fileno(),include_resident_indices=True)
                end = clock()
                assert r['stable'] and (r['inode'],r['size'],r['mtime_ns'])==(st.st_ino,st.st_size,st.st_mtime_ns)
                vmstat = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
                mem = Path('/proc/meminfo').read_text()
                file_bytes = int(dict(line.split() for line in (group/'memory.stat').read_text().splitlines())['file'])
                pss = (Path('/proc')/str(vm['pid'])/'smaps_rollup').read_text()
                counters_end = clock()
                bits = bytearray((r['pages']+7)//8)
                for i in r['resident_indices']:
                    bits[i//8] |= 1 << (i%8)
                name = f'{second:02d}.bits'
                (out/name).write_bytes(bits)
                rows.append(dict(second=second,start_ns=start,end_ns=end,counters_end_ns=counters_end,
                                 pages=r['pages'],resident=r['resident_pages'],bitmap=name,file_bytes=file_bytes,
                                 pgscan=int(vmstat['pgscan_kswapd']),pgsteal=int(vmstat['pgsteal_kswapd']),
                                 available_kib=int(mem.split('MemAvailable:')[1].split()[0]),
                                 pss_kib=int(pss.split('\nPss:')[1].split()[0])))
        assert mapped_device(vm)[2] == maps
        check_vmm(vm,binding.cgroup)
        result['status'] = 'PASS'
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        if paused:
            def resume():
                check_vmm(vm,binding.cgroup)
                api(vm,'PATCH','/vm','Resumed')
                assert api(vm,'GET','/') == 'Running'
            cleanup('resume',resume)
        for key,sid in [('restored',restored),('source',source)]:
            if sid: cleanup(key,lambda:cli('delete',sid))
        if snapshot: cleanup('snapshot',lambda:cli('template','delete',snapshot))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
