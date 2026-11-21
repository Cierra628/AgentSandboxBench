"""Five fresh sets of workers and guests. Only the saved source is reused."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from run_seven_paused_serial import ROOT, CONTENT, WRITE, create_source, save, clean_environment, main


def series():
    p=argparse.ArgumentParser(add_help=False)
    for k in ('config-home','service-url','output','workers'):
        p.add_argument('--'+k,required=True)
    args,_=p.parse_known_args();assert args.workers=='7'
    output=Path(args.output);output.mkdir()
    env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=args.config_home))
    def cli(*a):
        return subprocess.check_output([ROOT/'runtime/bin/aenv',*a],env=env,text=True,timeout=150).strip()
    strict=os.environ.get("ASB_REPAIRED_FIVE")=="1"
    envdir=output.parent.parent/"env"
    def empty():
        if json.loads(cli('list','--output','json'))!=[]:raise RuntimeError('remaining sandboxes')
        if strict and any((envdir/'provider-slots').glob('*')):
            raise RuntimeError('provider slots left after release')
        root=Path(os.environ['ASB_VMM_CGROUP_PARENT'])
        control=next(root.glob('*control.service'))
        for pid in (control/'cgroup.procs').read_text().split():
            try:name=(Path('/proc')/pid/'comm').read_text().strip()
            except FileNotFoundError:continue
            if name=='bpftrace':raise RuntimeError('remaining owned probe '+pid)
        assert all(not (g/'cgroup.procs').read_text().strip() for g in root.glob('vmm-*'))
    phase=os.environ.get('ASB_FIRST_FRAME_STUDY')
    count={'diagnose':1,None:5}[phase]
    result=dict(status='FAIL',scope='five fresh restore cohorts, one immutable snapshot',attempts=[])
    try:
        with ExitStack() as held:
            source=create_source(Path(args.config_home),args.service_url)
            cleanup=ExitStack();held.enter_context(cleanup);cleanup.callback(cli,'delete',source)
            cli('exec',source,'true')
            cli('exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
            snap='seven-paused-'+output.parents[2].name
            cli('snapshot','create',source,'--name',snap)
            held.callback(cli,'template','delete',snap);cleanup.close()
            result.update(source=source,snapshot=snap)
            empty()
            for n in range(1,count+1):
                if strict: pos=(envdir/"logs/ublk-daemon.log").stat().st_size
                child=output.with_name('seven-try-'+str(n))
                argv=list(sys.argv[1:]);argv[argv.index('--output')+1]=str(child)
                arg=[sys.executable,'-B',__file__,snap,source,*argv]
                with (output/f'try-{n}.log').open('x') as log:
                    run=subprocess.run(arg,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=310)
                child.rename(output/f'try-{n}')
                record=output/f'try-{n}/result.json'
                v=json.loads(record.read_text()) if record.exists() else {}
                row=dict(attempt=n,exit=run.returncode,status=v.get('status','FAIL'))
                result['attempts'].append(row)
                if run.returncode==0 and v.get('valid')==7 and v['status']=='PASS':
                    row['ns']=v['merge_end']-v['go']
                empty();row['cleanup']='PASS';save(output/'series.json',result)
                if strict:
                    with (envdir/'logs/ublk-daemon.log').open('rb') as f:
                        f.seek(pos); row['file_exists']=f.read().count(b'File exists')
                    low={r:q['pages'] for r,q in v.get('query_rows',{}).items() if q['pages']<30000}
                    if row['file_exists'] or 'ns' not in row or low:
                        result['stop']=dict(attempt=n,low_pages=low,file_exists=row['file_exists'],error=v.get('error'))
                        row['status']='STOP'
                        break
                elif n==2 and all(r.get('ns',0)>1_500_000_000 for r in result['attempts']):
                    result['stop']='both_first_two_over_1.5s';break
        result['status']='PASS' if (not strict or (len(result['attempts'])==count and not result.get('stop'))) and all('ns' in r for r in result['attempts']) else 'INCOMPLETE'
    except Exception:
        result['error']=traceback.format_exc()
    save(output/'result.json',result)
    return int(result['status']!='PASS')


if __name__=='__main__':
    if os.environ.get('ASB_FIRST_FRAME_STUDY')=='diagnose':
        import runpy
        runpy.run_path(str(ROOT/'docs/records/first-frame-opt-20261121/profile_build.py'))['install']()
    snap,source=sys.argv[1:3];del sys.argv[1:3]
    raise SystemExit(main(process_mode=True,fixed_snapshot=(snap,source)))
