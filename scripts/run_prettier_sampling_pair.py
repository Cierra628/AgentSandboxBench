"""One Prettier GRPO pair: all sampling off, then full sampling plus endpoint PSS/P1."""
import argparse,csv,json,os,re,shutil,subprocess,sys,time,traceback,urllib.request
from pathlib import Path
from platform_tasks import ROOT,configuration,commands
from prettier_no_proxy import clean_environment
from isolated_storage import available,capacity_budget,GIB
from run_light_monitor_controls import SOURCE,SOURCE_ID
from run_schedule_smoke import summarize


def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def inspect(storage,mode):
    raw=storage/'prettier6604';outer=read(storage/'result.json');task=read(raw/'result.json')
    assert outer['status']==task['status']=='PASS'
    assert len(task['paths'])==7 and task['actual_action_count']==189
    assert all(p['official_passed']==0 and p['official_failed']==2 for p in task['paths'].values())
    source_check='original_source_firecracker_identities_unchanged' if 'original_source_firecracker_identities_unchanged' in outer['cleanup'] else 'original_firecracker_identities_unchanged'
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged',source_check))
    if mode=='off':
        assert task['monitor_mode']=='off' and task['cleanup']['monitoring_disabled']
        assert task['schedule_smoke_metrics']['status']=='NOT_REQUESTED'
        assert not any((raw/p).exists() for p in ('runtime-monitor.json','host-vmm','host/memory-samples.tsv','schedule-smoke-metrics.json'))
        for p in (raw/'rollouts').glob('*/*/guest-*.tsv'):
            with p.open() as f:assert not list(csv.DictReader(f,delimiter='\t')),p
    else:summarize(storage)
    return dict(mode=mode,status='PASS',storage=str(storage),raw=str(raw),actual_actions=189,
        task_seconds=(task['original_paths_end_monotonic_ns']-task['rollout_start_monotonic_ns'])/1e9,
        actions_download_seconds=(task['original_paths_end_monotonic_ns']-task['first_tool_start_monotonic_ns'])/1e9,
        tool_seconds=sum(p['guest_action_sum_ns'] for p in task['paths'].values())/1e9,
        controller_seconds=task['controller_elapsed_ns']/1e9,
        endpoint=task['schedule_smoke_metrics'],minimum_disk_gib=outer['observed_host_available_min_bytes']/GIB)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();out.mkdir(parents=True)
    (out/'code').mkdir()
    for name in ('run_prettier_sampling_pair.py','schedule_smoke_metrics.py','run_prettier6604_grpo.py','validate_isolated_agentenv.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,PYTHONDONTWRITEBYTECODE='1'));env.pop('ASB_LOCAL_NO_PROXY',None);env.pop('ASB_LOCAL_NO_PROXY_CHECK',None)
    cache=ROOT/'.artifacts/prettier6604'/('sampling-pair-cache-'+out.name)
    state=dict(status='RUNNING',runs=[],order=['off','on'],repeats_per_condition=1);started=False
    def call(argv,label,timeout):
        with (out/(label+'.log')).open('x') as log:p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        if p.returncode:raise RuntimeError(label+' exit '+str(p.returncode)+'; inspect '+str(out/(label+'.log')))
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0];assert source['Id']==SOURCE_ID and not source['State']['Running']
        configs={m:configuration(ROOT/('configs/platform-prettier6604-grpo'+('-off' if m=='off' else '')+'.json')) for m in ('off','on')}
        flags=['--schedule-smoke-metrics','--schedule-sampling-control']
        for mode,cfg in configs.items():check,_=commands(cfg,cache);call(check+flags,mode+'-offline',60)
        assert capacity_budget(available(ROOT),32*GIB)['ready'];write(out/'plan.json',dict(configs=configs,extra_flags=flags,guest_count=7,actions=189,cache_reset=False,proxy=False))
        call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],'cache-prepare',300)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health')
        for mode,cfg in configs.items():
            assert capacity_budget(available(ROOT),16*GIB)['ready'];state['stage']=mode;write(out/'result.json',state)
            _,live=commands(cfg,cache);print('status=RUNNING mode='+mode+' result_dir='+str(out),flush=True)
            call(live+flags,mode,1100)
            paths=re.findall(r'status=PASS result_dir=(\S+)',(out/(mode+'.log')).read_text());storage=[Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'];assert len(storage)==1
            state['runs'].append(inspect(storage[0],mode));write(out/'result.json',state)
        off,on=state['runs']
        for role in read(Path(off['raw'])/'result.json')['paths']:
            a=Path(off['raw'])/'rollouts'/role;b=Path(on['raw'])/'rollouts'/role
            for p in a.glob('*/patch.diff'):assert p.read_bytes()==(b/p.relative_to(a)).read_bytes(),str(p)
        state['changes']={k:dict(off=off[k],on=on[k],delta_seconds=on[k]-off[k],change_percent=(on[k]/off[k]-1)*100) for k in ('tool_seconds','task_seconds','actions_download_seconds','controller_seconds')}
        state.update(status='PASS',stage='complete',patches_match=True)
    except BaseException as e:state.update(status='FAIL',error=repr(e));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                call(['docker','stop','-t','30',SOURCE],'source-stop',60);assert not read_state();state['source_stopped']=True
            except Exception as e:state.update(status='FAIL',source_cleanup_error=repr(e))
        if (cache/'resources.json').exists():
            try:call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release',90);state['cache_released']=read(cache/'resources.json')['released']
            except Exception as e:state.update(status='FAIL',cache_cleanup_error=repr(e))
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def read_state():return json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State.Running}}',SOURCE],timeout=15))
if __name__=='__main__':raise SystemExit(main())
