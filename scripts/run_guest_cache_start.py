"""Run the agreed preserve/drop/drop/preserve initial-cache comparison."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

from experiment_disk_guard import writer_lock
from probe_storage_boundary import identities
from report_guest_cache_start import ORDER, check_run, compare, report

ROOT=Path(__file__).resolve().parents[1]
SERVICE='agentenv-smoke-20260922T114006Z-1387374'


def service():
    value=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SERVICE],text=True,timeout=10))
    assert value['Running']
    return {k:value[k] for k in ('Pid','StartedAt','Running')}


def run(output,resume=False):
    output=output.resolve();assert output.is_relative_to(ROOT/'.artifacts')
    output.mkdir(parents=True,exist_ok=True)
    assert resume or not (output/'runs.json').exists(), 'use a new output directory; no automatic reruns'
    if resume:
        original=json.loads((output/'original-resources.json').read_text())
        assert dict(service=service(),vmms=identities())==original
    else:
        original=dict(service=service(),vmms=identities());assert len(original['vmms'])==12
        (output/'original-resources.json').write_text(json.dumps(original,indent=2)+'\n')
    implemented=output/'implemented';implemented.mkdir(exist_ok=resume)
    for name in ('run_prettier6604_grpo.py','validate_isolated_agentenv.py','guest_page_cache_control.py',
                 'kernel_cache_metadata.py','guest_diskstats.py','report_guest_cache_start.py',
                 'run_guest_cache_start.py','run-prettier-snapshot-cache-pair.sh'):
        if resume and name not in ('run_guest_cache_start.py','report_guest_cache_start.py'):
            assert (implemented/name).read_bytes()==(ROOT/'scripts'/name).read_bytes(),name
        else:shutil.copy2(ROOT/'scripts'/name,implemented/name)
    runs=json.loads((output/'runs.json').read_text()) if resume else []
    assert [r['policy'] for r in runs]==list(ORDER[:len(runs)])
    result=dict(status='FAIL',order=list(ORDER),live_runs=len(runs));checked=[];stage='preflight'
    try:
        for index,entry in enumerate(runs,1):
            stage=f'{index}-saved-run-verification'
            value=check_run(Path(entry['storage']),Path(entry['wrapper']),entry['policy']);checked.append(value)
            (output/(f'{index}-'+entry['policy']+'-checks.json')).write_text(json.dumps(value,indent=2)+'\n')
        if len(checked)>=2:compare(checked[0],checked[1])
        for index,policy in enumerate(ORDER,1):
            if index<=len(checked):continue
            key=f'{index}-{policy}';stage=key+'-preflight'
            free=shutil.disk_usage(ROOT).free;assert free>50*2**30
            available=int(next(s for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')).split()[1])*1024
            assert available>64*2**30
            with writer_lock():pass
            assert dict(service=service(),vmms=identities())==original
            for p in implemented.iterdir():assert p.read_bytes()==(ROOT/'scripts'/p.name).read_bytes(),p.name
            preflight=dict(status='PASS',policy=policy,time_ns=time.time_ns(),host_available_bytes=free,
                host_available_memory_bytes=available,load=subprocess.check_output(['uptime'],text=True).strip(),
                original_resources_unchanged=True,capacity_gib=24,reserve_gib=20,margin_gib=6)
            (output/(key+'-preflight.json')).write_text(json.dumps(preflight,indent=2)+'\n')
            stage=key+'-runtime';print('status=RUNNING stage='+stage+' result_dir='+str(output),flush=True)
            with (output/(key+'-runtime.log')).open('x') as log:
                proc=subprocess.run(['timeout','-k','120s','1200s','bash','scripts/run-prettier-snapshot-cache-pair.sh',
                    '--snapshot-provider-binary','.artifacts/provider-segments-build-20261016/uvm-ublk-daemon',
                    '--snapshot-provider-get-data','--snapshot-bpf-batch',
                    '--prettier6604-guest-cache-policy',policy],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            (output/(key+'-runtime.rc')).write_text(str(proc.returncode)+'\n')
            result['live_runs']=index
            assert proc.returncode==0, 'wrapper failed; inspect '+key+'-runtime.log'
            matches=re.findall(r'status=PASS result_dir=(\S+)',(output/(key+'-runtime.log')).read_text())
            wrappers=[Path(p) for p in matches if Path(p).parent==ROOT/'.artifacts/prettier-snapshot-cache-pair']
            assert len(wrappers)==1
            wrapper=wrappers[0];saved=json.loads((wrapper/'result.json').read_text());storage=Path(saved['storage_result_dir'])
            entry=dict(policy=policy,storage=str(storage),wrapper=str(wrapper));runs.append(entry)
            (output/'runs.json').write_text(json.dumps(runs,indent=2)+'\n')
            stage=key+'-verification';value=check_run(storage,wrapper,policy);checked.append(value)
            assert dict(service=service(),vmms=identities())==original
            seed=ROOT/'.artifacts/prettier6604'/('pair-cache-'+wrapper.name)
            state=json.loads((seed/'resources.json').read_text())
            assert state['released'] and not state['memory_mounted'] and not state['readonly_mounted'] and not state['loop_device']
            assert not subprocess.check_output(['losetup','-j',str(storage/'volume.ext4')],text=True).strip()
            assert subprocess.run(['findmnt','-rn','-M',str(storage/'mount')],capture_output=True).returncode==1
            (output/(key+'-checks.json')).write_text(json.dumps(value,indent=2)+'\n')
            if index==2:compare(checked[0],checked[1])
            print('status=PASS stage='+key+'-complete result_dir='+str(storage),flush=True)
        stage='offline-report';summary=report(output/'runs.json',output/'report')
        result.update(status=summary['status'],task_actions=124,original_resources_unchanged=True,owned_resources_released=True)
    except BaseException as exc:
        result.update(error=repr(exc),failed_stage=stage)
        (output/'failure.txt').write_text(traceback.format_exc())
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(output),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Explicitly recheck saved successful runs and execute only missing planned runs')
    args=parser.parse_args();raise SystemExit(run(args.output,args.resume))
