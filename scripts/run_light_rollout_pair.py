"""One Prettier GRPO and one BPO with explicit light guest and host counters."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

from platform_tasks import ROOT, configuration, preflight
from isolated_storage import available, capacity_budget, GIB
from run_light_monitor_controls import SOURCE, SOURCE_ID


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def inspect_run(workflow,schedule,output,*,report_override=None,retained_inventory_failure=False):
    work=read(workflow/'result.json');assert work['cache_released']
    assert work['status']==('FAIL' if retained_inventory_failure else 'PASS')
    storage=Path(work['storage_result_dir']);outer=read(storage/'result.json')
    raw=storage/'prettier6604';task=read(raw/'result.json')
    report=Path(report_override) if report_override is not None else Path(work['report_dir']);summary=read(report/'summary.json')
    monitor=read(raw/'runtime-monitor.json')
    assert task['status']==monitor['status']=='PASS'
    assert outer['status']==('FAIL' if retained_inventory_failure else 'PASS')
    if retained_inventory_failure:
        inventory=outer['host_file_residency']
        assert inventory['status']=='INCOMPLETE' and not inventory['errors']
        unstable=[f for f in inventory['files'] if not f['stable']]
        assert len(unstable)==1 and unstable[0]['path']==str(storage/'volume.ext4') and unstable[0]['metadata_changed']
        assert not outer.get('error') and outer['default_runtime_report']['status']=='PASS'
        assert outer['cleanup']['failed_fixture_retained'] and not outer['cleanup']['host_file_residency_captured']
    assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and monitor['guest_performance_version'] is None
    assert len(monitor['sessions'])==(7 if schedule=='grpo' else 9)
    assert len({s['sandbox_id'] for s in monitor['sessions']})==7
    for session in monitor['sessions']:
        directory=raw/session['raw_directory']
        assert session['status']=='PASS' and session['collector_reaped'] and session['receipt']['profile']=='light'
        assert session['receipt']['interval_seconds']==.1
        assert not any((directory/name).exists() for name in ('guest-cpu.tsv','guest-diskstats.tsv','guest-vmstat.tsv'))
    assert not (raw/'host-vmm').exists() and not (raw/'host/memory-samples.tsv').exists()
    assert task['actual_action_count']==(189 if schedule=='grpo' else 84)
    assert all(p['official_passed']==0 and p['official_failed']==2 and p['official_task_status']=='FAIL' for p in task['paths'].values())
    assert all(outer['cleanup'].get(k) for k in ('owned_container_removed','mount_and_loop_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    if not retained_inventory_failure:assert outer['cleanup']['successful_fixture_removed']
    timing=summary['rollout_timing'];baseline=task['host_before_first_tool']
    assert baseline['end_monotonic_ns']<=task['first_tool_start_monotonic_ns']
    with (workflow/'host-file/memory.tsv').open() as stream:
        counters=[{k:int(v) if v else None for k,v in row.items()} for row in csv.DictReader(stream,delimiter='\t')]
    window=[r for r in counters if task['first_tool_start_monotonic_ns']<=r['monotonic_ns']<=timing['end_monotonic_ns']]
    assert window and counters[0]['monotonic_ns']<timing['start_monotonic_ns']<timing['end_monotonic_ns']<counters[-1]['monotonic_ns']
    host=read(report/'host-file/summary.json')
    shared=None
    if 'host_shared_files' in task:
        shared=read(report/'shared-files/summary.json')
        assert shared['status']=='OBSERVED' and shared['rollout_includes_query']
        assert host['pss_status']=='NOT_REQUESTED' and not (workflow/'host-file/pss.jsonl').exists()
        assert outer['host_shared_preparation_sync']['status']=='PASS'
        assert outer['host_shared_preparation_sync']['end_monotonic_ns']<outer['host_file_creation']['verified_before_guest_creation_ns']
    output.mkdir()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,constrained_layout=True)
    times=[(r['monotonic_ns']-task['first_tool_start_monotonic_ns'])/1e9 for r in window]
    base=baseline['counters']['file']
    axes[0].plot(times,[r['file']/2**20 for r in window],label='Host file (includes shmem)')
    axes[0].set(title=schedule.upper()+': host accounting during task segments and checks',ylabel='MiB')
    axes[1].plot(times,[(r['file']-base)/2**20 for r in window],label='Host file minus pre-first-segment baseline')
    axes[1].axhline(0,color='gray',linewidth=.7)
    axes[1].set(ylabel='MiB change',xlabel='Host monotonic seconds from first segment dispatch')
    for ax in axes:ax.grid(alpha=.2);ax.legend()
    fig.savefig(output/'host-task.png',dpi=130);fig.savefig(output/'host-task.svg');plt.close(fig)
    value=dict(status='PASS_LIGHT_METRICS_WITH_INCOMPLETE_INVENTORY' if retained_inventory_failure else 'PASS',
        original_workflow_status=work['status'],inventory_status=outer.get('host_file_residency',{}).get('status'),
        retained_failure_volume=str(storage/'volume.ext4') if retained_inventory_failure else None,
        schedule=schedule,workflow=str(workflow),storage=str(storage),raw=str(raw),report=str(report),
        actual_actions=task['actual_action_count'],logical_actions=task.get('logical_action_count',task['actual_action_count']),
        tool_ns=summary['tool_duration_sum_ns'],rollout_timing=timing,
        guest_samples=sum(s['receipt']['samples'] for s in monitor['sessions']),guest_sessions=len(monitor['sessions']),
        per_sandbox=summary['per_sandbox'],host=host,host_before_first_segment=baseline,
        task_window_file_min_bytes=min(r['file'] for r in window),task_window_file_max_bytes=max(r['file'] for r in window),
        task_window_file_final_delta_bytes=window[-1]['file']-base,
        final_sample_to_checks_end_ns=timing['end_monotonic_ns']-window[-1]['monotonic_ns'],
        official_task_status='FAIL',official_per_instance=dict(passed=0,failed=2),
        cache_policy='Original kernel/DAMON configuration retained; no added reclaim, prefetch or pinning',
        physical_savings='NOT_MEASURED',scope='Current light collection, one run per schedule; descriptive comparison, not repeated performance estimation')
    if shared is not None:value['shared_files']=shared
    write(output/'summary.json',value)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reuse-completed-grpo',type=Path,help='Offline delivery of a completed GRPO whose only failure was the final changing loop image; run BPO only')
    parser.add_argument('--shared-files',action='store_true',help='New explicit configs: no PSS, shared-object query after original paths')
    args=parser.parse_args();out=args.output.resolve()
    if args.shared_files and args.reuse_completed_grpo:parser.error('shared-files requires new measurements; cannot reuse a legacy GRPO')
    profile='shared-files' if args.shared_files else 'light-host'
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_light_rollout_pair.py','run-light-rollout-pair.sh','platform_tasks.py','run_prettier6604_grpo.py',
                 'validate_isolated_agentenv.py','report_task_overview.py','report_prettier6604_grpo.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.shared_files:
        for name in ('host_rollout_shared.py','host_rootfs_layers.py','host_prefix_compare.py','host_scaling.py','run_host_file_restore.py','report_host_file.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    os.environ['ASB_AGENTENV_SOURCE_SERVICE']=SOURCE
    runs=[];started=False;result=dict(status='FAIL',stage='precheck')
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        for schedule in ('grpo','bpo'):
            cfg=configuration(ROOT/f'configs/platform-prettier6604-{schedule}-{profile}.json')
            write(out/(schedule+'-preflight.json'),preflight(cfg,offline=True))
        if args.reuse_completed_grpo:
            workflow=args.reuse_completed_grpo.resolve();work=read(workflow/'result.json')
            storage=Path(work['storage_result_dir']) if 'storage_result_dir' in work else None
            if storage is None:
                paths=re.findall(r'status=RUNNING result_dir=(\S+)',(workflow/'isolated-runtime.log').read_text())
                assert len(paths)==1;storage=Path(paths[0])
            assert storage.parent==ROOT/'.artifacts/storage-isolated-service'
            # A derived receipt records the source relationship; the original
            # FAIL workflow and volume are never rewritten or deleted.
            delivery=out/'grpo-source';delivery.mkdir()
            write(delivery/'result.json',dict(work,storage_result_dir=str(storage)))
            reports=out/'grpo-reports';shutil.copytree(storage/'report',reports)
            from report_host_file import report as host_report
            host_report(workflow/'host-file',storage/'host-file-residency.json',reports/'host-file',allow_incomplete_inventory=True)
            # Preserve the original host sample directory for the same query.
            (delivery/'host-file').symlink_to(workflow/'host-file',target_is_directory=True)
            value=inspect_run(delivery,'grpo',out/'grpo',report_override=reports,retained_inventory_failure=True)
            value['workflow']=str(workflow);value['source_delivery_receipt']=str(delivery)
            write(out/'grpo/summary.json',value);runs.append(value);write(out/'runs.json',runs)
        result['stage']='source-start'
        subprocess.run(['docker','start',SOURCE],timeout=30,check=True,capture_output=True);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        for schedule in ('grpo','bpo'):
            if schedule=='grpo' and args.reuse_completed_grpo:continue
            result['stage']=schedule
            budget=capacity_budget(available(ROOT),(16 if schedule=='grpo' else 32)*GIB)
            assert budget['ready'],'disk budget insufficient'
            write(out/(schedule+'-resources.json'),dict(budget=budget,load=os.getloadavg()))
            write(out/'result.json',dict(result,status='RUNNING',runs=runs))
            print('status=RUNNING schedule='+schedule+' result_dir='+str(out),flush=True)
            with (out/(schedule+'.log')).open('x') as log:
                proc=subprocess.run(['bash','scripts/run-host-file-monitor.sh',f'configs/platform-prettier6604-{schedule}-{profile}.json'],
                    cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1700)
            assert proc.returncode==0,schedule+' failed; retain original evidence'
            paths=re.findall(r'status=PASS result_dir=(\S+)',(out/(schedule+'.log')).read_text())
            workflows={Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/platform-tasks'}
            assert len(workflows)==1
            runs.append(inspect_run(workflows.pop(),schedule,out/schedule));write(out/'runs.json',runs)
            print('status=PASS schedule='+schedule,flush=True)
        result.update(status='PASS',stage='complete')
    except BaseException as error:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());result['error']=repr(error)
    finally:
        if started:
            with (out/'source-stop.log').open('x') as log:
                stop=subprocess.run(['docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT,timeout=60)
            state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=20))
            write(out/'source-after.json',state)
            result['source_returned_to_stopped']=stop.returncode==0 and not state['Running']
            if not result['source_returned_to_stopped']:result['status']='FAIL'
        result['runs']=runs;write(out/'result.json',result)
        print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
