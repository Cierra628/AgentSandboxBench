"""Recount named-file evidence without VM launches or exposing raw addresses."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from named_guest_file_physical import recount
from measure_file_physical import audit_run

ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(path.read_text())


def agentenv(directory):
    cfg=read(directory/'config.json');run=read(directory/'result.json')
    assert run['status']=='PASS' and all(value=='PASS' for value in run['checks'].values())
    for name,digest in cfg['source_sha256'].items():
        assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
    sid=(directory/'sandbox-id.txt').read_text().strip();p=directory/'named-files'
    value=recount(read(p/'before.json'),read(p/'after.json'),read(p/'pages.json'),sid)
    saved=read(p/'result.json');assert all(saved[key]==v for key,v in value.items())
    receipt=read(p/'guest-result.json');assert receipt==saved['guest_receipt']
    audits=[path for path in (directory/'named-offline-audit.json',directory/'offline-audit.json') if path.exists()]
    assert any(read(path).get('named_files')==value and read(path)['status']=='PASS' for path in audits)
    return dict(status=value['status'],raw_dir=str(directory.relative_to(ROOT)),
        task='Prettier #14400; frozen full 26 actions',cpu=cfg['cpu'],memory_mib=cfg['memory_mib'],
        image=cfg['image'],file_accounting=value,
        file_hashes=[dict(path=row['path'],sha256=row['sha256']) for row in receipt['files']],
        latency_ns={key:saved[key] for key in ('controller_elapsed_ns','host_read_elapsed_ns',
            'guest_before_elapsed_ns','guest_after_elapsed_ns')},
        correctness=read(directory/'exp1-correctness.json'),cleanup=run['checks']['cleanup'])


def trenvx_success(directory):
    cfg=read(directory/'config.json');run=read(directory/'result.json')
    assert run['status']=='PASS' and run['full_trajectory_verified']
    assert all(value=='PASS' for value in run['checks'].values())
    assert run['host_cleanup']['cgroup_removed'] and run['host_cleanup']['owned_loop_backings']==[]
    assert cfg['checkpoint_after']=='022' and cfg['file_physical_diagnostic']
    for name,key in [('named-files-input.json','file_physical_manifest_sha256'),
                     ('guest_layer_file_state.py','guest_layer_state_sha256')]:
        assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==cfg[key]
    declared=read(directory/'named-files-input.json')['files']
    stages={};hashes=None
    for label in ('restored-before-source-read','after-continuation'):
        p=directory/'file-physical'/label
        checked=audit_run(p);assert checked['measurement_status']=='PASS'
        proof=read(p/'guest-layer-proof.json')
        before,after=proof['before'],proof['after']
        assert before['status']==after['status']=='PASS'
        assert not before['content_reads'] and after['content_reads']
        assert len(before['files'])==len(after['files'])==len(declared)
        observed=[]
        for item,a,b in zip(declared,before['files'],after['files']):
            assert {key:a[key] for key in ('path','origin')}==item
            assert all(a[key]==b[key] for key in ('path','origin','candidate','visible','underlying'))
            assert b['visible_sha256']==b['candidate_sha256']
            observed.append(dict(path=b['path'],origin=b['origin'],bytes=b['visible']['st_size'],
                                 sha256=b['visible_sha256']))
        if hashes is None:hashes=observed
        assert hashes==observed, 'declared file contents changed across continuation'
        saved=read(p/'result.json')
        stages[label]=dict(status='PASS',offline_audit=checked,
            scan_elapsed_ns=saved['scan_elapsed_ns'],upper_file_content=saved['upper_file_content'])
    archive=read(directory/'archive-manifest.json')
    assert archive['source_removed'] and archive['gzip_test']==archive['tar_compare']=='PASS'
    assert archive['archive_sha256']==run['data_retention']['archive_sha256']
    assert (directory/'data.tar.gz').stat().st_size==archive['archive_bytes']
    correctness=read(directory/'correctness.json')
    assert correctness['patch_reference_match']=='PASS'
    official=read(directory/'official-task-result.json')
    assert official['status']=='PASS'
    guards=[json.loads(line) for line in (directory/'disk-guard.jsonl').read_text().splitlines()]
    assert all(row['status']=='PASS' for row in guards)
    events=[json.loads(line) for line in (directory/'diagnostic-events.jsonl').read_text().splitlines()]
    from report_memory_metrics import report as memory_report
    metrics=memory_report(dict(backend='trenvx',mode='checkpoint-022-diagnostic',raw=str(directory)))
    return dict(status='PASS',raw_dir=str(directory.relative_to(ROOT)),files=hashes,stages=stages,
        cpu=cfg['cpu'],memory_mib=cfg['memory_mib'],seed_sha256=cfg['seed_sha256'],
        restore_semantics=run['restore_semantics'],correctness=correctness,official_task=official,
        phase_elapsed_ns={row['phase']:row['elapsed_ns'] for row in events},
        checks=run['checks'],cleanup=run['host_cleanup'],archive=archive,
        memory_metrics={key:metrics[key] for key in ('guest_composition','host_memory',
            'phase_breakdown','named_file_mapping','monitoring_impact')},
        observed_min_free_bytes=min(row['observed_min_free_bytes'] for row in guards),
        measurement_scope='declared file data in VMM mappings; not all backing-cache residency',
        cache_intervention='full declared files read for origin/content checks after first scan, before continuation')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agentenv',type=Path,nargs='+',required=True)
    parser.add_argument('--trenvx',type=Path,required=True)
    parser.add_argument('--trenvx-success',type=Path,
                        help='new successful follow-up; preserves the earlier failure evidence')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();trenv=args.trenvx.resolve();failed=read(trenv/'result.json')
    # This report records the failed integration, never promotes checkpoint alone.
    assert failed['status']=='FAIL' and not failed['full_trajectory_verified']
    failure=read(trenv/'layer-state-before-restored-before-source-read.json')
    assert failure['exit_code']==1 and 'trusted.overlay.impure' in failure['stderr']
    prefix=list(csv.DictReader((trenv/'prefix/steps.tsv').open(),delimiter='\t'))
    assert [row['step'] for row in prefix]==[f'{i:03d}' for i in range(1,23)]
    assert {row['step']:int(row['exit_code']) for row in prefix if int(row['exit_code'])}=={'008':127,'016':127}
    guard=[json.loads(line) for line in (trenv/'disk-guard.jsonl').read_text().splitlines()]
    result=dict(status='PARTIAL',initial_acceptance_item=3,
        agentenv=[agentenv(p.resolve()) for p in args.agentenv],
        trenvx=dict(status='FAIL',raw_dir=str(trenv.relative_to(ROOT)),completed_steps=22,
            first_failure='file-origin check rejects trusted.overlay.impure on upper root',
            file_measurement='NOT_RUN',continuation='NOT_RUN',official_task_tests='NOT_RUN',
            checks=failed['checks'],host_cleanup=failed['host_cleanup'],data_retention=failed['data_retention'],
            observed_min_free_bytes=min(row['observed_min_free_bytes'] for row in guard),
            initial_minimum_free_bytes=read(trenv/'config.json')['minimum_free_bytes'],
            archive_bytes=(trenv/'data.tar.gz').stat().st_size),
        fix=dict(status='UNIT_TESTS_PASS; REAL_VM_NOT_RETESTED',
            source='https://github.com/gregkh/linux/blob/v6.1.134/fs/overlayfs/dir.c#L1183',
            change='accept impure=y on directories; still reject upper file shadows, opaque, redirect, metacopy and unknown values'),
        limitations=['AgentENV results cover a declared resident file subset after file-LRU analysis, not the complete task',
            'Two successful runs are integration smoke, not repeats for variance or a cross-backend comparison',
            'No multi-VM sharing measurement, upper-layer classification or full host cache accounting',
            'Diagnostic read/transfer times are not low-overhead monitoring estimates; prior monitoring intervals remain separately scoped'])
    if args.trenvx_success:
        result['trenvx_followup']=trenvx_success(args.trenvx_success.resolve())
        result['fix']['status']='REAL_VM_PASS; earlier failed run retained separately'
        hashes={row['path']:row['sha256'] for row in result['trenvx_followup']['files']}
        for run in result['agentenv']:
            assert all(hashes[row['path']]==row['sha256'] for row in run['file_hashes'])
        result['declared_file_hashes_equal_across_backends']=True
        result['report_source_sha256']={name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
            for name in ('report_named_file_metrics.py','report_memory_metrics.py',
                         'audit_exp1_metrics.py','measure_file_physical.py','memory_composition.py')}
    with args.output.open('x') as stream:stream.write(json.dumps(result,indent=2)+'\n')
    print(f'status=PARTIAL agentenv=PASS trenvx_prior=FAIL trenvx_followup={"PASS" if args.trenvx_success else "NOT_RUN"} output={args.output}')


if __name__=='__main__':main()
