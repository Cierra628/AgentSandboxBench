"""One explicit Prettier GRPO run after the saved mincore and clone checks."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.request

from isolated_storage import available, capacity_budget, GIB
from platform_tasks import ROOT, configuration, commands
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE, SOURCE_ID


def run(out, cache):
    assert out.is_relative_to(ROOT/'.artifacts') and out.is_dir()
    assert not (out/'run-result.json').exists(), 'one attempt only'
    assert json.loads((out/'clone-test.json').read_text())['status']=='PASS'
    assert json.loads((out/'mincore-permissions.json').read_text())['status']=='PASS'
    assert capacity_budget(available(ROOT),32*GIB)['ready']
    memory=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
    assert memory>64*GIB
    cfg=configuration(ROOT/'configs/platform-prettier6604-grpo.json')
    check, live=commands(cfg,cache)
    flags=['--schedule-smoke-metrics']
    membership=Path('/proc/self/cgroup').read_text().strip()
    assert membership.startswith('0::/')
    parent=Path('/sys/fs/cgroup')/Path(membership[3:]).parent.relative_to('/')
    assert parent.name.startswith('asbvmm') and parent.name.endswith('.slice'), 'must start in dedicated systemd slice'
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,
        ASB_VMM_CHILD_CGROUP_CHECK=str(out),ASB_VMM_CGROUP_PARENT=str(parent),
        PYTHONDONTWRITEBYTECODE='1',ASB_ISOLATED_UNUSED_NETWORK_POOLS='on'))
    value=dict(status='RUNNING',stage='preflight',start_monotonic_ns=time.monotonic_ns(),
               requested_runs=1,task='Prettier #6604 original seven roles',schedule='grpo')
    started=False
    def call(argv,label,timeout):
        with (out/(label+'.log')).open('x') as stream:
            p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        if p.returncode:raise RuntimeError(f'{label} exited {p.returncode}; {out/(label+".log")}')
    try:
        info=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert info['Id']==SOURCE_ID and not info['State']['Running'] and info['HostConfig']['RestartPolicy']['Name']=='no'
        value['source_before']=info['State']
        call(check+flags,'offline',60)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as r:
                    if r.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        value['stage']='single-grpo'
        (out/'run-result.json').write_text(json.dumps(value,indent=2)+'\n')
        call(live+flags,'grpo',1100)
        log=(out/'grpo.log').read_text()
        stores=[Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',log)
                if Path(p).parent==ROOT/'.artifacts/storage-isolated-service']
        assert len(stores)==1
        storage=stores[0];raw=storage/'prettier6604'
        task=json.loads((raw/'result.json').read_text());outer=json.loads((storage/'result.json').read_text())
        metrics=json.loads((raw/'schedule-smoke-metrics.json').read_text())
        assert outer['status']==task['status']=='PASS' and metrics['status']=='OBSERVED'
        assert task['actual_action_count']==189 and len(task['paths'])==7
        assert all(p['official_passed']==0 and p['official_failed']==2 for p in task['paths'].values())
        assert len(metrics['vmm_child_cgroups']['children'])==7 and metrics['p1']['unique_files']==19
        assert all(f['mincore_permission'] for f in metrics['p1']['files'])
        assert outer['vmm_cgroup_sampling']['status']=='PASS'
        value.update(status='PASS',stage='complete',storage=str(storage),raw=str(raw))
    except BaseException as error:
        value.update(status='FAIL',error=repr(error))
        (out/'run-failure.txt').write_text(traceback.format_exc())
        if (out/'grpo.log').exists():
            value['reported_paths']=re.findall(r'result_dir=(\S+)',(out/'grpo.log').read_text())
    finally:
        try:
            released=[]
            for group in sorted(parent.glob('vmm-*')):
                assert not (group/'cgroup.procs').read_text().strip(), 'VMM still alive: '+str(group)
                group.rmdir();released.append(str(group))
            value['empty_vmm_groups_removed']=released
        except Exception as error:value.update(status='FAIL',vmm_group_cleanup_error=repr(error))
        if started:
            try:
                call(['docker','stop','-t','30',SOURCE],'source-stop',60)
                assert not json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]['State']['Running']
                value['source_returned_to_stopped']=True
            except Exception as error:value.update(status='FAIL',source_cleanup_error=repr(error))
        try:
            call([sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release',90)
            value['cache_released']=json.loads((cache/'resources.json').read_text())['released']
        except Exception as error:value.update(status='FAIL',cache_cleanup_error=repr(error))
        value['end_monotonic_ns']=time.monotonic_ns()
        (out/'run-result.json').write_text(json.dumps(value,indent=2)+'\n')
        print('status='+value['status']+' result_dir='+str(out),flush=True)
    return 0 if value['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.output.resolve(),a.cache.resolve()))
