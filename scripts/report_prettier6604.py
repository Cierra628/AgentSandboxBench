"""Read-only audit of one frozen backbone; write into a new report directory."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
from prettier6604_inputs import validate
from prettier6604_boundary import verify as verify_boundary
from audit_prettier6604_official import reference, signature


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def audit(raw, cfg, official_audit=None):
    result = json.loads((raw/'result.json').read_text())
    assert result['status'] in ('PASS','FAIL') and result['task_id'] == cfg['task_id']
    required = ('smoke_and_unfixed_oracle','frozen_order_hashes_exit_codes_patch_and_key_outputs',
                'independent_properties')
    assert all(result['checks'].get(k) == 'PASS' for k in required)
    assert result['cleanup'].get('sandbox_removed') and result['cleanup'].get('monitor_stopped')
    if (raw/'sampling-variant.json').exists():
        from sandbox_runtime_monitor import boundary_runner
        variant=json.loads((raw/'sampling-variant.json').read_text())
        original=raw/'frozen-input/guest-runner.original.sh'
        runner=raw/'frozen-input/guest-runner.sh'
        assert hashlib.sha256(original.read_bytes()).hexdigest()==cfg['runner_sha256']==variant['original_runner_sha256']
        assert hashlib.sha256(runner.read_bytes()).hexdigest()==variant['executed_runner_sha256']
        assert runner.read_text()==boundary_runner(original.read_text(),guest_phases=variant.get('phase_updates')=='guest runner')
    guest=[];tools=[]
    for segment in cfg['path']:
        root=raw/'rollouts/backbone'/segment
        assert hashlib.sha256((root/'actions.tsv').read_bytes()).hexdigest()==cfg['manifest_sha256'][segment]
        actions=rows(root/'steps.tsv')
        assert [a['step'] for a in actions]==[f'{i:03}' for i in range(1,len(actions)+1)]
        assert [int(a['exit_code']) for a in actions]==cfg['expected_exit_codes'][segment]
        tools.extend(dict(segment=segment,**a) for a in actions)
        guest.extend(rows(root/'guest-memory.tsv'))
    assert len(tools)==29 and result.get('action_count',29)==29
    patch=raw/'rollouts/backbone'/cfg['path'][-1]/'patch.diff'
    assert hashlib.sha256(patch.read_bytes()).hexdigest()==cfg['reference_patch_sha256']
    official=raw/'official-task-result.json'
    if official.exists():
        official_result=json.loads(official.read_text())
    else:
        assert official_audit, 'official test evidence required even for failed report'
        official_result=json.loads(official_audit.read_text())
        assert official_result['source_sha256']==hashlib.sha256((raw/'official-task-tests.stdout').read_bytes()).hexdigest()
    assert official_result['status'] in ('PASS','FAIL')
    assert json.loads((raw/'independent-check.json').read_text())['status']=='PASS'
    host=rows(raw/'host/memory-samples.tsv');assert guest and host
    peak=max(guest,key=lambda r:int(r['MemTotal_kib'])-int(r['MemFree_kib']))
    used=(int(peak['MemTotal_kib'])-int(peak['MemFree_kib']))*1024
    parts={k:int(peak[k+'_kib'])*1024 for k in ('AnonPages','Cached','Buffers','Shmem')}
    hp=max(host,key=lambda r:int(r['cgroup_current_bytes']))
    events=json.loads((raw/'events.json').read_text())
    assert all(e['exit_code']==0 for e in events)
    status='PASS' if result['status']=='PASS' and official_result['status']=='PASS' and result['checks'].get('official_task_tests')=='PASS' else 'FAIL'
    restored=result.get('checkpoint_restore')=='PASS'
    if restored:
        for key in ('checkpoint_interface','restored_progress_and_files','process_continuity','official_outcome_matches_full_replay'):
            assert result['checks'].get(key)=='PASS'
        assert result['status']=='PASS' and result['cleanup'].get('snapshot_removed')
        verify_boundary(json.loads((raw/'boundary-before.json').read_text()),json.loads((raw/'boundary-after.json').read_text()))
        processes=json.loads((raw/'process-continuity.json').read_text());before,after=processes['before'],processes['after']
        assert all(before[k]==after[k] for k in ('pid','start_ticks','boot_id')) and int(after['count'])>int(before['count'])
        assert signature(official_result)==signature(reference(raw/'expected-official-audit.json'))
        segments=rows(raw/'segments.tsv')
        assert [r['segment'] for r in segments]==cfg['path']
        assert len({r['sandbox_id'] for r in segments[:4]})==1
        assert segments[-1]['sandbox_id']!=segments[0]['sandbox_id']
        status='PASS' # Platform restore passed; task failure is explicitly separate.
    return dict(status=status,replay_consistency='PASS',official_task_status=official_result['status'],task_id=cfg['task_id'],rollout='one frozen backbone',actions=29,
        guest=dict(sample_count=len(guest),observed_peak_bytes=used,
                   peak_sample_timestamp_ns=int(peak['timestamp_ns']),components_at_peak_bytes=parts,
                   components_at_peak_percent={k:100*v/used for k,v in parts.items()},
                   note='MemTotal-MemFree; counters from /proc/meminfo, KiB converted to bytes. Shmem overlaps Cached; components are not added. Slab not collected. Runner windows only.'),
        host=dict(sample_count=len(host),observed_peak_bytes=int(hp['cgroup_current_bytes']),
                  components_at_peak_bytes={k:int(hp['cgroup_'+k+'_bytes']) for k in ('anon','file','kernel')},
                  note='Temporary service cgroup, includes cache and VMM. Does not include original service. VMM PSS not measured; zero columns are placeholders.'),
        timing=dict(guest_action_sum_ns=sum(int(t['duration_ns']) for t in tools),
                    controller_elapsed_ns=result['controller_elapsed_ns'],stages=events,
                    note='ns; guest actions include frozen shell and runner timing boundary overhead. Stage totals overlap segment totals; do not add them as end-to-end time.'),
        page_cache=dict(physical_bytes='NOT_MEASURED',guest_logical_counter='Cached_kib',
                        cache_control='no manual host or guest cache drop',cold_state='temporary service reuses immutable base layers; missing task layers prepared at creation'),
        monitoring_impact='NOT_VALIDATED',checkpoint_restore='PASS' if restored else 'NOT_RUN',
        restore_semantics='complete VM state; process continuity verified' if restored else 'not exercised'),guest,host


def report(raw, output, official_audit=None):
    assert not output.resolve().is_relative_to(raw.resolve()), 'report must be outside raw'
    inputs=[p for p in raw.rglob('*') if p.is_file()]
    hashes={str(p.relative_to(raw)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    cfg,_,_=validate()
    summary,guest,host=audit(raw,cfg,official_audit)
    # Only write after audit, and never overwrite an existing report or raw file.
    output.mkdir(parents=True,exist_ok=False)
    from report_sandbox_runtime import write_report
    summary['runtime_metrics']=write_report(raw,output,host)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    assert hashes=={str(p.relative_to(raw)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    (output/'input-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    shutil.copy2(Path(__file__),output/'report-source.py')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--official-audit',type=Path,help='Strict offline audit if original grading crashed')
    args=parser.parse_args();summary=report(args.raw,args.output,args.official_audit)
    print('status='+summary['status']+' result_dir='+str(args.output))
    raise SystemExit(0 if summary['status']=='PASS' else 1)
