"""Audit an isolated find diagnosis; never award full-trajectory acceptance."""
import argparse
import hashlib
import json
from pathlib import Path

from sphinx7590_inputs import ROOT,sha,validate
from task_metrics_prefix import without_sampling
from run_sphinx_command_diagnostic import check_trace


def capture_summary(rows,digest,stdout_digest,stderr_digest):
    for row in rows:
        assert row['status']=='CAPTURED' and row['command_sha256']==digest
        for key in ('original_stdout','diagnostic_stdout'):
            assert hashlib.sha256(row[key].encode()).hexdigest()==stdout_digest
        for key in ('original_stderr','diagnostic_stderr'):
            assert hashlib.sha256(row[key].encode()).hexdigest()==stderr_digest
        assert len(row['pipeline_exit_codes'])==2
    return dict(captures=len(rows),original_exit_codes=[r['original_exit_code'] for r in rows],
        diagnostic_pipeline_exit_codes=[r['pipeline_exit_codes'] for r in rows],
        captures_with_find_error=sum(bool(r['find_stderr']) for r in rows),
        errors=[dict(capture=i,find_stderr=r['find_stderr'],processes_after_find=r['error_processes_after_find'])
                for i,r in enumerate(rows,1) if r['find_stderr']],
        scope='each capture runs original and diagnostic commands consecutively; their exit codes may differ')


def report(raw,output):
    raw=raw.resolve();output=output.resolve()
    assert raw.is_relative_to(ROOT/'.artifacts/storage-isolated-service')
    assert output.is_relative_to(ROOT/'.artifacts') and not output.is_relative_to(raw) and not raw.is_relative_to(output)
    reference=ROOT/'.artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone'
    def hashes():return {str(p.relative_to(ROOT)):sha(p) for directory in (raw,reference) for p in sorted(directory.rglob('*')) if p.is_file()}
    before=hashes();outer_sha=sha(raw.parent/'result.json')
    value=json.loads((raw/'result.json').read_text());outer=json.loads((raw.parent/'result.json').read_text())
    assert value['status']==outer['status']=='PASS' and value['find_diagnostic'] and value['monitor_mode']=='off'
    assert value['cleanup']['service_sandbox_list_empty']
    assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed',
        'successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged'))
    cfg,_,samples,names=validate(raw/'prepared-input')
    assert check_trace(raw,reference,cfg,samples,names,True)==value['replay']
    assert (raw/'frozen-input/guest-runner.sh').read_text()==without_sampling((raw/'frozen-input/guest-runner.original.sh').read_text())
    assert sha(raw/'frozen-input/guest-runner.original.sh')==cfg['input_sha256']['guest-runner.sh']
    for key in ('task','task-oracle'):
        assert json.loads((raw/(key+'-before.json')).read_text())==json.loads((raw/(key+'-after.json')).read_text())
    assert value['checks']['find_captures_and_source_unchanged']=='PASS'
    digest=sha(raw/'find-command.txt')
    assert digest=='1f6e4d89a09212e18cf773594c96e46f5aaab4bf0d55b3f27d0e17086cc800a5'
    stdout_digest=sha(reference/'rollouts/backbone/trace_cpp_parser/stdout/005.log')
    stderr_digest=sha(reference/'rollouts/backbone/trace_cpp_parser/stderr/005.log')
    rows=value['find_captures'];assert len(rows)==8
    for i,row in enumerate(rows,1):assert json.loads((raw/f'find-capture-{i:02}.json').read_text())==row
    summary=dict(status='PASS',scope='short diagnostic audit only; no full replay, task tests or monitoring overhead acceptance',
        source_directory=str(raw.relative_to(ROOT)),source_image=cfg['source_image'],cpu=value['cpu'],memory_mib=value['memory_mib'],
        monitor_mode='off',command_sha256=digest,original_prefix=value['replay'],
        quiet_repeats=capture_summary(rows,digest,stdout_digest,stderr_digest),controlled_process_test=None,
        raw_and_reference_files=len(before),raw_and_reference_hashes_unchanged=True,cleanup=outer['cleanup'],
        volume_capacity_bytes=outer['capacity_bytes'],observed_host_available_min_bytes=outer['observed_host_available_min_bytes'],
        original_failure_cause='NOT_ESTABLISHED',full_trajectory='NOT_RUN',independent_task_tests='NOT_RUN')
    if 'find_transient_control' in value:
        control=value['find_transient_control']
        assert control==json.loads((raw/'find-transient-control.json').read_text())
        assert control['status']=='CAPTURED' and control['command_sha256']==digest and control['owned_children_reaped']
        assert [len(control[k]) for k in ('quiet_before','active','quiet_after')]==[3,8,3]
        summary['controlled_process_test']={k:control[k] for k in ('controller_pid','workers','child_command','wait_delay_seconds','owned_child_count','owned_children_reaped','scope')}
        summary['controlled_process_test'].update({k:capture_summary(control[k],digest,stdout_digest,stderr_digest) for k in ('quiet_before','active','quiet_after')})
    assert hashes()==before and sha(raw.parent/'result.json')==outer_sha
    output.mkdir(parents=True,exist_ok=False)
    (output/'input-sha256.json').write_text(json.dumps(dict(files=before,outer_result_sha256=outer_sha),indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('status=PASS result_dir='+str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report(args.raw,args.output)
