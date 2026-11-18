"""One C4 sampling pair, two concurrent guests per fresh private service."""
import argparse,json,os,re,shutil,statistics,subprocess,sys,time,traceback,urllib.request
from pathlib import Path
from platform_tasks import ROOT
from isolated_storage import available,capacity_budget,GIB
from prettier_no_proxy import clean_environment
from external_workload import load,validate_c4_outputs,validate_timings
from external_oci import validate
from run_c4_parallel import validate_parallel
from run_light_monitor_controls import SOURCE,SOURCE_ID
from external_task_metrics import SNAPSHOT_FILES

ALTERNATING_ORDER=((1,'off'),(1,'on'),(2,'on'),(2,'off'),(3,'off'),(3,'on'))
TIMING_KEYS=('tool_duration_sum_ns','schedule_elapsed_ns','actions_download_elapsed_ns','controller_elapsed_ns')

def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def pair_changes(off,on):
    return {k:dict(off=off['task'][k]/1e9,on=on['task'][k]/1e9,delta_seconds=(on['task'][k]-off['task'][k])/1e9,change_percent=(on['task'][k]/off['task'][k]-1)*100) for k in TIMING_KEYS}

def alternating_comparison(runs):
    assert [(r['pair'],r['mode']) for r in runs]==list(ALTERNATING_ORDER)
    assert len({c['sandbox_id'] for r in runs for c in r['task']['children']})==12
    assert len({r['storage'] for r in runs})==6
    pairs=[]
    for number in (1,2,3):
        selected=[r for r in runs if r['pair']==number]
        off=next(r for r in selected if r['mode']=='off');on=next(r for r in selected if r['mode']=='on')
        pairs.append(dict(pair=number,order=[r['mode'] for r in selected],metrics=pair_changes(off,on)))
    summary={k:dict(off_median_seconds=statistics.median(p['metrics'][k]['off'] for p in pairs),on_median_seconds=statistics.median(p['metrics'][k]['on'] for p in pairs),paired_change_median_percent=statistics.median(p['metrics'][k]['change_percent'] for p in pairs),paired_change_range_percent=[min(p['metrics'][k]['change_percent'] for p in pairs),max(p['metrics'][k]['change_percent'] for p in pairs)]) for k in TIMING_KEYS}
    return dict(status='PASS',pairs=pairs,summary=summary)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--delete-trace',action='store_true');p.add_argument('--alternating-repeats',action='store_true',help='Explicit three alternating pairs, without deletion tracing');p.add_argument('--output',type=Path,required=True);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--oci-input',type=Path,required=True);args=p.parse_args();out=args.output.resolve()
    if args.alternating_repeats and args.delete_trace:p.error('alternating repeats do not include extra deletion tracing')
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();out.mkdir(parents=True);started=False;state=dict(status='RUNNING',runs=[])
    cfg=ROOT/'configs/external-c4-light.json';config,_,expected=load(cfg,args.upstream);validate(args.oci_input,config)
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,PYTHONDONTWRITEBYTECODE='1'))
    order=ALTERNATING_ORDER if args.alternating_repeats else ((1,'off'),(1,'on'))
    if args.alternating_repeats:
        code=out/'code';code.mkdir()
        for name in set(SNAPSHOT_FILES)|{'run_c4_parallel_pair.py','run_c4_parallel.py','run_external_workload.py','run_host_external_workload.py','validate_isolated_agentenv.py','external_workload.py','external_oci.py','isolated_storage.py'}:shutil.copy2(ROOT/'scripts'/name,code/name)
        write(out/'plan.json',dict(order=order,guest_count=2,actions_per_guest=36,total_actions=432,host_guest_interval=.1,pss_interval=1,p1_files_per_guest=9,delete_trace=False,proxy=False,cache_reset=False,oci_input=str(args.oci_input.resolve())))
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0];assert source['Id']==SOURCE_ID and not source['State']['Running']
        assert capacity_budget(available(ROOT),32*GIB)['ready']
        assert int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024>64*GIB
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health')
        for index,(number,mode) in enumerate(order,1):
            budget=capacity_budget(available(ROOT),32*GIB);assert budget['ready'],budget
            mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024;assert mem>64*GIB
            state.update(stage=f'{index}-{mode}',available_disk_bytes=budget['available_bytes'],mem_available_bytes=mem,load_average=os.getloadavg());write(out/'result.json',state)
            cmd=['bash',str(ROOT/'scripts/run-external-workload-light.sh'),str(cfg),str(args.upstream.resolve()),'--sampling',mode,'--sampling-control','--guest-count','2','--oci-input',str(args.oci_input.resolve())]
            if args.delete_trace:cmd+=['--delete-trace']
            if mode=='on':cmd+=['--endpoint-metrics']
            label=f'{index:02}-{mode}' if args.alternating_repeats else mode
            write(out/(label+'-command.json'),cmd);print(f'status=RUNNING run={index}/{len(order)} mode={mode} result_dir={out}',flush=True)
            logpath=out/(label+'.log')
            with logpath.open('x') as log:r=subprocess.run(cmd,env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1500)
            paths={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',logpath.read_text()) if Path(p).parent==ROOT/'.artifacts/host-external-workload'}
            if len(paths)==1:state['last_workflow']=str(next(iter(paths)))
            assert r.returncode==0,'first failure retained; no retry';assert len(paths)==1
            workflow=paths.pop();w=read(workflow/'result.json');t=w['task'];validate_parallel(t,mode);storage=Path(w['storage']);raw=Path(w['raw'])
            for i,c in enumerate(t['children']):
                child=raw/f'c4-parallel-{i}';validate_c4_outputs(child/'input/logs')
                import csv
                with (child/'tools.tsv').open() as f:tools=list(csv.DictReader(f,delimiter='\t'))
                validate_timings(tools);assert len(tools)==36 and [int(x['exit_code']) for x in tools]==[int(x['exit_code']) for x in expected]
                if mode=='off':assert not any((child/k).exists() for k in ('runtime-monitor.json','external-metrics.json','host-vmm'))
                elif args.alternating_repeats:
                    assert c['endpoint_metrics']['p1']['unique_files']==9
                    monitor=read(child/'runtime-monitor.json');assert monitor['profile']=='light' and monitor['interval_seconds']==.1
            if mode=='off':assert not (workflow/'host-file').exists()
            elif args.alternating_repeats:
                assert w['collector']['status']=='PASS' and w['collector']['interval_seconds']==.1 and w['collector']['pss_interval_seconds'] is None
                identities=[{(f['device'],f['inode']) for f in c['endpoint_metrics']['p1']['files']} for c in t['children']]
                assert identities[0]==identities[1] and len(identities[0])==9
            outer=read(storage/'result.json');assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
            assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
            row=dict(delete_trace=outer.get('delete_trace'),mode=mode,workflow=str(workflow),storage=str(storage),raw=str(raw),task=t,host_collector=w.get('collector'),min_disk_gib=outer['observed_host_available_min_bytes']/GIB)
            if args.alternating_repeats:row.update(index=index,pair=number)
            state['runs'].append(row);write(out/'result.json',state);print('status=PASS mode='+mode,flush=True)
        if args.alternating_repeats:state['comparison']=alternating_comparison(state['runs'])
        else:state['changes']=pair_changes(*state['runs'])
        state.update(status='PASS',stage='complete')
    except BaseException as e:state.update(status='FAIL',error=repr(e));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:subprocess.run(['docker','stop','-t','30',SOURCE],check=True,capture_output=True,timeout=60)
            except Exception as e:state.update(status='FAIL',source_cleanup_error=repr(e))
        state['source_stopped']=subprocess.check_output(['docker','inspect','--format','{{.State.Running}}',SOURCE],text=True,timeout=15).strip()=='false'
        if not state['source_stopped']:state['status']='FAIL'
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
