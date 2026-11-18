"""C4: three alternating on/off pairs for the explicit endpoint-metrics profile."""
import argparse,csv,json,os,re,shutil,statistics,subprocess,sys,time,traceback,urllib.request
from pathlib import Path
from platform_tasks import ROOT
from isolated_storage import available,capacity_budget,GIB
from prettier_no_proxy import clean_environment
from external_workload import C4,load,validate_timings,validate_c4_outputs
from external_oci import validate as validate_oci
from external_task_metrics import validate_result,SNAPSHOT_FILES
from run_light_monitor_controls import SOURCE,SOURCE_ID
ORDER=((1,'off'),(1,'on'),(2,'on'),(2,'off'),(3,'off'),(3,'on'))


def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def table(p):
    with Path(p).open() as f:return list(csv.DictReader(f,delimiter='\t'))


def inspect(workflow,mode,expected):
    w=read(workflow/'result.json');storage=Path(w['storage']);raw=Path(w['raw']);t=read(raw/'result.json');outer=read(storage/'result.json')
    assert w['status']==t['status']==outer['status']=='PASS' and t['task_id']==C4 and t['actions']==36 and t['sampling']==mode
    assert t['roundtrip_validation']=='PASS' and t['syntax_validation']=='PASS' and t['strict_exit_match']
    validate_c4_outputs(raw/'input/logs');assert int((raw/'input/logs/002.stdout').read_text().splitlines()[0])==9898
    tools=table(raw/'tools.tsv');validate_timings(tools);assert len(tools)==36 and [int(r['exit_code']) for r in tools]==[int(r['exit_code']) for r in expected]
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    assert t['cleanup']['sandbox_removed'] and t['cleanup']['service_sandbox_list_empty']
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
    if mode=='on':
        validate_result(t);assert t['endpoint_metrics']['p1']['unique_files']==9
        monitor=read(raw/'runtime-monitor.json');assert monitor['profile']=='light' and monitor['interval_seconds']==.1
        assert w['collector']['status']=='PASS' and w['collector']['interval_seconds']==.1
        assert w['collector']['pss_interval_seconds'] is None
    else:
        assert t['memory']=='NOT_MEASURED' and t['cleanup']['memory_sampling_disabled']
        assert not any((raw/p).exists() for p in ('runtime-monitor.json','host-vmm','external-metrics.json')) and not (workflow/'host-file').exists()
    events=read(raw/'events.json');cold=next(e for e in events if e['stage']=='cold-create')
    return dict(mode=mode,workflow=str(workflow),storage=str(storage),raw=str(raw),sandbox_id=t['sandbox_id'],
        timings=dict(tools=t['tool_duration_sum_ns']/1e9,task=t['schedule_elapsed_ns']/1e9,actions_download=t['actions_download_elapsed_ns']/1e9,controller=t['controller_elapsed_ns']/1e9,cold_create=cold['command_duration_ns']/1e9),
        min_available_disk_gib=outer['observed_host_available_min_bytes']/GIB,
        endpoint=t.get('endpoint_metrics'),vmm_collector=t.get('host_vmm_collector'),host_collector=w.get('collector'),published_report=w.get('published_report'))


def comparison(out):
    runs=read(out/'runs.json');assert [(r['pair'],r['mode']) for r in runs]==list(ORDER)
    assert len({r['sandbox_id'] for r in runs})==6
    pairs=[]
    for number in (1,2,3):
        selected=[r for r in runs if r['pair']==number];a=next(r for r in selected if r['mode']=='on');b=next(r for r in selected if r['mode']=='off')
        pairs.append(dict(pair=number,order=[r['mode'] for r in selected],metrics={k:dict(on=a['timings'][k],off=b['timings'][k],delta_seconds=a['timings'][k]-b['timings'][k],change_percent=(a['timings'][k]/b['timings'][k]-1)*100) for k in a['timings']}))
    stats={k:dict(on_median_seconds=statistics.median(p['metrics'][k]['on'] for p in pairs),off_median_seconds=statistics.median(p['metrics'][k]['off'] for p in pairs),paired_change_median_percent=statistics.median(p['metrics'][k]['change_percent'] for p in pairs),paired_change_range_percent=[min(p['metrics'][k]['change_percent'] for p in pairs),max(p['metrics'][k]['change_percent'] for p in pairs)]) for k in runs[0]['timings']}
    write(out/'comparison.json',dict(status='PASS',runs=6,pairs=pairs,summary=stats));return stats


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--oci-input',type=Path,required=True);args=p.parse_args();out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();out.mkdir(parents=True);(out/'code').mkdir();started=False;runs=[];state=dict(status='RUNNING',stage='precheck')
    cfg=ROOT/'configs/external-c4-light.json';value,_,expected=load(cfg,args.upstream);validate_oci(args.oci_input,value)
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,PYTHONDONTWRITEBYTECODE='1'))
    for name in set(SNAPSHOT_FILES)|{'run_c4_endpoint_controls.py','run_external_workload.py','run_host_external_workload.py','validate_isolated_agentenv.py','external_workload.py','external_oci.py'}:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    write(out/'plan.json',dict(order=ORDER,actions_per_run=36,guest_count=1,host_guest_interval=.1,pss_interval=1,p1_files=9,proxy=False,cache_reset=False,oci_input=str(args.oci_input.resolve())))
    write(out/'runs.json',runs)
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0];assert source['Id']==SOURCE_ID and not source['State']['Running']
        assert capacity_budget(available(ROOT),32*GIB)['ready']
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health')
        for i,(number,mode) in enumerate(ORDER,1):
            assert capacity_budget(available(ROOT),32*GIB)['ready']
            mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024;assert mem>64*GIB
            state.update(stage=f'{i}-{mode}',mem_available_bytes=mem,load_average=os.getloadavg());write(out/'result.json',state)
            cmd=['bash',str(ROOT/'scripts/run-external-workload-light.sh'),str(cfg),str(args.upstream.resolve()),'--sampling',mode,'--sampling-control','--oci-input',str(args.oci_input.resolve())]
            if mode=='on':cmd+=['--endpoint-metrics']
            write(out/f'{i:02}-command.json',cmd);logpath=out/f'{i:02}-{mode}.log';print(f'status=RUNNING run={i}/6 mode={mode} result_dir={out}',flush=True)
            with logpath.open('x') as log:r=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1500)
            paths={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',logpath.read_text()) if Path(p).parent==ROOT/'.artifacts/host-external-workload'}
            if len(paths)==1:state['last_workflow']=str(next(iter(paths)))
            assert r.returncode==0,'workflow failed; first failure retained, no retry'
            assert len(paths)==1;row=inspect(paths.pop(),mode,expected);row.update(index=i,pair=number);runs.append(row);write(out/'runs.json',runs)
            print(f'status=PASS run={i}/6 mode={mode}',flush=True)
        comparison(out);state.update(status='PASS',stage='complete',runs_completed=6)
    except BaseException as e:state.update(status='FAIL',error=repr(e),runs_completed=len(runs));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:subprocess.run(['docker','stop','-t','30',SOURCE],check=True,capture_output=True,timeout=60)
            except Exception as e:state.update(status='FAIL',source_cleanup_error=repr(e))
        state['source_stopped']=subprocess.check_output(['docker','inspect','--format','{{.State.Running}}',SOURCE],text=True,timeout=15).strip()=='false'
        if not state['source_stopped']:state['status']='FAIL'
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
