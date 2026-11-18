"""One 1s/2s PSS comparison, two concurrent C4 guests per private service."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback
import urllib.request

from external_oci import validate
from external_task_metrics import SNAPSHOT_FILES
from external_workload import load,validate_c4_outputs,validate_timings
from isolated_storage import available,capacity_budget,GIB
from platform_tasks import ROOT
from prettier_no_proxy import clean_environment
from run_c4_parallel import validate_parallel
from run_c4_parallel_pair import TIMING_KEYS,read,write
from run_light_monitor_controls import SOURCE,SOURCE_ID


def inspect_run(workflow,interval,expected):
    work=read(workflow/'result.json');assert work['status']=='PASS'
    task=work['task'];validate_parallel(task,'on',pss_interval_seconds=interval)
    raw=Path(work['raw']);storage=Path(work['storage']);outer=read(storage/'result.json')
    assert outer['status']=='PASS'
    identities=[]
    for i,child in enumerate(task['children']):
        directory=raw/f'c4-parallel-{i}';validate_c4_outputs(directory/'input/logs')
        with (directory/'tools.tsv').open() as stream:tools=list(csv.DictReader(stream,delimiter='\t'))
        validate_timings(tools)
        assert len(tools)==36 and [int(t['exit_code']) for t in tools]==[int(t['exit_code']) for t in expected]
        guest=read(directory/'runtime-monitor.json')
        assert guest['profile']=='light' and guest['interval_seconds']==.1 and not guest['host_vmm_requested']
        assert len(guest['sessions'])==1 and guest['sessions'][0]['collector_reaped']
        assert guest['sessions'][0]['receipt']['status']=='PASS'
        collector=child['host_vmm_collector']
        assert read(directory/'host-vmm/external/collector-status.json')['pss_interval_seconds']==interval
        with (directory/'host-vmm/external/host-vmm.tsv').open() as stream:points=list(csv.DictReader(stream,delimiter='\t'))
        assert len(points)==collector['samples'] and sum(p['pss_kib']!='' for p in points)==collector['pss_samples']
        pss=[int(p['monotonic_ns']) for p in points if p['pss_kib']!='']
        assert pss and all(b-a>=interval*10**9 for a,b in zip(pss,pss[1:]))
        assert child['endpoint_metrics']['p1']['unique_files']==9
        identities.append({(f['device'],f['inode']) for f in child['endpoint_metrics']['p1']['files']})
    assert identities[0]==identities[1] and len(identities[0])==9
    assert work['collector']['status']=='PASS' and work['collector']['interval_seconds']==.1
    assert work['collector']['pss_interval_seconds'] is None
    assert outer['host_file_residency']['status']=='NOT_REQUESTED' and not outer.get('delete_trace')
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed',
        'successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
    return dict(pss_interval_seconds=interval,workflow=str(workflow),storage=str(storage),raw=str(raw),
        task=task,host_collector=work['collector'],min_disk_gib=outer['observed_host_available_min_bytes']/GIB)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','upstream','oci-input'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();out.mkdir(parents=True)
    state=dict(status='RUNNING',runs=[]);started=False
    cfg=ROOT/'configs/external-c4-light.json'
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,PYTHONDONTWRITEBYTECODE='1'))
    try:
        config,_,expected=load(cfg,args.upstream);validate(args.oci_input,config)
        code=out/'code';code.mkdir()
        for name in set(SNAPSHOT_FILES)|{'run_c4_pss_interval_pair.py','run_c4_parallel_pair.py','run_c4_parallel.py',
                'run_external_workload.py','run_host_external_workload.py','validate_isolated_agentenv.py',
                'external_workload.py','external_oci.py','isolated_storage.py'}:shutil.copy2(ROOT/'scripts'/name,code/name)
        shutil.copy2(cfg,out/'config.json')
        write(out/'plan.json',dict(order=[1,2],guest_count=2,actions_per_guest=36,total_actions=144,
            ordinary_interval_seconds=.1,p1_files_per_guest=9,proxy=False,cache_reset=False,
            delete_trace=False,oci_input=str(args.oci_input.resolve()),upstream=str(args.upstream.resolve())))
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running']
        state['source_before']=dict(id=source['Id'],image=source['Image'],running=False)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health')
        for interval in (1,2):
            budget=capacity_budget(available(ROOT),32*GIB);assert budget['ready'],budget
            mem=int(next(s for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')).split()[1])*1024
            assert mem>64*GIB
            state.update(stage=f'{interval}s',available_disk_bytes=budget['available_bytes'],mem_available_bytes=mem,load_average=os.getloadavg())
            write(out/'result.json',state)
            command=['bash',str(ROOT/'scripts/run-external-workload-light.sh'),str(cfg),str(args.upstream.resolve()),
                '--sampling','on','--endpoint-metrics','--sampling-control','--guest-count','2',
                '--oci-input',str(args.oci_input.resolve()),'--vmm-pss-interval-seconds',str(interval)]
            write(out/f'{interval}s-command.json',command)
            print(f'status=RUNNING pss_interval={interval}s result_dir={out}',flush=True)
            logpath=out/f'{interval}s.log'
            with logpath.open('x') as log:result=subprocess.run(command,env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1500)
            paths={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',logpath.read_text()) if Path(p).parent==ROOT/'.artifacts/host-external-workload'}
            if len(paths)==1:state['last_workflow']=str(next(iter(paths)))
            assert result.returncode==0,'first failure retained; no retry'
            assert len(paths)==1
            state['runs'].append(inspect_run(paths.pop(),interval,expected));write(out/'result.json',state)
            print(f'status=PASS pss_interval={interval}s',flush=True)
        assert len({c['sandbox_id'] for r in state['runs'] for c in r['task']['children']})==4
        a,b=[r['task'] for r in state['runs']]
        state['changes']={k:dict(one_second=a[k]/1e9,two_seconds=b[k]/1e9,
            delta_seconds=(b[k]-a[k])/1e9,change_percent=(b[k]/a[k]-1)*100) for k in TIMING_KEYS}
        state.update(status='PASS',stage='complete')
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:subprocess.run(['docker','stop','-t','30',SOURCE],check=True,capture_output=True,timeout=60)
            except Exception as error:state.update(status='FAIL',source_cleanup_error=repr(error))
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        state['source_after']=dict(id=source['Id'],image=source['Image'],running=source['State']['Running'])
        state['source_stopped']=not source['State']['Running']
        if started and state['source_after']!=state['source_before']:state['status']='FAIL'
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
