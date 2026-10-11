"""Read-only audit of an incomplete Xarray replay; never changes FAIL to PASS."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from xarray6992_inputs import sha,validate,normalize_patch


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def audit(raw):
    cfg,_,_,_=validate(raw/'prepared-input')
    result=json.loads((raw/'result.json').read_text())
    outer=json.loads((raw.parent/'result.json').read_text())
    assert result['status']==outer['status']=='FAIL'
    assert result['failed_stage']=='remaining-frozen-segments'
    for name,digest in cfg['input_sha256'].items():assert sha(raw/'frozen-input'/name)==digest
    segments=rows(raw/'segments.tsv');ids=rows(raw/'sandbox-ids.tsv')
    assert len(ids)==1 and ids[0]['created_from']=='root'
    assert [r['segment'] for r in segments]==cfg['path']
    assert all(r['sandbox_id']==ids[0]['sandbox_id'] for r in segments)
    actions=[];guest=[];mismatches=[];offset=0
    for segment in cfg['path']:
        out=raw/'rollouts/backbone'/segment
        manifest=raw/'frozen-input/segments'/segment/'actions.tsv'
        assert sha(out/'actions.tsv')==sha(manifest)
        step_rows=rows(out/'steps.tsv');expected=cfg['expected_exit_codes'][segment]
        assert [r['step'] for r in step_rows]==[f'{i:03}' for i in range(1,len(expected)+1)]
        assert all(r['branch']=='backbone' and r['round']==segment for r in step_rows)
        for index,(row,code) in enumerate(zip(step_rows,expected),1):
            if int(row['exit_code'])!=code:
                mismatches.append(dict(segment=segment,step=row['step'],global_step=offset+index,
                                       expected_exit_code=code,actual_exit_code=int(row['exit_code'])))
        offset+=len(step_rows);actions.extend(step_rows);guest.extend(rows(out/'guest-memory.tsv'))
    assert len(actions)==35
    assert mismatches, 'this failure audit requires an exit-code mismatch'
    patch=raw/'rollouts/backbone'/cfg['path'][-1]/'patch.diff'
    reference=raw/'prepared-input/reference.patch'
    patch_equal=normalize_patch(patch.read_text())==normalize_patch(reference.read_text())
    peak=max(guest,key=lambda r:int(r['MemTotal_kib'])-int(r['MemFree_kib']))
    host=rows(raw/'host/memory-samples.tsv')
    host_peak=max(host,key=lambda r:int(r['cgroup_current_bytes']))
    events=json.loads((raw/'events.json').read_text())
    calls=[r for r in events if r['stage']=='controller-exec']
    downloads=[r for r in events if r['stage']=='controller-download']
    assert len(calls)==len(downloads)==5
    assert not any(r['stage']=='benchmark-pytest' for r in events)
    for key in ('owned_container_removed','mount_and_loop_removed','failed_fixture_retained',
                'original_firecracker_identities_unchanged','original_service_unchanged'):
        assert outer['cleanup'][key]
    return dict(status='FAIL',scope='offline audit of failed frozen replay; not task acceptance',
        task_id=cfg['task_id'],rollout='single frozen backbone',actions=35,
        action_hashes_and_order='PASS',exit_codes_matched=35-len(mismatches),exit_mismatches=mismatches,
        final_patch_matches_reference=patch_equal,final_patch_sha256=sha(patch),
        normalized_patch_sha256=hashlib.sha256(normalize_patch(patch.read_text()).encode()).hexdigest(),
        final_file_hashes='not captured by driver after strict exit-code failure; equal normalized patch checked separately',
        benchmark_tests='NOT_RUN',checkpoint_restore='NOT_RUN',physical_page_cache='NOT_MEASURED',
        monitoring_impact='NOT_VALIDATED',VMM_PSS='NOT_MEASURED',
        guest=dict(sample_count=len(guest),observed_peak_bytes=(int(peak['MemTotal_kib'])-int(peak['MemFree_kib']))*1024,
            components_at_peak_bytes={k:int(peak[k+'_kib'])*1024 for k in ('AnonPages','Cached','Buffers','Shmem')}),
        host=dict(sample_count=len(host),observed_peak_bytes=int(host_peak['cgroup_current_bytes']),
            components_at_peak_bytes={k:int(host_peak['cgroup_'+k+'_bytes']) for k in ('anon','file','kernel')}),
        timing=dict(controller_elapsed_ns=result['controller_elapsed_ns'],
            guest_action_sum_ns=sum(int(r['duration_ns']) for r in actions),
            cold_request_ns=next(r['duration_ns'] for r in events if r['stage']=='cold-start'),
            controller_exec_ns=[r['duration_ns'] for r in calls],controller_download_ns=[r['duration_ns'] for r in downloads]),
        config=dict(image=cfg['source_image'],cpu=cfg['guest_cpu'],memory_mib=cfg['guest_memory_mib']),
        cache='no manual host/guest cache drop; private mirrored OCI and converted cache; baseline probe precedes replay',
        cleanup=outer['cleanup'],storage={k:outer[k] for k in ('capacity_bytes','observed_volume_used_peak_bytes','observed_host_available_min_bytes')},
        measurement_scope=['Guest MemTotal-MemFree; KiB converted to bytes; sampled original runner windows.',
            'Cached is logical and Shmem overlaps it; not unique physical page cache. Slab is not sampled.',
            'Host temporary service cgroup includes VMM and charged image cache; excludes controller and separate RAM OCI preparation cache.',
            'Observed peaks are not exact instantaneous peaks. Guest and host are not added.',
            'Guest durations cover timeout/bash execution and timestamp instrumentation; nested times are not added to controller total.',
            'Exit-code mismatch remains a failure despite final patch equality. No benchmark results inferred from original self-tests.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('raw',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args();result=audit(args.raw.resolve())
    with args.output.open('x') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('status=FAIL audit=RECORDED matched='+str(result['exit_codes_matched'])+'/35 output='+str(args.output))
