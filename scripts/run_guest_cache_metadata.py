"""Private AgentENV: pair complete boot-time cache metadata with live host PFNs."""
import argparse
import array
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

from agentenv_cold_start import create
from agentenv_whole_cache import bind_vm
from kernel_cache_metadata import decode,join
from measure_file_physical import identity,ram_address,read_entry
from task_physical_cache import TaskPhysicalCache

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',required=True)
    parser.add_argument('--config-home',type=Path,required=True)
    parser.add_argument('--service-url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir(exist_ok=False)
    env=dict(os.environ,XDG_CONFIG_HOME=str(args.config_home))
    sid=capture=None;stage='preflight';success=False
    result=dict(status='FAIL',vm_pause=False,scope='whole guest Cached membership, regular files plus shmem; excludes block-device/swap-only cache',pairs=[])

    def cli(label,*argv,timeout=60):
        process=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(process.stdout);(out/(label+'.stderr')).write_bytes(process.stderr)
        process.check_returncode();return process.stdout.decode().strip()

    try:
        capture=TaskPhysicalCache(out,args.container,cli)
        config=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())
        stage='diagnostic_guest_boot'
        sid=create(args.config_home,args.service_url,config['workload_image'],request_timeout=90)
        (out/'sandbox-id.txt').write_text(sid+'\n')
        cli('ready','exec',sid,'true',timeout=15)
        kernel=cli('kernel','exec',sid,'uname','-r')
        assert kernel=='6.1.175-asb-cache-v3', 'diagnostic guest kernel not active'
        cli('interface','exec',sid,'test','-r','/proc/asb_pagecache')
        tools=out/'tools';tools.mkdir()
        for name in ('guest_pagecache_marker.py','kernel_cache_metadata.py'):
            shutil.copy2(ROOT/'scripts'/name,tools/name)
        cli('upload','upload',sid,tools,'/workspace/asb-whole-tools')
        guest='/dev/shm/asb-whole-'+sid
        stage='known_cache_types'
        cli('marker-start','exec',sid,'bash','-lc',f'nohup timeout 160 python3 /workspace/asb-whole-tools/guest_pagecache_marker.py {sid} {guest} >/tmp/asb-whole.log 2>&1 </dev/null &')
        cli('marker-ready','exec',sid,'bash','-lc',f'for i in $(seq 1 100); do test -f {guest}/bootstrap.json && exit 0; test ! -f {guest}/failure.txt || break; sleep 0.1; done; cat /tmp/asb-whole.log; exit 1',timeout=15)
        bootstrap=json.loads(cli('bootstrap','exec',sid,'cat',guest+'/bootstrap.json'))
        (out/'bootstrap.json').write_text(json.dumps(bootstrap,indent=2)+'\n')
        (out/'marker.json').write_text(json.dumps(bootstrap)+'\n');(out/'iomem.txt').write_text(bootstrap['iomem'])
        capture.ready();shutil.copy2(capture.directory/'kvm-slots.tsv',out/'kvm-slots.tsv')
        pids=[int(p) for p in capture.cgpath.joinpath('cgroup.procs').read_text().split() if Path('/proc',p,'comm').read_text().strip()=='firecracker']
        assert pids
        vm,proof=bind_vm(out,pids,capture.cgroup,os.readlink('/proc/'+str(pids[0])+'/exe'),sid)
        proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        assert all(0<=slot['guest']+slot['size']<=8*2**30 for slot in proof['slots'])
        marker_address=ram_address(bootstrap['pfn'],proof['slots'])
        def snapshot(label):
            program="import base64,json,pathlib,time; a=pathlib.Path('/proc/meminfo').read_text(); t=time.monotonic_ns(); b=pathlib.Path('/proc/asb_pagecache').read_bytes(); u=time.monotonic_ns(); c=pathlib.Path('/proc/meminfo').read_text(); print(json.dumps(dict(meminfo_before=a,metadata=base64.b64encode(b).decode(),meminfo_after=c,guest_start_ns=t,guest_end_ns=u)))"
            value=json.loads(cli(label,'exec',sid,'python3','-c',program))
            raw=base64.b64decode(value.pop('metadata'),validate=True)
            decode(raw)
            (out/(label+'.bin')).write_bytes(raw);(out/(label+'.json')).write_text(json.dumps(value,indent=2)+'\n')
            return raw,value
        def bulk(fd):
            values=[]
            for slot in proof['slots']:
                raw=os.pread(fd,slot['size']//4096*8,slot['host']//4096*8)
                assert len(raw)==slot['size']//4096*8
                data=array.array('Q');data.frombytes(raw);values.append((slot,data))
            return values
        def entry(pfn,values):
            for slot,data in values:
                if slot['guest']<=pfn*4096<slot['guest']+slot['size']:
                    return data[(pfn*4096-slot['guest'])//4096]
            raise ValueError('cache PFN outside attested RAM')
        stage='whole_cache_pairs'
        with (proc/'pagemap').open('rb',buffering=0) as pm,(proc/'mem').open('rb',buffering=0) as mem:
            ticks=[]
            for point in range(6):
                started=time.monotonic_ns();wall=time.time_ns()
                before,guest_before=snapshot(f'pair-{point:02}-before')
                left,pages=decode(before)
                for known in bootstrap['known_pages']:
                    assert pages.get(known['pfn'],0)>0
                    assert bool(pages[known['pfn']]&1)==(known['kind']=='shmem')
                assert bootstrap['pfn'] not in pages
                ticks.append(struct.unpack('<Q',os.pread(mem.fileno(),8,marker_address+128))[0])
                host_start=time.monotonic_ns();first=bulk(pm.fileno());middle=bulk(pm.fileno())
                host_end=time.monotonic_ns()
                after,guest_after=snapshot(f'pair-{point:02}-after')
                final=bulk(pm.fileno())
                rows=[dict(guest_pfn=pfn,first=entry(pfn,first),middle=entry(pfn,middle),last=entry(pfn,final)) for pfn,tag in pages.items() if tag]
                value=join(before,after,rows)
                value.update(point=point,timestamp_ns=wall,elapsed_ns=time.monotonic_ns()-started,
                    host_first_two_scans_ns=host_end-host_start,
                    guest_proc_read_before_ns=guest_before['guest_end_ns']-guest_before['guest_start_ns'],
                    guest_proc_read_after_ns=guest_after['guest_end_ns']-guest_after['guest_start_ns'])
                (out/f'pair-{point:02}-host.json').write_text(json.dumps(rows)+'\n')
                result['pairs'].append(value)
                time.sleep(.5)
            assert ticks[-1]>ticks[0], 'guest did not continue while measuring'
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        cli('marker-release','exec',sid,'touch',guest+'/release')
        cli('marker-done','exec',sid,'bash','-lc',f'for i in $(seq 1 40); do test -f {guest}/done && exit 0; sleep 0.1; done; cat /tmp/asb-whole.log; exit 1',timeout=10)
        result.update(kernel=kernel,known_types='regular locked file and tmpfs included, anonymous excluded',
            guest_heartbeats=ticks,metadata_static_bytes=16*2**20+256*2**10,
            limitations=['New isolated guest build/config; not the original binary or a performance comparison.',
            'Host bytes are the confirmed union across unchanged lifetimes; unresolved current pages are separate.',
            'Kernel bitmap uses a short spinlock copy; no VM pause; collection overhead not yet controlled.',
            'THP disabled for this diagnostic build; no forced migration or swap test.'])
        success=True
    except BaseException as exc:
        import traceback
        result.update(error=repr(exc),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
        if sid:
            try:cli('guest-failure','exec',sid,'cat','/tmp/asb-whole.log',timeout=5)
            except Exception:pass
    finally:
        if capture:capture.close()
        if sid:
            try:cli('delete','delete',sid,timeout=60)
            except Exception as exc:success=False;result['cleanup_error']=repr(exc)
        result['status']='PASS' if success else 'FAIL'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],failed_stage=result.get('failed_stage'),output=str(out))))
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
