"""Two C4 controllers in one owned service; each child owns only its guest."""
import argparse,json,os,shutil,subprocess,sys,time,traceback
from pathlib import Path
from external_workload import C4
from external_task_metrics import validate_result
from host_sandbox_monitor import validate_pss_interval
ROOT=Path(__file__).resolve().parents[1]

def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def validate_parallel(value,sampling,*,pss_interval_seconds=None):
    assert value['status']=='PASS' and value['actions']==72 and len(value['children'])==2
    children=value['children'];assert len({c['sandbox_id'] for c in children})==2
    for c in children:
        assert c['status']=='PASS' and c['task_id']==C4 and c['actions']==36 and c['strict_exit_match']
        assert c['roundtrip_validation']=='PASS' and c['syntax_validation']=='PASS'
        assert c['cleanup']['sandbox_removed']
        if sampling=='on':validate_result(c,pss_interval_seconds=pss_interval_seconds)
        else:assert c['memory']=='NOT_MEASURED' and 'endpoint_metrics' not in c
    assert value['replay_overlap_ns']>0
    assert value['cleanup']['service_sandbox_list_empty']

def run(args):
    pss_interval=validate_pss_interval(getattr(args,'vmm_pss_interval_seconds',1))
    if pss_interval!=1 and not (args.endpoint_metrics and args.sampling=='on'):
        raise ValueError('non-default PSS interval requires sampled endpoint metrics')
    out=args.output.resolve();out.mkdir();begin=time.monotonic_ns();state=dict(status='FAIL',task_id=C4,actions=72,sampling=args.sampling,children=[])
    env=dict(os.environ,XDG_CONFIG_HOME=str(args.config_home));children=[]
    def cli(*argv):return subprocess.check_output([str(ROOT/'runtime/bin/aenv'),*argv],env=env,text=True,timeout=90).strip()
    try:
        assert args.sampling_control
        assert json.loads(cli('list','--output','json'))==[]
        state['schedule_start_monotonic_ns']=time.monotonic_ns()
        for i in range(2):
            target=out.parent/f'c4-parallel-{i}'
            command=[sys.executable,'-B',str(ROOT/'scripts/run_external_workload.py'),'--config',str(args.config),'--upstream',str(args.upstream),'--output',str(target),'--config-home',str(args.config_home),'--service-url',args.service_url,'--container',args.container,'--sampling',args.sampling,'--sampling-control','--concurrent-c4']
            if args.endpoint_metrics:command+=['--endpoint-metrics']
            if pss_interval!=1:command+=['--vmm-pss-interval-seconds',str(pss_interval)]
            log=(out/f'guest-{i}.log').open('x');process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
            children.append((process,log,target));write(out/f'guest-{i}-command.json',command)
        deadline=time.monotonic()+1000
        for process,_,_ in children:
            status=process.wait(timeout=max(1,deadline-time.monotonic()));assert status==0,'C4 child failed; see retained child log'
        values=[read(target/'result.json') for _,_,target in children];state['children']=values
        state['schedule_end_monotonic_ns']=max(v['schedule_end_monotonic_ns'] for v in values)
        state['schedule_elapsed_ns']=state['schedule_end_monotonic_ns']-state['schedule_start_monotonic_ns']
        state['actions_download_elapsed_ns']=state['schedule_end_monotonic_ns']-min(v['replay_start_monotonic_ns'] for v in values)
        state['replay_overlap_ns']=min(v['replay_end_monotonic_ns'] for v in values)-max(v['replay_start_monotonic_ns'] for v in values)
        state['tool_duration_sum_ns']=sum(v['tool_duration_sum_ns'] for v in values)
        state.update(strict_exit_match=all(v['strict_exit_match'] for v in values),roundtrip_validation='PASS',syntax_validation='PASS',functional_validation='PASS',status='PASS')
        if args.sampling=='off':state['memory']='NOT_MEASURED'
    except BaseException as e:state.update(status='FAIL',error=repr(e));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        for process,log,target in children:
            if process.poll() is None:
                try:process.wait(timeout=60)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=15)
            log.close()
            # Preserve each child, including failure evidence, before the native mount is removed.
            if target.exists():shutil.copytree(target,out/target.name)
        try:
            guests=json.loads(cli('list','--output','json'))
            for guest in guests:
                # All guests belong to this newly checked-empty private service.
                sid=guest.get('sandboxID',guest.get('sandbox_id',guest.get('id'))) if isinstance(guest,dict) else guest
                cli('delete',sid)
            assert json.loads(cli('list','--output','json'))==[]
            cleanup.update(sandbox_removed=True,service_sandbox_list_empty=True)
            if args.sampling=='off':cleanup['memory_sampling_disabled']=True
        except Exception as e:state.update(status='FAIL',cleanup_error=repr(e))
        state['cleanup']=cleanup;state['controller_elapsed_ns']=time.monotonic_ns()-begin
        if state['status']=='PASS':
            try:validate_parallel(state,args.sampling,pss_interval_seconds=pss_interval if args.endpoint_metrics else None)
            except Exception as e:state.update(status='FAIL',validation_error=repr(e))
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('config','upstream','output','config-home'):p.add_argument('--'+key,type=Path,required=True)
    for key in ('service-url','container'):p.add_argument('--'+key,required=True)
    p.add_argument('--sampling',choices=('on','off'),default='on');p.add_argument('--sampling-control',action='store_true');p.add_argument('--endpoint-metrics',action='store_true')
    p.add_argument('--vmm-pss-interval-seconds',type=validate_pss_interval,default=1)
    raise SystemExit(run(p.parse_args()))
