"""One original step-11 snapshot; independent writable N=1/N=2 services, 41 actions."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from host_file_monitor import HostFileMonitor, measurement_group, linear_reference
from host_file_matched_task import report_matched
from host_independent_task import phase_arguments, checked_prefix
from native_checkpoint_bundle import ARCHIVE_LIMIT
from platform_tasks import ROOT, configuration, preflight, commands
from run_host_file_restore import read, write, restore_command


def phase_command(cfg,cache,phase,bundle=None,source=None):
    _,command=commands(cfg,cache)
    return restore_command(command,matched=True)+phase_arguments(phase,bundle,source)


def report(out,output=None):
    out=Path(out).resolve()
    result=read(out/'result.json');assert result['status']=='PASS'
    target=out
    if output is not None:
        target=Path(output).resolve()
        inputs=[out,*[Path(p['storage']).resolve() for p in result['phases']]]
        if any(target==p or target.is_relative_to(p) for p in inputs):
            raise ValueError('offline report output must be outside original inputs')
        target.mkdir(parents=True,exist_ok=False)
    source=Path(result['phases'][0]['storage']);bundle=source/'checkpoint-bundle'
    groups=[];volumes=[]
    for phase,n in zip(result['phases'][1:],(1,2)):
        storage=Path(phase['storage']);raw=storage/'prettier6604';task=read(raw/'result.json')
        assert checked_prefix(bundle,source/'prettier6604')['template']==task['imported_snapshot']
        assert read(storage/'result.json')['checkpoint_import']['archive_sha256']==read(bundle/'bundle.json')['archive_sha256']
        trial=read(raw/'host-matched.json');assert len(trial['groups'])==1
        g=trial['groups'][0];before=g['before_restore_ordinary'];after=g['ordinary_residency']
        assert g['sandboxes']==n and before['end_monotonic_ns']<=g['baseline_start_ns']
        assert g['baseline_end_ns']<min(v['restore_begin_ns'] for v in g['guests'])
        assert before['status']==after['status']=='OBSERVED' and not before['errors'] and not after['errors']
        image=str(storage/'volume.ext4');loop=next(f for f in before['files'] if f['path']==image)
        # The filesystem identity is saved while mounted; both paths are unique owned volumes.
        volumes.append(image)
        summary=report_matched(out/'host-file',raw,target/('report-N'+str(n)))
        memory=summary['groups'][0]['memory_device'];pair=g['residency']['memory_device_pss']
        groups.append(dict(sandboxes=n,storage=str(storage.relative_to(ROOT)),
            actual_actions=task['actual_action_count'],reused_prefix_actions=task['reused_action_count'],
            snapshot_id=task['imported_snapshot'],baseline=g['baseline'],
            before_regular_files=len(before['files']),before_regular_resident_bytes=before['resident_bytes'],
            after_regular_files=len(after['files']),after_regular_resident_bytes=after['resident_bytes'],
            loop_resident_before_bytes=loop['resident_bytes'],
            loop_resident_after_bytes=next(f for f in after['files'] if f['path']==image)['resident_bytes'],
            memory_device_bytes=memory['resident_bytes'],
            anon_pss_before_bytes=pair['before']['vmm_pss_anon_bytes'],anon_pss_after_bytes=pair['after']['vmm_pss_anon_bytes'],
            task_file_delta_range_bytes=summary['groups'][0]['file_delta_range_bytes'],
            task_wall_ns=g['end_monotonic_ns']-g['start_monotonic_ns'],
            final_accounting=summary['final_inventory']['accounting'],
            final_net_shortfall_bytes=summary['final_inventory']['minimum_unaccounted_or_timing_bytes'],
            tool_ns=sum(p['guest_action_sum_ns'] for role,p in task['paths'].items() if role!='backbone'),
            official_results=[dict(role=v['role'],passed=v.get('official_passed'),failed=v.get('official_failed'),
                status=v.get('official_task_status')) for v in summary['groups'][0]['guests']]))
        if 'compact_inventory' in g['residency']:
            compact=g['residency']['compact_inventory']
            assert compact['status']=='OBSERVED' and compact['end_monotonic_ns']<g['device_released_ns']
            groups[-1]['memory_detail']=summary['groups'][0]['memory_detail']
    assert len(set(volumes))==2
    summary=dict(status='PASS_INDEPENDENT_WRITABLE_PAIR',actual_actions=sum(p['actual_actions'] for p in result['phases']),
        new_actions=sum(p.get('new_actions',p['actual_actions']) for p in result['phases']),
        original_snapshot=groups[0]['snapshot_id'],groups=groups,
        baseline_file_difference_bytes=groups[1]['baseline']['file']-groups[0]['baseline']['file'],
        device_linear_reference=linear_reference(groups[0]['memory_device_bytes'],groups[1]['memory_device_bytes'],2),
        whole_host_physical_savings='NOT_MEASURED',guest_cache_physical_attribution='NOT_MEASURED',
        scope='Same imported snapshot; distinct fresh writable volumes/services. Sequential object observations, not full physical union; device-only hypothetical linear reference.')
    assert summary['actual_actions']==41 and groups[0]['snapshot_id']==groups[1]['snapshot_id']
    if 'metadata_query_build' in result:summary['metadata_query_build']=result['metadata_query_build']
    from host_memory_detail_report import write_memory_report
    write(target/'summary.json',summary);write_memory_report(target/'MEMORY_DETAIL.md',summary)
    return summary


def resumed_capture(storage):
    storage=Path(storage).resolve()
    assert storage.parent==ROOT/'.artifacts/storage-isolated-service'
    outer=read(storage/'result.json')
    if outer['status']=='FAIL':
        recovered=read(storage/'checkpoint-export-recovery.json')
        assert recovered['status']=='PASS' and recovered['source_volume_unchanged'] and recovered['original_capture_reused']
        verification=read(storage/'checkpoint-archive-verification.json')
        assert verification['status']=='PASS' and verification['repository_contents_verified']
        assert verification['archive_sha256']==recovered['archive_sha256']
    else:
        assert outer['status']=='PASS' and outer['checkpoint_export']['status']=='PASS'
    checked_prefix(storage/'checkpoint-bundle',storage/'prettier6604')
    return dict(phase='capture',storage=str(storage),actual_actions=11,new_actions=0,
        reused_capture=True,original_storage_status=outer['status'])


def workflow(config,resume_prefix=None):
    cfg=configuration(config)
    if cfg['task_id']!='prettier__prettier-6604' or not cfg.get('host_file_monitor') or cfg['rollout']!='single' or cfg['checkpoint_after_step'] is not None:
        raise ValueError('independent pair requires the ordinary explicit Prettier host config')
    if cfg.get('damon_observe') or cfg.get('damon_disable') or cfg.get('monitor_mode')=='off':
        raise ValueError('independent pair preserves guest sampling and default DAMON')
    if os.environ.get('ASB_HOST_FILE_SAMPLING','on')!='on':raise ValueError('independent pair requires host sampling')
    group,_=measurement_group();ready=preflight(cfg)
    saved_capture=resumed_capture(resume_prefix) if resume_prefix else None
    os.umask(0o077)
    out=ROOT/'.artifacts/host-file-independent-pair'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False);write(out/'effective-config.json',cfg);write(out/'preflight.json',ready)
    code=out/'code';code.mkdir()
    for name in ('run_host_independent_pair.py','host_independent_task.py','native_checkpoint_bundle.py','host_file_matched_task.py','run_prettier6604_grpo.py','validate_isolated_agentenv.py','run_host_file_workflow.py','run_host_file_restore.py','host_mapped_backing.py','host_special_metadata.py','host_deleted_backing.py','host_existing_cache_owner.py','host_ublk_info.c','host_memory_detail_report.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    cache=ROOT/'.artifacts/prettier6604'/('host-independent-cache-'+out.name)
    result=dict(status='FAIL',workflow='host-file-independent-pair',phases=[],whole_host_physical_savings='NOT_MEASURED')
    monitor=None;stage='preflight-disk'
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        assert shutil.disk_usage(ROOT).free>50*2**30+(0 if saved_capture else ARCHIVE_LIMIT),'24 GiB volume requires over 50 GiB available plus any new archive budget'
        if os.environ.get('ASB_HOST_MEMORY_DETAIL','off')=='on':
            from host_special_metadata import build_ublk_query
            stage='build-ublk-metadata-query'
            build_start=time.monotonic_ns()
            os.environ['ASB_HOST_UBLK_INFO_QUERY']=str(build_ublk_query(out))
            result['metadata_query_build']=dict(status='PASS',duration_ns=time.monotonic_ns()-build_start,
                executable=os.environ['ASB_HOST_UBLK_INFO_QUERY'],scope='Preparation before periodic host samples; not tool or detail query time.')
        monitor=HostFileMonitor(out/'host-file',group)
        stage='cache-prepare';call(stage,[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        bundle=source=None
        if saved_capture:
            result['phases'].append(saved_capture)
            source=Path(saved_capture['storage'])/'prettier6604';bundle=source.parent/'checkpoint-bundle'
        for phase,count in ((('single',10),('dual',20)) if saved_capture else (('capture',11),('single',10),('dual',20))):
            stage=phase
            assert shutil.disk_usage(ROOT).free>50*2**30+(ARCHIVE_LIMIT if phase=='capture' else 0),'24 GiB volume requires over 50 GiB available plus new archive budget'
            call(phase,phase_command(cfg,cache,phase,bundle,source),900)
            paths=re.findall(r'status=PASS result_dir=(\S+)',(out/(phase+'.log')).read_text())
            children={Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
            assert len(children)==1;storage=children.pop();outer=read(storage/'result.json');task=read(storage/'prettier6604/result.json')
            assert outer['status']==task['status']=='PASS' and all(outer['cleanup'].values())
            assert task['actual_action_count']==count and not (storage/'volume.ext4').exists()
            assert all(v=='PASS' for v in outer['checks'].values())
            result['phases'].append(dict(phase=phase,storage=str(storage),actual_actions=count,new_actions=count))
            write(out/'result.json',result)
            if phase=='capture':
                source=storage/'prettier6604';bundle=storage/'checkpoint-bundle'
                checked_prefix(bundle,source)
        result['status']='PASS'
    except BaseException as error:
        import traceback
        result.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if monitor:
            try:
                result['collector']=monitor.stop()
                if result['collector']['status']!='PASS':result.update(status='FAIL',host_stop_error=result['collector'].get('error'))
            except Exception as error:result.update(status='FAIL',host_stop_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                result['cache_released']=read(cache/'resources.json')['released']
            except Exception as error:result.update(status='FAIL',cache_release_error=repr(error))
    write(out/'result.json',result)
    if result['status']=='PASS':
        try:result['summary']=report(out)
        except BaseException as error:
            import traceback
            result.update(status='FAIL',failed_stage='offline-report',error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path,nargs='?')
    parser.add_argument('--resume-prefix',type=Path)
    parser.add_argument('--report-only',type=Path,help='Read a saved successful workflow; no service or device access')
    parser.add_argument('--output',type=Path,help='New directory outside the original workflow and raw inputs')
    args=parser.parse_args(argv)
    if args.report_only is not None:
        if args.config or args.resume_prefix or args.output is None:
            parser.error('report-only requires output, without config or resume-prefix')
        report(args.report_only,args.output)
        print('status=PASS_OFFLINE_REPORT result_dir='+str(args.output.resolve()))
        return 0
    if args.config is None or args.output is not None:parser.error('run requires config; output is for report-only')
    return workflow(args.config,args.resume_prefix)


if __name__=='__main__':raise SystemExit(main())
