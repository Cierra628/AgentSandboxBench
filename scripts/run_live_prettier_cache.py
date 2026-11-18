"""Run one complete frozen Prettier path with private no-pause cache collection."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback

from platform_tasks import ROOT, configuration, commands


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kernel',type=Path,required=True)
    parser.add_argument('--cache-sampling',choices=('on','off'),default='on',help='off retains existing lightweight monitoring as a control')
    args=parser.parse_args();out=args.output.resolve();kernel=args.kernel.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    assert kernel.is_relative_to(ROOT/'.artifacts') and kernel.is_file()
    os.umask(0o077);out.mkdir(parents=True)
    cfg=configuration(ROOT/'configs/platform-prettier6604.json')
    cache=ROOT/'.artifacts/prettier6604'/('live-cache-'+out.name)
    result=dict(status='FAIL',vm_pause=False,workflow='whole-guest-cache-during-frozen-task',cache_sampling=args.cache_sampling)
    stage='inputs'
    def call(label,argv,timeout):
        print('status=RUNNING stage='+label+' result_dir='+str(out),flush=True)
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    try:
        check,live=commands(cfg,cache)
        call(stage,check,60)
        stage='cache-prepare'
        call(stage,[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        stage='isolated-runtime'
        live+=['--diagnostic-guest-kernel',kernel]
        if args.cache_sampling=='on':live+=['--prettier6604-live-cache']
        call(stage,live,1100)
        children=[Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(stage+'.log')).read_text())
                  if Path(p).parent==ROOT/'.artifacts/storage-isolated-service']
        assert len(set(children))==1
        child=children[0]
        result['storage_result_dir']=str(child)
        if args.cache_sampling=='on':
            stage='cache-report'
            call(stage,[sys.executable,'-B',ROOT/'scripts/report_live_task_cache.py',child/'prettier6604','--output',out/'report'],120)
        result['status']='PASS'
    except BaseException as exc:
        result.update(error=repr(exc),failed_stage=stage)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if (cache/'resources.json').exists():
            try:
                call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                result['cache_released']=json.loads((cache/'resources.json').read_text())['released']
            except Exception as exc:result.update(status='FAIL',cache_release_error=repr(exc))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
