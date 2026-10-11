"""Recompute a public summary of the persistent-sampling acceptance evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from exp1_physical_workload import file_lru_accounting
from recheck_file_lru import report as type_report
from report_memory_metrics import report
from report_persistent_metrics import audit

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def memory_summary(item):
    value=report(item)
    assert value['guest_composition']['status']=='PASS'
    return {key:value[key] for key in ('run_id','backend','raw_dir','guest_memory','host_memory',
        'phase_breakdown','final_checks','cleanup','guest_collector')} | dict(
        observed_peak=value['guest_composition']['composition_at_observed_peak'],
        sampling_mode=value['guest_composition']['sampling_mode'],
        time_scope=value['guest_composition']['time_scope'],
        collector_segments=value.get('guest_collector_segments',[]))


def relative(value):
    if isinstance(value,str):return value.replace(str(ROOT)+'/', '')
    if isinstance(value,list):return [relative(v) for v in value]
    if isinstance(value,dict):return {relative(k):relative(v) for k,v in value.items()}
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--checkpoint-raw',type=Path,required=True)
    parser.add_argument('--checkpoint-audit',type=Path,required=True)
    parser.add_argument('--trenvx-raw',type=Path,required=True)
    parser.add_argument('--physical',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    comparison=audit(args.comparison.resolve())
    source=read(args.comparison/'result.json')
    assert comparison['replays']==60 and len(source['groups'])==5
    cases={}
    for case in ('off','host','guest','both','control_A','control_B'):
        rows=[row for group in source['groups'] for row in group if row['label']==case]
        cases[case]=dict(replays=len(rows),mean_seconds={metric:statistics.mean(row[metric] for row in rows)/1e9
            for metric in comparison['analysis']},
            guest_samples=sum(row['guest_sample_count'] for row in rows),
            host_samples=sum(row['sample_count'] for row in rows))
    checkpoint=memory_summary(dict(backend='agentenv',mode='checkpoint-013',
        raw=str(args.checkpoint_raw.resolve()),audit=str(args.checkpoint_audit.resolve())))
    trenvx=memory_summary(dict(backend='trenvx',mode='full-diagnostic',raw=str(args.trenvx_raw.resolve())))
    physical=args.physical.resolve()
    assert read(physical/'result.json')['status']==read(physical/'offline-audit.json')['status']=='PASS'
    for name,digest in read(physical/'config.json')['source_sha256'].items():
        assert hashlib.sha256((physical/name).read_bytes()).hexdigest()==digest
    checked=type_report(physical);assert checked==read(physical/'file-type-recheck.json')
    accounted=file_lru_accounting(physical);assert accounted==read(physical/'file-lru-accounting.json')
    result=dict(status='PARTIAL',initial_acceptance_item=3,
        scope='Prettier #14400 only; not the three PPT tasks; functional success does not prove zero overhead',
        comparison=dict(status='PASS',raw_dir=str(args.comparison.resolve()),
            protocol=comparison['protocol'],identity=comparison['identity'],analysis=comparison['analysis'],
            cases=cases,memory=comparison['memory'],limitations=comparison['limitations'],
            source_hashes=read(args.comparison/'source-hashes.json')),
        agentenv_checkpoint=checkpoint,trenvx_full=trenvx,
        physical=dict(raw_dir=str(physical),type_recheck=checked,accounting=accounted,
            config=read(physical/'config.json')),
        limitations=['Full physical task-file attribution, upper, host unmapped cache and multi-VM sharing remain incomplete',
            'Only the comparison collector snapshot has a matched overhead estimate; checkpoint/TrEnv-X use a segment-receipt extension',
            'TrEnv-X persistent guest monitoring and checkpoint/restore lifecycle impact have no matched controls',
            'Guest sampling stops between runner calls and during checkpoint/restore; asynchronous samples may miss short tools',
            'Guest counters, host service cgroup and VMM PSS are separate ranges; never sum them',
            'Host cache retained, no global cache flush; CPU policies restored after every comparison trial'])
    with args.output.open('x') as stream:stream.write(json.dumps(relative(result),indent=2)+'\n')
    print(f'status=PARTIAL comparison=PASS checkpoint=PASS trenvx=PASS output={args.output}')


if __name__=='__main__':main()
