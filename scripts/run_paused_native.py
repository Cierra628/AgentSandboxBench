"""Invoke the unmodified page collector with guest access explicitly disabled."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from live_task_cache import LiveTaskCache
from host_sandbox_monitor import Binding, UnixHTTP
from host_file_pfn_query import HostFilePfnQuery
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE, mapped_device


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
    collector = None
    result = dict(status='FAIL', scope='paused VMM; native entry boundary, not a completed page query', events={}, cleanup={})
    events = result['events']
    def cli(*args):
        proc = subprocess.run([ROOT/'runtime/bin/aenv', *args], capture_output=True, env=env, timeout=150)
        if proc.returncode:
            (out/'cli-error.log').write_bytes(proc.stderr)
            proc.check_returncode()
        return proc.stdout.decode().strip()
    source = restored = snapshot = vm = None
    paused = False
    class GuestAccessRequired(Exception):pass
    def no_guest(label,command,*args,**kwargs):
        result['entry_request']=dict(label=label,command=command,executed=False)
        raise GuestAccessRequired(label)

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
        collector=LiveTaskCache(out,a.container,no_guest,session='restored',session_name='native-pages',
                                lower_cache=True,provider_cache=True)
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
        check_vmm(vm,binding.cgroup)
        device,ident,maps=mapped_device(vm)
        result['mapped_ram']=dict(path=device,device=ident[0],inode=ident[1],rdev=ident[2],maps=len(maps))
        collector.expected_binding=vm
        events['entry_start']=clock()
        try:
            collector.start(restored,coordinated=True)
            collector._run(one_point=True)
            if collector.error:raise RuntimeError(collector.error)
        except GuestAccessRequired as exc:
            events['entry_stop']=clock()
            assert str(exc)=='live-cache-upload' and not collector.samples
            result['four_source_status']='UNAVAILABLE_REQUIRES_GUEST_EXPORT'
        else:
            raise RuntimeError('unexpected collector path; see native receipts')
        assert api(vm,'GET','/')=='Paused'
        result['status'] = 'PASS'
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        if collector:cleanup('kvm_capture',collector.capture.close)
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
