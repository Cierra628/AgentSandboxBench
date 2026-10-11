"""Continue the predeclared TrEnv-X groups without repeating completed data."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

from experiment_disk_guard import guarded_run
from report_trenvx_monitor_groups import report

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'configs/trenvx-monitor-balanced-plan.json'


def main():
    state = json.loads(PLAN.read_text())
    directories = [(ROOT/path).resolve(strict=True) for path in state['completed_groups']]
    maximum = state['maximum_groups']
    assert maximum == 5 and state['seed_base']==20261001 and 1 <= len(directories) < maximum, 'no unfinished declared groups'
    summary = report(directories)
    assert all(abs(value)<=5 for value in summary['analysis']['control']['group_percent']), 'unstable control'
    out = ROOT/'.artifacts/trenvx-monitor-followup'/(
        time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    for name in ('continue_trenvx_monitor_groups.py','report_trenvx_monitor_groups.py'):
        shutil.copy2(ROOT/'scripts'/name,out/name)
    result = dict(status='FAIL', completed_groups=state['completed_groups'])
    try:
        for group in range(len(directories)+1,maximum+1):
            argv=[sys.executable,str(ROOT/'scripts/run_trenvx_monitor_balanced.py'),
                  '--groups','1','--seed',str(state['seed_base']+group-1)]
            (out/f'group-{group}-command.json').write_text(json.dumps(argv)+'\n')
            with (out/f'group-{group}.log').open('w') as log:
                guarded_run(argv,directory=out,log=log,timeout=1200)
            found=re.findall(r'^status=PASS result_dir=(\S+)$',
                             (out/f'group-{group}.log').read_text(),re.MULTILINE)
            assert len(found)==1, 'missing successful group result'
            directory=Path(found[0]).resolve(strict=True)
            assert directory.parent==(ROOT/'.artifacts/trenvx-monitor-balanced').resolve(strict=True)
            directories.append(directory)
            summary=report(directories)
            state.update(status='RUNNING' if group<maximum else 'COMPLETED',
                         completed_groups=[str(path.relative_to(ROOT)) for path in directories])
            PLAN.write_text(json.dumps(state,indent=2)+'\n')
            (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
            print(f'status=RUNNING completed_groups={group} result_dir={out}',flush=True)
        result.update(status='PASS',completed_groups=state['completed_groups'],
                      verdict=summary['analysis']['verdict'])
    except BaseException as exc:
        state.update(status='STOPPED',error=repr(exc))
        PLAN.write_text(json.dumps(state,indent=2)+'\n')
        result['error']=repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__': sys.exit(main())
