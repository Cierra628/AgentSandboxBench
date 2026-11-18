"""Five fixed Prettier prefix sequences; one fresh group/service per repetition."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

from platform_tasks import ROOT,configuration,preflight
from isolated_storage import available,capacity_budget,GIB
from run_light_monitor_controls import SOURCE,SOURCE_ID
from report_host_shared_repeats import collect,report


def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def source_state():return json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_host_shared_repeats.py','report_host_shared_repeats.py','run_host_scaling.py',
                 'host_scaling.py','host_file_monitor.py','host_prefix_compare.py','run_host_file_workflow.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    os.environ['ASB_AGENTENV_SOURCE_SERVICE']=SOURCE
    state=dict(status='RUNNING',stage='precheck',planned=5,runs=[],attempts=[],whole_physical_savings='OUT_OF_SCOPE')
    started=False
    try:
        config=ROOT/'configs/platform-prettier6604-host-file.json'
        write(out/'preflight.json',preflight(configuration(config),offline=True))
        source=source_state()
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        assert capacity_budget(available(ROOT),16*GIB)['ready'],'insufficient disk budget before source start'
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        for index in range(1,6):
            state['stage']='repeat-'+str(index)
            budget=capacity_budget(available(ROOT),16*GIB)
            assert budget['ready'],'insufficient disk budget; no automatic cleanup of failed volumes'
            attempt=dict(repeat=index,status='RUNNING',disk_budget=budget,load=os.getloadavg())
            state['attempts'].append(attempt);write(out/'result.json',state)
            print('status=RUNNING repeat='+str(index)+'/5 result_dir='+str(out),flush=True)
            with (out/f'repeat-{index}.log').open('x') as log:
                proc=subprocess.run(['bash','scripts/run-host-file-monitor.sh',str(config),'--scaling','cold'],
                    cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1700)
            attempt['returncode']=proc.returncode
            if proc.returncode:
                attempt['status']='FAIL';raise RuntimeError('repeat failed; diagnose first boundary before more guests')
            matches={Path(v) for v in re.findall(r'status=PASS result_dir=(\S+)',(out/f'repeat-{index}.log').read_text())
                if Path(v).parent==ROOT/'.artifacts/host-scaling'}
            assert len(matches)==1,'missing or ambiguous scaling workflow result'
            workflow=matches.pop();attempt['workflow']=str(workflow)
            run=collect(workflow);assert all(r['summary']['baseline']['group']!=run['summary']['baseline']['group'] for r in state['runs'])
            state['runs'].append(run);attempt['status']='PASS';write(out/'result.json',state)
        state['summary_status']=report(state['runs'],out/'report')['status']
        assert state['summary_status']=='PASS_FIVE_REPEATS'
        state.update(status='PASS',stage='complete')
    except BaseException as error:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());state.update(status='FAIL',error=repr(error))
        if state['attempts'] and state['attempts'][-1]['status']=='RUNNING':state['attempts'][-1]['status']='FAIL'
    finally:
        if started:
            with (out/'source-stop.log').open('x') as log:
                stop=subprocess.run(['docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT,timeout=60)
            after=source_state();write(out/'source-after.json',after['State'])
            state['source_returned_to_stopped']=stop.returncode==0 and not after['State']['Running'] and after['Id']==SOURCE_ID
            if not state['source_returned_to_stopped']:state['status']='FAIL'
        write(out/'result.json',state)
        print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
