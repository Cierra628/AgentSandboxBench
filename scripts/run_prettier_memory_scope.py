"""One opt-in Prettier GRPO: prepare in a dedicated slice, VMMs born in children."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from platform_tasks import ROOT
from isolated_storage import available,capacity_budget,GIB
from prettier_no_proxy import clean_environment
from experiment_memory import GroupSampler,sample_groups
from vmm_child_cgroup_check import accounting_controller_state,accounting_acceptance


def write(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def smoke_output(out):
    return out/('run-'+out.name)


def inside(out,unit):
    membership=Path('/proc/self/cgroup').read_text().strip()
    assert membership.startswith('0::/')
    control=Path('/sys/fs/cgroup')/membership[3:].lstrip('/')
    parent=control.parent
    assert parent.name==unit+'.slice' and not (parent/'cgroup.procs').read_text().strip()
    assert {'memory','io'}<=set((parent/'cgroup.subtree_control').read_text().split())
    assert capacity_budget(available(ROOT),32*GIB)['ready']
    value=dict(status='RUNNING',parent=str(parent),controller=str(control),
        scope='Private preparation through cleanup; source service and pre-existing globally charged input excluded',
        available_before_bytes=available(ROOT),schedule='grpo',requested_runs=1)
    sampler=GroupSampler(parent,out/'full-memory')
    try:
        value['controller_states_before_preparation']=[accounting_controller_state(p,require_io=True) for p in [parent,control]]
        value['systemd_accounting']=subprocess.check_output(['systemctl','show',unit+'control.service',
            '-p','IOAccounting','-p','MemoryAccounting'],text=True,timeout=15)
        assert 'IOAccounting=yes' in value['systemd_accounting']
        subprocess.run(['gcc','-O2','-Wall','-Wextra','scripts/vmm_cgroup_launcher.c','-o',str(out/'vmm-cgroup-launcher')],check=True,timeout=30)
        env=clean_environment(dict(os.environ,ASB_VMM_CHILD_CGROUP_CHECK=str(out),
            ASB_VMM_CGROUP_PARENT=str(parent),ASB_EXPERIMENT_MEMORY_SERIES=str(out/'full-memory/memory.jsonl'),
            ASB_VMM_ACCOUNTING_ONLY='on',ASB_VMM_REQUIRE_IO='on',PYTHONDONTWRITEBYTECODE='1'))
        write(out/'preparation-start.json',dict(monotonic_ns=time.monotonic_ns(),parent=str(parent),controller=str(control)))
        run_out=smoke_output(out)
        cmd=[sys.executable,'-B','scripts/run_schedule_smoke.py','--output',str(run_out),
            '--schedule','grpo','--expanded-files','--cache-layers','--block-cache-method','cachestat']
        with (out/'run.log').open('x') as log:
            completed=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        value['business_entry_exit_code']=completed.returncode
        if completed.returncode:raise RuntimeError('single GRPO failed; see run.log; no retry')
        batch=json.loads((run_out/'result.json').read_text());assert batch['status']=='PASS'
        value['raw']=batch['runs'][0]['raw'];value['storage']=batch['runs'][0]['storage']
        metrics=json.loads((Path(value['raw'])/'schedule-smoke-metrics.json').read_text())
        assert metrics['experiment_memory_context']['status']=='AVAILABLE'
        assert metrics['experiment_memory_context']['parent']==str(parent)
        assert len(metrics['vmm_child_cgroups']['children'])==7
        assert all(c.get('io_controller_state') for c in metrics['vmm_child_cgroups']['children'])
        value['status']='PASS'
    except BaseException as error:
        value.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        try:
            value['after_cleanup']=sample_groups(parent)
            value['sampling']=sampler.stop()
            if value.get('raw'):
                metrics=json.loads((Path(value['raw'])/'schedule-smoke-metrics.json').read_text())
                samples=[json.loads(line) for line in (out/'full-memory/memory.jsonl').read_text().splitlines()]
                value['full_series_acceptance']=accounting_acceptance(metrics,samples)
                if value['full_series_acceptance']['status']!='PASS':value['status']='FAIL'
            removed=[]
            for group in parent.glob('vmm-*'):
                assert not (group/'cgroup.procs').read_text().strip(),'VMM still alive'
                group.rmdir();removed.append(str(group))
            value['empty_vmm_groups_removed']=removed
        except Exception as error:value.update(status='FAIL',cleanup_error=repr(error))
        value['available_after_bytes']=available(ROOT);write(out/'result.json',value)
    return 0 if value['status']=='PASS' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--inside',action='store_true');parser.add_argument('--unit')
    args=parser.parse_args();out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts')
    if args.inside:return inside(out,args.unit)
    assert not out.exists()
    budget=capacity_budget(available(ROOT),32*GIB)
    assert budget['ready'],budget
    out.mkdir();(out/'code').mkdir()
    for name in ['run_prettier_memory_scope.py','vmm_cgroup_launcher.c','vmm_child_cgroup_check.py','experiment_memory.py','schedule_smoke_metrics.py','run_prettier6604_grpo.py']:
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    unit='asbvmm'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit+'control','--slice='+unit+'.slice',
        '--property=WorkingDirectory='+str(ROOT),'--property=MemoryAccounting=yes','--property=IOAccounting=yes',sys.executable,'-B',str(Path(__file__).resolve()),
        '--output',str(out),'--inside','--unit',unit]
    write(out/'launch.json',dict(command=command,budget=budget,unit=unit))
    with (out/'launch.log').open('x') as log:
        result=subprocess.run(command,env=clean_environment(os.environ),stdout=log,stderr=subprocess.STDOUT,timeout=1300)
    print('status='+('PASS' if result.returncode==0 else 'FAIL')+' result_dir='+str(out),flush=True)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
