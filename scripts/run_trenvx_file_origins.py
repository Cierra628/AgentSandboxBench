"""One private full Exp1 replay with post-task merged-file source verification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback

from archive_trenvx_run_data import sha256
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from run_trenvx_fixed_identity import ROOT, SOURCES as OLD_SOURCES, cpu_policies, inspect

SOURCES=tuple(dict.fromkeys(OLD_SOURCES+('run_trenvx_file_origins.py','guest_file_origins.py',
    'trenvx_named_guest_files.py','trenvx_kvm_capture.py','named_guest_file_physical.py',
    'guest_named_file_probe.py','guest_file_type_check.py','measure_file_physical.py',
    'guest_whole_cache_probe.py','trenvx_whole_cache.py')))


def source_proof(raw):
    cfg=json.loads((raw/'config.json').read_text())
    assert cfg['file_origin_diagnostic'] and not cfg['checkpoint_after']
    assert sha256(raw/'guest_file_origins.py')==cfg['file_origin_source_sha256']
    assert sha256(raw/'file-origin-input.json')==cfg['file_origin_manifest_sha256']
    inputs=json.loads((raw/'file-origin-input.json').read_text())
    result=json.loads((raw/'file-origin-result.json').read_text())
    before=result['before'];after=result['after']
    assert before['status']==after['status']=='PASS'
    assert before['layout']==after['layout']
    assert before['layout']['mountpoint']=='/' and before['layout']['pivot_prefix']=='/rom'
    # This no-checkpoint probe is deliberately scoped to one upper over base.
    assert before['layout']['layers']==[dict(origin='upper',root='/rom/overlay/root'),dict(origin='base',root='/rom')]
    assert len(before['files'])==len(after['files'])==len(inputs['files'])==3
    for sample,label in ((before,'metadata'),(after,'content')):
        receipt=json.loads((raw/f'file-origin-{label}.json').read_text())
        assert receipt['exit_code']==0 and json.loads(receipt['stdout'])==sample
        for row,expected in zip(sample['files'],inputs['files']):
            assert row['path']==expected['path'] and row['origin']==expected['expected_origin']
            assert row['status']==expected['expected_status']=='SOURCE_VERIFIED'
            assert row['physical_memory']==dict(status='NOT_MEASURED',bytes=None)
            proof=row['proof'];layers=sample['layout']['layers']
            assert [part['origin'] for part in proof]==[layer['origin'] for layer in layers[:len(proof)]]
            assert proof[-1]['origin']==row['origin'] and proof[-1]['kind']=='file'
            assert all(part['kind']=='absent' for part in proof[:-1])
            selected=layers[len(proof)-1]['root']+row['path']
            assert proof[-1]['candidate']==row['candidate']==selected
            assert proof[-1]['identity']==row['underlying']
            assert row['visible']['st_size']==row['underlying']['st_size']
            assert row['visible']['st_mtime_ns']==row['underlying']['st_mtime_ns']
            assert row['content_verified']==(label=='content')
            if label=='content':assert re.fullmatch('[0-9a-f]{64}',row['sha256'])
    for left,right in zip(before['files'],after['files']):
        assert all(left[key]==right[key] for key in ('path','status','origin','candidate','visible','underlying','proof'))
    return dict(status='PASS',files=[dict(path=row['path'],origin=row['origin'],sha256=row['sha256'],
        content_verified=True,physical_memory=row['physical_memory']) for row in after['files']],
        metadata_elapsed_ns=before['elapsed_ns'],content_elapsed_ns=after['elapsed_ns'],
        scope=result['scope'],physical_cache_status='NOT_MEASURED')


def audit(out):
    saved=json.loads((out/'result.json').read_text());assert saved['status']=='PASS'
    raw=Path(saved['run']['raw_dir'])
    monitored=saved['protocol'].get('monitoring_acceptance',False)
    row,identity=inspect(raw,'both' if monitored else 'off')
    assert identity==saved['identity']
    for key,value in row.items():
        if key!='retained_allocated_bytes':assert value==saved['run'][key]
    checked=source_proof(raw);assert checked==saved['origins']
    if json.loads((raw/'config.json').read_text()).get('guest_file_physical_diagnostic'):
        physical=physical_proof(raw,checked);assert physical==saved['physical']
    if monitored:
        assert post_scan_proof(raw)['status']=='PASS'
        from report_memory_metrics import report
        memory=report(dict(backend='trenvx',mode='full-light',raw=str(raw)))
        assert memory==json.loads((out/'memory/summary.json').read_text())
    if saved['protocol'].get('base_file_physical',False):
        assert combined_file_proof(raw)==saved['combined_physical']
    if saved['protocol'].get('whole_cache',False):
        from trenvx_whole_cache import recount
        computed=recount(raw/'whole-cache')
        assert computed=={k:v for k,v in saved['whole_cache'].items() if k!='controller_elapsed_ns'}
    assert json.loads((out/'cpu-before.json').read_text())==json.loads((out/'cpu-after.json').read_text())
    hashes=json.loads((out/'code/sha256.json').read_text())
    assert hashes=={name:sha256(out/'code'/name) for name in hashes}
    measured=json.loads((raw/'config.json').read_text()).get('guest_file_physical_diagnostic',False)
    return dict(status='PASS',full_exp1_replay_count=1,source_proof=checked,
        scope=('upper-file subset RAM proof; whole-task, multi-VM and restore impact unmeasured' if measured
               else 'file-source verification; physical memory, multi-VM and restore impact unmeasured'))


def physical_proof(raw,origins):
    from named_guest_file_physical import recount
    from trenvx_kvm_capture import recount_capture
    directory=raw/'named-guest-files'
    load=lambda name:json.loads((directory/name).read_text())
    saved=load('result.json');vm=load('vmm-identity.json');proof=load('ram-attestation.json')
    assert proof==recount_capture(raw/'kvm-capture',vm,proof['iomem'])
    from trenvx_named_guest_files import with_mapping_source
    source=json.loads((raw/'file-origin-result.json').read_text())
    before=with_mapping_source(load('before.json'),source['before'])
    after=with_mapping_source(load('after.json'),source['after'])
    assert before==load('before-with-source.json') and after==load('after-with-source.json')
    rows=load('pages.json')
    computed=recount(before,after,rows,vm['sandbox_id'])
    assert saved['status']==computed['status']=='PASS'
    for key,value in computed.items():
        if key!='limitations':assert value==saved[key]
    for phase,checks in load('host-rechecks.json').items():
        assert checks==[dict(owner=r['owner'],index=r['index'],address=r['address'],entry=r['entry']) for r in rows],phase
    from measure_file_physical import ram_address
    assert all(r['vm']==vm['sandbox_id'] and r['address']==ram_address(r['guest_pfn'],proof['slots']) for r in rows)
    receipt=load('guest-result.json');assert receipt==saved['guest_receipt'] and receipt['status']=='PASS'
    upper=[row for row in origins['files'] if row['origin']=='upper'];assert len(upper)==len(receipt['files'])==1
    assert receipt['files'][0]['path']==upper[0]['path'] and receipt['files'][0]['sha256']==upper[0]['sha256']
    cfg=json.loads((raw/'config.json').read_text())
    assert cfg['guest_file_physical_sources']=={name:sha256(raw/name) for name in cfg['guest_file_physical_sources']}
    return saved


def post_scan_proof(raw):
    from exp1_official_task import grade
    load=lambda name:json.loads((raw/name).read_text())
    cfg=load('config.json');assert cfg['monitoring_acceptance'] or cfg.get('monitoring_control')
    before=json.loads(load('independent-after.json')['stdout'])
    after=load('independent-post-scan.json')
    assert after['exit_code']==0 and json.loads(after['stdout'])==before
    patch=load('patch-post-scan.json')
    assert patch['exit_code']==0 and patch['stdout']==(raw/'replay/patch.diff').read_text()
    official=load('official-task-post-scan.json');assert official['exit_code']==0
    assert grade(Path(cfg['official_task']),official['stdout'])==load('official-task-post-scan-result.json')
    result=load('post-scan-correctness.json');assert result['status']=='PASS'
    return result


def combined_file_proof(raw):
    from measure_file_physical import audit_run as audit_physical
    from trenvx_named_guest_files import combined_pages
    directory=raw/'base-guest-files'
    load=lambda p:json.loads(p.read_text())
    checked=audit_physical(directory)
    assert checked['audit_status']==checked['measurement_status']=='PASS'
    rows=load(raw/'named-guest-files/pages.json')+load(directory/'pages.json')
    assert load(directory/'combined-host-rechecks.json')==[dict(address=r['address'],entry=r['entry']) for r in rows]
    computed=combined_pages(load(raw/'named-guest-files/pages.json'),load(directory/'pages.json'))
    saved=load(directory/'combined.json')
    assert computed=={k:v for k,v in saved.items() if k!='controller_elapsed_ns'}
    source=load(raw/'file-origin-result.json')['after']['files']
    assert load(directory/'file-hashes.json')=={r['path']:r['sha256'] for r in source if r['origin']=='base'}
    return saved


def main(physical=False,monitored=False,base_files=False,whole_cache=False):
    os.umask(0o077)
    physical=physical or monitored
    category='trenvx-monitoring-acceptance' if monitored else 'trenvx-upper-file-physical' if physical else 'trenvx-file-origins'
    out=ROOT/'.artifacts'/category/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True);(out/'code').mkdir()
    hashes={name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    before=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(before,indent=2)+'\n')
    result=dict(status='FAIL',protocol=dict(vm_count=1,full_replays=1,checkpoint=False,
        host_periodic_sampling=monitored,guest_periodic_sampling=monitored,monitoring_acceptance=monitored,
        cache_policy='preserve; no global drop',
        base_file_physical=base_files,
        whole_cache=whole_cache,
        probe_order='full frozen replay; official task evaluation; source metadata; optional upper physical proof; source content',
        performance_comparison='NOT_RUN',physical_cache_measurement='REQUESTED' if physical else 'NOT_RUN',
        stop_policy='first failure; no automatic retry'))
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        plan=successful_image_cleanup_budget(shutil.disk_usage(out).free,1)
        (out/'storage-plan.json').write_text(json.dumps(plan,indent=2)+'\n');assert plan['status']=='READY'
        argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images','--record-phases',
            '--fixed-git-identity','--file-origin-diagnostic',
            '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
            '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
        if physical:argv.append('--guest-file-physical-diagnostic')
        if monitored:argv+=['--monitoring-acceptance','--guest-memory-sampling','--guest-collector','persistent',
                            '--guest-sample-interval','0.1','--host-sample-interval','0.1']
        if base_files:argv.append('--base-file-physical-diagnostic')
        if whole_cache:argv.append('--whole-cache-diagnostic')
        with (out/'driver.log').open('w') as log:guarded_run(argv,directory=out,log=log,timeout=600)
        paths=re.findall(r'result_dir=(\S+)',(out/'driver.log').read_text());assert len(paths)==1
        raw=Path(paths[0]).resolve(strict=True);assert raw.parent==(ROOT/'.artifacts/trenvx-service-smoke').resolve()
        row,identity=inspect(raw,'both' if monitored else 'off');result.update(run=row,identity=identity,origins=source_proof(raw))
        if physical:result['physical']=physical_proof(raw,result['origins'])
        if base_files:result['combined_physical']=combined_file_proof(raw)
        if whole_cache:result['whole_cache']=json.loads((raw/'whole-cache/result.json').read_text())
        if monitored:
            from report_memory_metrics import report,write_report
            result['post_scan_correctness']=post_scan_proof(raw)
            memory=report(dict(backend='trenvx',mode='full-light',raw=str(raw)))
            assert memory['guest_composition']['status']=='PASS'
            write_report(memory,out/'memory')
        assert hashes=={name:sha256(ROOT/'scripts'/name) for name in SOURCES},'source changed'
        assert cpu_policies()==before,'CPU policy changed'
        result['status']='PASS'
    except BaseException as error:
        result['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
        if (out/'driver.log').exists():
            paths=re.findall(r'result_dir=(\S+)',(out/'driver.log').read_text())
            if len(paths)==1:result['failed_raw_dir']=paths[0]
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',type=Path)
    parser.add_argument('--guest-file-physical',action='store_true')
    parser.add_argument('--monitoring-acceptance',action='store_true')
    parser.add_argument('--base-files',action='store_true')
    parser.add_argument('--whole-cache',action='store_true');args=parser.parse_args()
    if args.base_files and not args.monitoring_acceptance:parser.error('base files require monitoring acceptance')
    if args.whole_cache and not args.monitoring_acceptance:parser.error('whole cache requires monitoring acceptance')
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main(args.guest_file_physical,args.monitoring_acceptance,args.base_files,args.whole_cache))
