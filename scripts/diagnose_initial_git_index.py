"""Inspect and switch Git index snapshots in two owned guests; no trajectory replay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from guest_diskstats import parse,delta
from git_step_diagnostic import add_performance_records,add_trace2_records

ROOT=Path(__file__).resolve().parents[1]
MODES=('damon_disabled','default')
ORDER=('original','updated','updated','original')
STAT_ORDER=('original','refreshed','refreshed','original')
DEBUG=re.compile(rb'  ctime: (\d+):(\d+)\n  mtime: (\d+):(\d+)\n  dev: (\d+)\tino: (\d+)\n  uid: (\d+)\tgid: (\d+)\n  size: (\d+)\tflags: ([0-9a-fA-F]+)\n')
FIELDS=('ctime_sec','ctime_nsec','mtime_sec','mtime_nsec','dev','ino','uid','gid','size','flags')


def index_debug(data):
    result={};pos=0
    while pos<len(data):
        end=data.index(b'\0',pos);name=os.fsdecode(data[pos:end]);pos=end+1
        assert name and not Path(name).is_absolute() and not any(p in ('.','..','.git') for p in name.split('/'))
        match=DEBUG.match(data,pos);assert match,'unsupported Git debug format'
        assert name not in result
        values=[int(v,16 if i==9 else 10) for i,v in enumerate(match.groups())]
        result[name]=dict(zip(FIELDS,values));pos=match.end()
    assert result
    return result


def summarize_stat(entries,actuals):
    assert set(entries)==set(actuals)
    counts={k:0 for k in FIELDS[:-1]};changed=0;total=0;examples=[]
    for name,old in entries.items():
        values={k:actuals[name][k] for k in counts}
        differences=[k for k in counts if old[k]!=values[k]]
        for k in differences:counts[k]+=1
        if differences:
            changed+=1;total+=actuals[name]['size_bytes']
            if len(examples)<4:examples.append(dict(path=name,index=old,actual=values,differences=differences))
    return dict(entries=len(entries),entries_with_stat_difference=changed,
                differing_fields=counts,stat_different_file_bytes=total,
                nonzero_flags=sum(v['flags']!=0 for v in entries.values()),examples=examples)


def stat_summary(repo,entries,output=None):
    actuals={}
    for name in entries:
        st=(repo/name).lstat()
        values=dict(ctime_sec=st.st_ctime_ns//10**9,ctime_nsec=st.st_ctime_ns%10**9,
                    mtime_sec=st.st_mtime_ns//10**9,mtime_nsec=st.st_mtime_ns%10**9,
                    dev=st.st_dev,ino=st.st_ino,uid=st.st_uid,gid=st.st_gid,size=st.st_size)
        actuals[name]=dict({k:v&0xffffffff for k,v in values.items()},size_bytes=st.st_size)
    if output:output.write_text(json.dumps(actuals)+'\n')
    return summarize_stat(entries,actuals)


def git(repo,*args):
    p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,timeout=20)
    p.check_returncode();return p.stdout


def inspect(repo,out,label):
    raw=git(repo,'ls-files','--debug','-z');(out/f'{label}-debug.bin').write_bytes(raw)
    stage=git(repo,'ls-files','--stage','-z');(out/f'{label}-stage.bin').write_bytes(stage)
    indexed={}
    for record in stage.split(b'\0')[:-1]:
        meta,name=record.split(b'\t',1);mode,oid,number=meta.split();assert number==b'0'
        indexed[os.fsdecode(name)]=dict(mode=mode.decode(),oid=oid.decode())
    entries=index_debug(raw);assert set(entries)==set(indexed)
    return dict(stat=stat_summary(repo,entries,out/f'{label}-actual-stat.json'),objects=indexed)


def guest(out,repo,stat_only=False):
    out.mkdir();index=repo/'.git/index';initial=index.read_bytes();initial_stat=index.stat()
    (out/'original.index').write_bytes(initial)
    initial_info=inspect(repo,out,'original')
    settings={}
    for key in ('core.trustctime','core.checkStat','core.filemode','core.ignorestat','core.fsmonitor','core.untrackedCache'):
        p=subprocess.run(['git','-C',str(repo),'config','--get',key],capture_output=True,timeout=5)
        assert p.returncode in (0,1);settings[key]=p.stdout.decode().strip() if p.returncode==0 else None
    result=dict(status='FAIL',stat_only=stat_only,git_version=git(repo,'--version').decode().strip(),config=settings,
                original_head=git(repo,'rev-parse','HEAD').decode().strip(),initial=initial_info['stat'],runs=[])
    snapshots={'original':(initial,initial_stat.st_atime_ns,initial_stat.st_mtime_ns)}
    for number,label in enumerate(('first',)+(STAT_ORDER if stat_only else ORDER)):
        if label!='first':
            data,atime,mtime=snapshots[label];index.write_bytes(data);os.utime(index,ns=(atime,mtime))
        directory=out/f'{number}-{label}';directory.mkdir()
        before=Path('/proc/diskstats').read_text();(directory/'before.diskstats').write_text(before)
        env=dict(os.environ,GIT_TRACE_PERFORMANCE=str(directory/'performance.txt'),GIT_TRACE2_EVENT=str(directory/'events.jsonl'))
        p=subprocess.run([str(out.parent/'command-clock'),str(directory/'clock.json'),'git','-C',str(repo),'add','-A'],env=env,capture_output=True,timeout=20)
        after=Path('/proc/diskstats').read_text();(directory/'after.diskstats').write_text(after)
        (directory/'stdout').write_bytes(p.stdout);(directory/'stderr').write_bytes(p.stderr)
        p.check_returncode();assert not p.stdout and not p.stderr
        argv=['git','-C',str(repo),'add','-A'];perf=add_performance_records((directory/'performance.txt').read_text(),argv)
        trace=add_trace2_records((directory/'events.jsonl').read_text(),perf,argv)
        clock=json.loads((directory/'clock.json').read_text());assert clock['exit_code']==0 and abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<2_000_000
        tree=git(repo,'write-tree').decode().strip()
        result['runs'].append(dict(position=number,index_source=label,tree=tree,clock=clock,performance=perf,trace2=trace,diskstats=delta(parse(before),parse(after))))
        if label=='first':
            data=index.read_bytes();st=index.stat();snapshots['updated']=(data,st.st_atime_ns,st.st_mtime_ns)
            (out/'updated.index').write_bytes(data)
            updated=inspect(repo,out,'updated');result['updated']=updated['stat']
            old=initial_info['objects'];new=updated['objects'];common=set(old)&set(new)
            result['object_changes']=dict(added=len(set(new)-set(old)),removed=len(set(old)-set(new)),
                changed=sum(old[k]!=new[k] for k in common),unchanged=sum(old[k]==new[k] for k in common))
            if stat_only:
                index.write_bytes(initial);os.utime(index,ns=(initial_stat.st_atime_ns,initial_stat.st_mtime_ns))
                p=subprocess.run([str(out.parent/'command-clock'),str(out/'refresh-clock.json'),
                                  'git','-C',str(repo),'update-index','--refresh'],capture_output=True,timeout=20)
                (out/'refresh.stdout').write_bytes(p.stdout);(out/'refresh.stderr').write_bytes(p.stderr)
                assert p.returncode in (0,1) and not p.stderr
                refreshed=inspect(repo,out,'refreshed');assert refreshed['objects']==old
                clock=json.loads((out/'refresh-clock.json').read_text())
                assert clock['exit_code']==p.returncode and abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<2_000_000
                result['refresh']=dict(exit_code=p.returncode,clock=clock,stat=refreshed['stat'],staged_objects_equal_original=True)
                data=index.read_bytes();st=index.stat();snapshots['refreshed']=(data,st.st_atime_ns,st.st_mtime_ns)
                (out/'refreshed.index').write_bytes(data)
                (out/'refresh-exit-code.txt').write_text(str(p.returncode)+'\n')
    assert len({r['tree'] for r in result['runs']})==1
    result['status']='PASS';(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')


def host(stat_only=False):
    from cpu_frequency_lease import save
    from diagnose_exp1_cpu_variance import cgroup
    from experiment_disk_guard import writer_lock,guarded_run
    os.umask(0o077);out=ROOT/('.artifacts/initial-git-index-stat-only' if stat_only else '.artifacts/initial-git-index')/ (time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}');out.mkdir(parents=True)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(ROOT/'runtime/config'))
    def call(label,argv,timeout=30,cleanup=False):
        with (out/f'{label}.stdout').open('w') as stdout,(out/f'{label}.stderr').open('w') as stderr:
            guarded_run(argv,directory=out,log=stdout,stderr_log=stderr,timeout=timeout,env=env,reserve=0 if cleanup else 20*1024**3)
        return (out/f'{label}.stdout').read_text().strip()
    result=dict(status='FAIL',guests=[]);print(f'status=RUNNING result_dir={out}',flush=True)
    with writer_lock():
        try:
            assert shutil.disk_usage(ROOT).free>32*1024**3
            assert json.loads(call('list-before',['aenv','list']))==[]
            tools=out/'tools';tools.mkdir()
            for name in ('diagnose_initial_git_index.py','guest_diskstats.py','git_step_diagnostic.py','command_clock.c','guest_damon_control.py'):
                shutil.copy2(ROOT/'scripts'/name,tools/name)
            call('build',['gcc','-O2','-static','-Wall','-Wextra','-Werror',str(tools/'command_clock.c'),'-o',str(tools/'command-clock')])
            save(out/'inputs.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tools.iterdir()})
            image=json.loads((ROOT/'configs/exp1-monitor-calibration.json').read_text())['workload_image'];result['image']=image
            save(out/'protocol.json',dict(stat_only=stat_only,full_stat_rows=True,modes=MODES,index_order=['first',*(STAT_ORDER if stat_only else ORDER)],maximum_guests=2,periodic_monitoring=False,
                cache_policy='no flush; metadata inspection before timing and deliberate first add warms contents; repeated trials restore index bytes and original snapshot mtime',
                scope='intervention diagnosis only; no natural replay or monitoring overhead acceptance'))
            cfg=json.loads((ROOT/'configs/exp1-agentenv.json').read_text());cid=(ROOT/cfg['server_result']/'container-id.txt').read_text().strip()
            group=cgroup(call('service-pid',['docker','inspect','--format','{{.State.Pid}}',cid]))
            for mode in MODES:
                sid=None;helper=None;log=None;row=dict(mode=mode,status='FAIL',cleanup='NOT_NEEDED');result['guests'].append(row)
                try:
                    sid=call(mode+'-create',['aenv','start','--cold',image,'--detach','--timeout','300','--cpu','2','--memory','4096'],90);row['sandbox_id']=sid
                    call(mode+'-prepare',['python3',str(tools/'guest_damon_control.py'),sid,mode,'prepare',str(out/f'{mode}-prepare.json')])
                    call(mode+'-upload',['aenv','upload',sid,str(tools),'/workspace/index-diagnosis'],45)
                    call(mode+'-chmod',['aenv','exec',sid,'chmod','+x','/workspace/index-diagnosis/command-clock'])
                    log=(out/f'{mode}-frequency.log').open('w');freq=out/f'{mode}-frequency'
                    helper=subprocess.Popen([sys.executable,str(ROOT/'scripts/fixed_frequency_window.py'),sid,str(freq),str(ROOT/'runtime'),group],env=env,stdout=log,stderr=subprocess.STDOUT)
                    deadline=time.monotonic()+45
                    while not (freq/'ready.json').exists():
                        assert helper.poll() is None and time.monotonic()<deadline;time.sleep(.05)
                    call(mode+'-before',['python3',str(tools/'guest_damon_control.py'),sid,mode,'before_task',str(out/f'{mode}-before.json')])
                    call(mode+'-probe',['aenv','exec',sid,'python3','/workspace/index-diagnosis/diagnose_initial_git_index.py','--guest','/workspace/index-diagnosis/results']+(['--stat-only'] if stat_only else []),90)
                    call(mode+'-after',['python3',str(tools/'guest_damon_control.py'),sid,mode,'after_task',str(out/f'{mode}-after.json')])
                    call(mode+'-download',['aenv','download',sid,'/workspace/index-diagnosis/results',str(out/mode)],45)
                    row['measurement']=json.loads((out/mode/'result.json').read_text());assert row['measurement']['status']=='PASS';row['status']='PASS'
                finally:
                    if helper:
                        try:
                            (freq/'stop').touch()
                            try:helper.wait(timeout=25)
                            except subprocess.TimeoutExpired:helper.terminate();helper.wait(timeout=20)
                            row['frequency']=json.loads((freq/'result.json').read_text())
                        except Exception as exc:row.update(status='FAIL',frequency_error=repr(exc))
                    if log:log.close()
                    if sid:
                        try:
                            call(mode+'-delete',['aenv','delete',sid],90,cleanup=True)
                            assert not any(x['sandboxID']==sid for x in json.loads(call(mode+'-list-after',['aenv','list'],cleanup=True)));row['cleanup']='PASS'
                        except Exception as exc:row.update(status='FAIL',cleanup='FAIL',cleanup_error=repr(exc))
                    save(out/'progress.json',result)
                assert row['status']==row['cleanup']==row['frequency']['status']==row['frequency']['restoration']['status']=='PASS'
                print(f'mode={mode} status=PASS result_dir={out}',flush=True)
            result['status']='PASS'
        except Exception as exc:result['error']=repr(exc)
        finally:result['free_bytes_after']=shutil.disk_usage(ROOT).free;save(out/'result.json',result)
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--guest',type=Path);p.add_argument('--repo',type=Path,default=Path('/testbed'));p.add_argument('--stat-only',action='store_true');args=p.parse_args()
    if args.guest:guest(args.guest,args.repo,args.stat_only)
    else:raise SystemExit(host(args.stat_only))
