"""Three short Sphinx cases on one owned service; calibration before formal controls."""
import argparse
import csv
import json
import os
from pathlib import Path
import time
import traceback

from sphinx7590_inputs import ROOT,validate
from smoke_agentenv_whole_cache import main as case
from task_metrics_prefix import check_prefix
from report_file_inventory import kernel_audit,audit_frozen_fixture
from archive_trenvx_run_data import sha256,allocated_bytes

MODES=('sampling_off','sampling_on','analysis_on')


def audit(raw):
    protocol=json.loads((raw/'protocol.json').read_text())
    cfg=json.loads((raw/'prepared-config.json').read_text())
    reference=Path(protocol['reference'])
    result=dict(status='PASS',task_id=cfg['task_id'],scope='Sphinx first six frozen actions; one short three-case smoke, not full task acceptance',
                cases=[],page_size=4096,physical_protocol='exact-kernel-frozen-v1',threshold=None,
                monitoring_impact='SHORT_SMOKE_ONLY; no repeated or balanced statistical validation')
    for mode in MODES:
        p=raw/mode;r=json.loads((p/'result.json').read_text());configuration=json.loads((p/'config.json').read_text())
        assert r['status']=='PASS' and r['checks']['cleanup']==r['checks']['frozen_task_prefix']==r['checks']['post_scan_correctness']=='PASS'
        assert json.loads((p/'task-before.json').read_text())==json.loads((p/'task-after.json').read_text())
        assert json.loads((p/'task-oracle-before.json').read_text())==json.loads((p/'task-oracle-after.json').read_text())
        for name,digest in configuration['source_sha256'].items():assert sha256(p/'code'/name)==digest
        replay=check_prefix(p,cfg,reference,mode!='sampling_off');assert replay==json.loads((p/'prefix-result.json').read_text())
        assert configuration['cpu']==2 and configuration['memory_mib']==4096
        assert configuration['periodic_sampling']==(mode!='sampling_off') and configuration['whole_cache_analysis']==(mode=='analysis_on')
        events=[json.loads(line) for line in (p/'events.jsonl').read_text().splitlines()];assert all(e['exit_code']==0 for e in events)
        event={e['phase']:e for e in events}
        def duration(label):return event[label]['end_monotonic_ns']-event[label]['start_monotonic_ns']
        with (p/'host-vmm.tsv').open() as stream:host=list(csv.DictReader(stream,delimiter='\t'))
        assert len(host)>=1 if mode=='sampling_off' else len(host)>=2
        host_summary=dict(sample_count=len(host),sampling='one preparation point' if mode=='sampling_off' else 'periodic 100ms',
            observed_vmm_pss_max_bytes=max(int(row['pss_bytes']) for row in host),
            observed_vmm_rss_max_bytes=max(int(row['rss_bytes']) for row in host),
            observed_service_cgroup_max_bytes=max(int(row['service_cgroup_current_bytes']) for row in host))
        row=dict(mode=mode,prefix=replay,host=host_summary,checks=r['checks'],cleanup='PASS',
                 timing_ns=dict(prefix_runner_call=duration('prefix-exec'),prefix_download_call=duration('prefix-download'),
                    guest_tools=replay['guest_action_sum_ns'],full_case=r['controller_elapsed_ns']),physical=None)
        if mode=='analysis_on':
            proof=json.loads((p/'whole-cache/ram-attestation.json').read_text())
            physical=kernel_audit(p/'file-inventory',proof)
            assert physical['cache_scope_status']==physical['frozen_cache_physical_status']==physical['classification_status']=='PASS'
            fixture=audit_frozen_fixture(p/'file-inventory');assert fixture['status']=='PASS'
            paused=json.loads((p/'whole-cache/paused-boundary.json').read_text())
            assert paused['status']==paused['original_vmm_identity']==paused['heartbeat_continuity']=='PASS'
            whole=json.loads((p/'whole-cache/result.json').read_text())
            row['physical']=dict(frozen_guest_file_lru=physical,known_fixture=fixture,
                backing_cache_interval={k:whole[k] for k in ('measurement_status','shared_layer_cache_stable_physical_bytes',
                    'private_upper_cache_stable_physical_bytes','attached_drive_cache_stable_physical_bytes','backing_cache_stable_physical_bytes','unstable_unique_pages')},
                fixture_regular_content_included_bytes=3*4096,whole_task_file_content_physical_bytes=None,
                scope='paused SystemRAM file-LRU, tmpfs/anonymous excluded, host PFN deduplicated; regular files and metadata separately typed; specified backing cache is an interval, not the frozen-point total')
            row['timing_ns'].update(analysis=whole['controller_elapsed_ns'],paused_interval=paused['paused_interval_ns'],kernel_decoder=physical['decoder_elapsed_ns'])
            row['analysis_resources']={k:whole[k] for k in ('collector_cpu_user_seconds','collector_cpu_system_seconds','collector_process_maxrss_kib','collector_scope')}
        result['cases'].append(row)
    off,on,analysis=result['cases']
    result['smoke_comparison_percent']={label:{k:100*(right['timing_ns'][k]/left['timing_ns'][k]-1) for k in ('guest_tools','prefix_runner_call','full_case')}
        for label,left,right in [('periodic_sampling',off,on),('precision_analysis',on,analysis)]}
    result['limitations']=['只有一组固定顺序短验证，顺序、缓存热度、主机负载和频率可能影响比较；不能判定稳定开销或无影响。',
        '精细分析在六步之后执行，包括探针、三页校准、暂停、扫描和恢复检查，不只计纯扫描时间。',
        '三页已知文件计入所测缓存；没有遍历全部任务文件，完整任务物理内容量未测。',
        'Host PSS和服务cgroup分别计量；与guest物理缓存、逻辑Cached和RAM准备量不得相加。',
        '未执行剩余29步、任务测试或checkpoint；原完整重放和恢复证据保持不变。']
    return result


def run(args):
    cfg,_,_,_=validate(args.inputs)
    from report_xarray6992 import audit as reference_audit
    reference_summary,_,_=reference_audit(args.reference.resolve());assert reference_summary['config']==cfg
    if args.check_only:print('status=VALIDATED task='+cfg['task_id']+' actions_per_case=6 cases=3');return 0
    out=args.output.resolve();assert out.is_relative_to(ROOT/'.artifacts/storage-isolated-service')
    assert out.name=='python-backbone' and out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir(exist_ok=False);begin=time.monotonic_ns()
    protocol=dict(task_id=cfg['task_id'],modes=list(MODES),reference=str(args.reference.resolve()),
        sampling_interval_seconds=.1,physical_protocol='exact-kernel-frozen-v1',threshold=None,
        scope='first six actions; fresh sequential VMs, same converted shared layers, no cache drop; one non-balanced smoke')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    (out/'prepared-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    task=dict(inputs=args.inputs.resolve(),reference=args.reference.resolve(),container=args.container,
              config_home=args.config_home.resolve(),service_url=args.service_url)
    result=dict(status='FAIL',task_id=cfg['task_id'],checks={},cache_metrics_smoke=True)
    try:
        for mode in MODES:
            rc=case(False,mode=='analysis_on',True,'fixture',True,'frozen',task=task,output=out/mode,sampling=mode!='sampling_off')
            assert rc==0,mode
            assert allocated_bytes(out)<384*2**20,'short smoke raw budget exceeded'
        summary=audit(out)
        (out/'metrics-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        result.update(status='PASS',checks=dict(frozen_prefix_all_cases='PASS',post_analysis_state='PASS',
            exact_kernel_physical_cache='PASS',known_fixture='PASS'),cleanup=dict(service_sandbox_list_empty=True),
            replay=dict(action_count=18,scope='three copies of the same six-step prefix; no complete trajectory'),
            monitoring_impact='SHORT_SMOKE_ONLY')
    except BaseException as error:
        result['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
    result['controller_elapsed_ns']=time.monotonic_ns()-begin
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--container');parser.add_argument('--config-home',type=Path);parser.add_argument('--service-url');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.check_only and not all((args.container,args.config_home,args.service_url,args.output)):parser.error('owned service and output arguments required')
    os.umask(0o077);raise SystemExit(run(args))
