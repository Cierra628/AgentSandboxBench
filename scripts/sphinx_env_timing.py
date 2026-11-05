"""Fixed eleven-action prefix and output checks for the separate environment timing diagnostic."""
import csv
import json
from pathlib import Path

from guest_sphinx_env_timing import COMMAND,PARTS


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def plan(workload,cfg):
    first=rows(workload/'segments/read_repo/actions.tsv')
    trace=rows(workload/'segments/trace_cpp_parser/actions.tsv')
    assert len(first)==6 and len(trace)==7 and trace[5]['step']=='006'
    assert trace[5]['command']==COMMAND=='; '.join(command for _,command in PARTS)
    return dict(prefix_actions=11,target_global_index=12,target_segment='trace_cpp_parser',target_step='006',
        target_command=COMMAND,expected_target_exit=cfg['expected_exit_codes']['trace_cpp_parser'][5],
        scope='Shortest contiguous original prefix before first target use; no claim that all eleven actions are functional dependencies')


def prefix_runner(text):
    marker='  [[ $step == step ]] && continue\n'
    assert text.count(marker)==1
    return text.replace(marker,marker+'  if [[ $round == trace_cpp_parser && $step == 006 ]]; then break; fi\n',1)


def check_prefix(raw,reference,cfg):
    directory=raw/'rollouts/backbone/trace_cpp_parser'
    actual=rows(directory/'steps.tsv')
    assert [r['step'] for r in actual]==['001','002','003','004','005']
    assert [int(r['exit_code']) for r in actual]==cfg['expected_exit_codes']['trace_cpp_parser'][:5]
    assert all(r['branch']=='backbone' and r['round']=='trace_cpp_parser' and int(r['duration_ns'])>=0 for r in actual)
    expected=reference/'rollouts/backbone/trace_cpp_parser'
    assert (directory/'actions.tsv').read_bytes()==(raw/'frozen-input/segments/trace_cpp_parser/actions.tsv').read_bytes()==(expected/'actions.tsv').read_bytes()
    for kind in ('stdout','stderr'):
        assert sorted(p.name for p in (directory/kind).glob('*.log'))==[f'{i:03}.log' for i in range(1,6)]
        for i in range(1,6):assert (directory/kind/f'{i:03}.log').read_bytes()==(expected/kind/f'{i:03}.log').read_bytes()
    assert (directory/'patch.diff').read_bytes()==(expected/'patch.diff').read_bytes()
    assert not rows(directory/'guest-memory.tsv') and not rows(directory/'guest-vmstat.tsv')
    assert not (directory/'collector-status.json').exists()
    return dict(action_count=11,trace_actions=5,target_not_previously_executed=True,
        scope='Six read_repo actions validated by the existing prefix runner, then these five original trace actions')


def check_capture(directory,reference,cfg,mode):
    value=json.loads((directory/'result.json').read_text())
    assert value['status']=='CAPTURED' and value['mode']==mode
    assert value['exit_code']==cfg['expected_exit_codes']['trace_cpp_parser'][5]
    expected=(('whole',COMMAND),) if mode=='whole' else PARTS
    assert [(r['name'],r['command']) for r in value['parts']]==list(expected)
    assert value['shell_invocations']==len(expected) and value['process_scans'] is False
    assert value['parts_elapsed_sum_ns']==sum(r['elapsed_ns'] for r in value['parts'])
    assert value['between_parts_ns']>=0 and value['elapsed_ns']==value['parts_elapsed_sum_ns']+value['between_parts_ns']
    for kind in ('stdout','stderr'):
        content=b''.join((directory/name/kind).read_bytes() for name,_ in expected)
        assert content==(directory/kind).read_bytes()==(reference/'rollouts/backbone/trace_cpp_parser'/kind/'006.log').read_bytes()
    return value


def report(raw,output):
    """Audit only the new first-use diagnostic, without scanning/hashing all raw files."""
    from sphinx7590_inputs import ROOT,validate
    from task_metrics_prefix import check_prefix as first_six,without_sampling
    raw=Path(raw).resolve();output=Path(output).resolve()
    assert raw.is_relative_to(ROOT/'.artifacts/storage-isolated-service')
    assert output.is_relative_to(ROOT/'.artifacts') and not output.is_relative_to(raw) and not raw.is_relative_to(output)
    result=json.loads((raw/'result.json').read_text());outer=json.loads((raw.parent/'result.json').read_text())
    assert result['status']==outer['status']=='PASS' and result['command_diagnostic']
    mode=result['env_command_timing'];assert mode in ('whole','split')
    assert result['checks']['env_command_timing_and_original_prefix']=='PASS'
    assert result['cleanup']['service_sandbox_list_empty'] and result['official_task_status']=='NOT_RUN'
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
        'original_firecracker_identities_unchanged','original_service_unchanged'))
    cfg,_,_,_=validate(raw/'prepared-input')
    reference=ROOT/'.artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone'
    prefix=first_six(raw,cfg,reference,False,True)
    assert prefix['strict_prefix_status']==result['strict_prefix_status']
    assert check_prefix(raw,reference,cfg)==result['replay']
    assert without_sampling((raw/'frozen-input/guest-runner.original.sh').read_text())==(raw/'env-prefix-base.sh').read_text()
    assert prefix_runner((raw/'env-prefix-base.sh').read_text())==(raw/'frozen-input/guest-runner.sh').read_text()
    for before,after in (('task-before.json','task-after.json'),('task-oracle-before.json','task-oracle-after.json')):
        assert json.loads((raw/before).read_text())==json.loads((raw/after).read_text())
    value=check_capture(raw/'asb-command/target',reference,cfg,mode)
    assert value==result['env_timing']
    summary=dict(status='PASS',source_directory=str(raw.relative_to(ROOT)),mode=mode,prefix_actions=11,target_invocations=1,
        strict_prefix_status=result['strict_prefix_status'],strict_replay_status='NOT_RUN',official_task_status='NOT_RUN',
        timing=value,scope=result['scope'])
    output.mkdir(parents=True,exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (output/'timing.tsv').open('x') as stream:
        fields=['name','exit_code','elapsed_ns','child_user_seconds','child_system_seconds','child_minor_faults','child_major_faults']
        writer=csv.DictWriter(stream,fieldnames=fields,delimiter='\t',extrasaction='ignore')
        writer.writeheader();writer.writerows(value['parts'])
    print('status=PASS result_dir='+str(output))
    return summary
