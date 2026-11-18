"""Two private running VMMs: separate cache identities and asymmetric eviction."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from agentenv_cold_start import create
from experiment_memory import sample_groups
from host_sandbox_monitor import read as read_vmm
from live_task_cache import LiveTaskCache
from measure_file_physical import identity
from prettier_no_proxy import IMAGE,clean_environment
from report_two_child_cache import check_targets
from report_dynamic_two_child_cache import check_targets as check_dynamic_targets

ROOT=Path(__file__).resolve().parents[1]
ROLES=('private-a','private-b')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',required=True);p.add_argument('--config-home',type=Path,required=True)
    p.add_argument('--service-url',required=True);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--dynamic-file',action='store_true')
    p.add_argument('--ram-page-evidence',action='store_true')
    p.add_argument('--vmm-page-evidence',action='store_true')
    p.add_argument('--io-uring-page-evidence',action='store_true')
    p.add_argument('--host-backing-evidence',action='store_true')
    args=p.parse_args();out=args.output.resolve()
    if args.io_uring_page_evidence and not args.vmm_page_evidence:
        raise ValueError('io_uring evidence requires explicit VMM and RAM evidence')
    if args.vmm_page_evidence and not args.ram_page_evidence:
        raise ValueError('VMM mapping evidence requires explicit RAM evidence')
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=str(args.config_home)))
    lives={};sids={};events=[];success=False;stage='capture_before_both_boots'
    guest='/dev/shm/asb-child-fixture'
    result=dict(status='FAIL',vm_pause=False,checkpoint=False,guest_count=2,image=IMAGE,
        phases=[],guests={},dynamic_file=args.dynamic_file,scope='two private diagnostic guests with overlapping collection; not a shared snapshot or original task')
    def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
    def cli(role,label,*argv,timeout=60):
        started=time.monotonic_ns()
        proc=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(role+'-'+label+'.stdout')).write_bytes(proc.stdout)
        (out/(role+'-'+label+'.stderr')).write_bytes(proc.stderr)
        events.append(dict(role=role,stage=label,start_ns=started,duration_ns=time.monotonic_ns()-started,returncode=proc.returncode))
        proc.check_returncode();return proc.stdout.decode().strip()
    def wait(role,name):
        cli(role,'wait-'+name,'exec',sids[role],'bash','-lc',
            f'for i in $(seq 1 100); do test -f {guest}/{name} && exit 0; sleep .1; done; cat /tmp/asb-child-fixture.log; exit 1',timeout=15)
    def state(role,name):
        wait(role,name+'.json')
        value=json.loads(cli(role,'state-'+name,'exec',sids[role],'cat',guest+'/'+name+'.json'))
        write(out/(role+'-'+name+'.json'),value)
        if args.dynamic_file:
            cli(role,'download-'+name+'-file','download',sids[role],guest+'/'+name+'-file.bin',out/(role+'-'+name+'-file.bin'))
        return value
    def request(role,name):cli(role,'request-'+name,'exec',sids[role],'touch',guest+'/'+name)
    def together(name):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(request,role,name) for role in ROLES]
            for future in futures:future.result()
    def observe(name):
        started=time.time_ns();mono=time.monotonic_ns();time.sleep(3)
        phase=dict(name=name,start_ns=started,end_ns=time.time_ns(),start_monotonic_ns=mono,
            end_monotonic_ns=time.monotonic_ns(),vmms={})
        for role,live in lives.items():
            if live.error:raise RuntimeError(role+': '+live.error)
            identity(live.vm,Path('/proc'),Path('/sys/fs/cgroup'))
            phase['vmms'][role]=read_vmm(live.vm['pid'],pss=True)
        phase['groups']=sample_groups(lives[ROLES[0]].capture.cgpath)
        result['phases'].append(phase)
    try:
        # Both captures must exist before either VMM registers RAM.
        live_options=dict(file_cache_path='/workspace/asb-child-cache.bin',expected_kernel='6.1.175-asb-file-query-v1') if args.dynamic_file else {}
        if args.ram_page_evidence:live_options['ram_page_evidence']=True
        if args.vmm_page_evidence:live_options['vmm_page_evidence']=True
        if args.io_uring_page_evidence:live_options['io_uring_page_evidence']=True
        for role in ROLES:
            lives[role]=LiveTaskCache(out,args.container,lambda label,*argv,_role=role,**kw:cli(_role,label,*argv,**kw),session=role,**live_options)
            assert lives[role].capture.child_scope
        for role in ROLES:
            stage='boot-'+role
            sids[role]=create(args.config_home,args.service_url,IMAGE,request_timeout=90,local_no_proxy=True)
            result['guests'][role]=dict(sandbox_id=sids[role])
            cli(role,'ready','exec',sids[role],'true',timeout=15)
        assert len(set(sids.values()))==2
        for role in ROLES:
            stage='start-collector-'+role;lives[role].start(sids[role])
            result['guests'][role]['host_identity']=lives[role].vm
        assert len({v.vm['pid'] for v in lives.values()})==2
        assert len({v.vm['cgroup'] for v in lives.values()})==2
        tools=out/'tools';tools.mkdir()
        for name in ('guest_cache_child_fixture.py','kernel_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,tools/name)
        if args.dynamic_file:
            for name in ('guest_file_cache_query.py','file_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,tools/name)
        for role in ROLES:
            cli(role,'fixture-upload','upload',sids[role],tools,'/workspace/asb-child-tools')
            cli(role,'fixture-start','exec',sids[role],'bash','-lc',
                f'nohup timeout 150 python3 -B /workspace/asb-child-tools/guest_cache_child_fixture.py {guest} --wait-for-begin'+(' --dynamic-file' if args.dynamic_file else '')+' >/tmp/asb-child-fixture.log 2>&1 </dev/null &')
            wait(role,'waiting')
        stage='both_written';together('begin')
        for role in ROLES:state(role,'written')
        observe(stage)
        if args.dynamic_file:
            for role,letter in zip(ROLES,('a','b')):
                stage=letter+'_evicted';request(role,'evict');state(role,'evicted');observe(stage)
                stage=letter+'_reread';request(role,'reread');state(role,'reread');observe(stage)
        else:
            stage='both_read';together('read')
            for role in ROLES:state(role,'read-done')
            observe(stage)
            stage='a_evicted_b_retained';request('private-a','evict');state('private-a','evicted');observe(stage)
            stage='both_evicted';request('private-b','evict');state('private-b','evicted');observe(stage)
        stage='stop_collectors'
        for role in ROLES:
            lives[role].close();result['guests'][role]['collector_stopped']=True
        if args.host_backing_evidence:
            from host_backing_evidence import observe as observe_backing
            stage='host_backing_evidence'
            result['host_backing_evidence']=observe_backing(args.container,out,sids,{r:live.vm for r,live in lives.items()})
        together('finish')
        for role in ROLES:
            wait(role,'done');fixture=json.loads(cli(role,'fixture-result','exec',sids[role],'cat',guest+'/result.json'))
            assert fixture['status']=='PASS';result['guests'][role]['fixture']=fixture
        stage='saved_page_evidence_check'
        checked=(check_dynamic_targets if args.dynamic_file else check_targets)(out,result)
        write(out/'target-check.json',checked)
        assert checked['status']=='PASS', checked['errors']
        success=True
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
        for role,sid in sids.items():
            try:cli(role,'fixture-failure','exec',sid,'cat','/tmp/asb-child-fixture.log',timeout=5)
            except Exception:pass
    finally:
        for role,live in lives.items():
            if not result['guests'].get(role,{}).get('collector_stopped'):
                try:live.close()
                except BaseException as error:success=False;result.setdefault('collector_cleanup_errors',{})[role]=repr(error)
        for role,sid in sids.items():
            try:cli(role,'delete','delete',sid,timeout=90);result['guests'][role]['vm_removed']=True
            except BaseException as error:success=False;result.setdefault('vm_cleanup_errors',{})[role]=repr(error)
        result['status']='PASS' if success else 'FAIL'
        write(out/'events.json',events);write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
