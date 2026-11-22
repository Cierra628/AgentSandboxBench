"""Time the normal single-restore preparation; never request a cache publication."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from contextlib import ExitStack
from unittest.mock import patch
import struct
from live_task_cache import LiveTaskCache
from guest_cache_exporter import REQUEST, STOP
from host_sandbox_monitor import Binding
from provider_slot_cache import ProviderCache
from snapshot_cache_residency import SourceTracer, SnapshotCache
import snapshot_layer_metadata
from prettier_no_proxy import clean_environment
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE


clock = time.monotonic_ns
ROOT = Path(__file__).resolve().parents[1]


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
    result = dict(status='FAIL', scope='single restore; preparation only; no page query',
                  stages=rows, events={}, cleanup={})
    events = result['events']
    def cli(*args):
        proc = subprocess.run([ROOT/'runtime/bin/aenv', *args], capture_output=True, env=env, timeout=150)
        if proc.returncode:
            (out/'cli-error.log').write_bytes(proc.stderr)
            proc.check_returncode()
        return proc.stdout.decode().strip()
    def timed(label,fn,*args,**kwargs):
        a=clock()
        try:return fn(*args,**kwargs)
        finally:rows.append(dict(stage=label,start_ns=a,end_ns=clock()))

    def guest_cli(label,*args,timeout=150):
        return timed(label,cli,*args)

    source = restored = snapshot = vm = None
    reader = None
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
        reader = LiveTaskCache(out,a.container,guest_cli,session='restored',
                               session_name='observed',lower_cache=True,provider_cache=True)
        events['restore_start'] = clock()
        restored = cli('start',snapshot,'--detach','--timeout','1800')
        events['restore_return'] = clock()
        vm = timed('bind_vmm',bind_vmm,binding,restored,out,'restored')
        timed('contents',cli,'exec',restored,'python3','-c', CONTENT+'assert p.read_bytes()==data; '+WRITE)
        reader.expected_binding=vm
        # Boundaries only: call originals with their original arguments, in order.
        with ExitStack() as taps:
            def tap(owner,name,label):
                original=getattr(owner,name)
                def call(*args,**kwargs):
                    key=label(*args,**kwargs) if callable(label) else label
                    return timed(key,original,*args,**kwargs)
                taps.enter_context(patch.object(owner,name,call))
            tap(reader.capture,'ready','capture.ready')
            tap(reader,'_start_guest_exporter','exporter_setup')
            tap(reader.capture,'bind','capture.bind')
            tap(reader,'_check_rollout_binding','check_binding')
            tap(reader,'_start_snapshot_queries','host_query_setup')
            tap(snapshot_layer_metadata,'owned_memory_layer','lower_metadata')
            tap(SnapshotCache,'__init__',lambda *args,**kw:'lower_reader' if kw.get('file_path') else 'backend_reader')
            tap(SourceTracer,'__init__',lambda obj,key,*args,**kw:'attach_'+key[1])
            tap(ProviderCache,'__init__','provider_init')
            timed('collector_start',reader.start,restored,coordinated=True)
        events['can_issue_request']=next(row['end_ns'] for row in rows if row['stage']=='collector_start')
        assert reader.request==0 and not reader.samples and reader.thread is None
        os.pwrite(reader.memory.fileno(),struct.pack('<Q',STOP),reader.addresses[0]+REQUEST)
        cli('exec',restored,'bash','-lc',f'for i in $(seq 1 100); do test -f {reader.guest}/done && exit 0; sleep .1; done; exit 1')
        receipt=json.loads(cli('exec',restored,'cat',reader.guest+'/result.json'))
        assert receipt['status']=='PASS' and receipt['publications']==0
        result['publications']=receipt['publications']
        result['status']='PASS'
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        if reader:
            for key,obj in [('provider',reader.provider),('lower',reader.lower_query),('backend',reader.backend),
                            ('memory',getattr(reader,'memory',None)),('pagemap',getattr(reader,'pm',None)),('capture',reader.capture)]:
                if obj: cleanup(key,lambda:obj.close(final_snapshot=False) if key=='provider' else obj.close())
        for key,sid in [('restored',restored),('source',source)]:
            if sid: cleanup(key,lambda:cli('delete',sid))
        if snapshot: cleanup('snapshot',lambda:cli('template','delete',snapshot))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
