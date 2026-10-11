"""Offline, blocked label-permutation sensitivity for the three identical off controls."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics

from run_trenvx_persistent_comparison import audit, METRICS

LABELS=('control_A','control_B','off')
TOLERANCE=1e-14


def distribution(triples):
    if not 1 <= len(triples) <= 5:
        raise ValueError('exact enumeration supports one through five groups')
    if any(len(row)!=3 or any(not math.isfinite(value) or value<=0 for value in row) for row in triples):
        raise ValueError('each group requires three positive finite measurements')
    observed=statistics.mean(math.log(row[1]/row[0]) for row in triples)
    choices=[tuple(math.log(b/a) for a,b,unused in itertools.permutations(row)) for row in triples]
    values=[statistics.mean(row) for row in itertools.product(*choices)]
    assert len(values)==6**len(triples)
    return observed,values


def summarize(triples):
    observed,values=distribution(triples)
    extreme=sum(abs(value)>=abs(observed)-TOLERANCE for value in values)
    logs=[math.log(row[1]/row[0]) for row in triples]
    signs=[statistics.mean(value*sign for value,sign in zip(logs,assignment))
           for assignment in itertools.product((-1,1),repeat=len(logs))]
    sign_extreme=sum(abs(value)>=abs(observed)-TOLERANCE for value in signs)
    return dict(observed_mean_log_ratio=observed,observed_geometric_mean_percent=100*math.expm1(observed),
        permutation_count=len(values),extreme_count=extreme,two_sided_tail_fraction=extreme/len(values),
        equality_tolerance_log=TOLERANCE,
        null_range_percent=[100*math.expm1(min(values)),100*math.expm1(max(values))],
        paired_sign_flip=dict(permutation_count=len(signs),extreme_count=sign_extreme,
            two_sided_tail_fraction=sign_extreme/len(signs)))


def report(batch):
    batch=batch.resolve(strict=True)
    checked=audit(batch)
    assert checked['protocol']['stage']=='balanced' and checked['protocol']['group_count']==5
    assert checked['run_count']==60
    saved=json.loads((batch/'result.json').read_text())
    identities=[];sources=[];initial=set();phase_files=0
    for group in saved['groups']:
        for row in group:
            raw=Path(row['raw_dir'])
            phase_files+=int((raw/'replay/runner-phases.tsv').exists())
            if row['label'] not in LABELS:continue
            cfg=json.loads((raw/'config.json').read_text())
            replay=json.loads((raw/'replay-config.json').read_text())
            assert not cfg['guest_memory_sampling'] and cfg['host_sample_interval_seconds']==0
            assert cfg['guest_collector']=='original' and cfg['record_phases']
            digest=hashlib.sha256((raw/'inputs/runner.sh').read_bytes()).hexdigest()
            assert digest==replay['runner_sha256']
            identities.append(dict(environment=replay['environment'],cwd=replay['cwd'],session_semantics=replay['session_semantics'],
                                   runner_sha256=digest,action_manifest_sha256=replay['action_manifest_sha256']))
            initial.add(tuple((raw/'replay'/name).read_text().strip() for name in ('baseline-tree.txt','original-head.txt')))
            sources.append(dict(group=row['group'],position=row['position'],label=row['label'],run_id=row['run_id']))
    assert len(identities)==30 and len(initial)==1 and all(value==identities[0] for value in identities)
    metrics={}
    for metric in METRICS:
        group_means=[{label:statistics.mean(row[metric] for row in group if row['label']==label) for label in LABELS}
                     for group in saved['groups']]
        value=summarize([tuple(row[label] for label in LABELS) for row in group_means])
        planned=checked['analysis'][metric]['control']
        assert math.isclose(value['observed_geometric_mean_percent'],planned['geometric_mean_percent'],abs_tol=1e-12)
        value.update(group_means_ns=group_means,planned_t95=planned)
        metrics[metric]=value
    tree,head=next(iter(initial))
    return dict(status='PASS',meaning='input audit and exact calculation pass; no causal or acceptance verdict',
        batch_id=batch.name,batch_result_sha256=hashlib.sha256((batch/'result.json').read_bytes()).hexdigest(),
        reporter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        run_count_audited=60,off_run_count=30,group_count=5,source_runs=sources,off_identity=identities[0],
        initial_git_tree=tree,initial_head=head,
        design=dict(unit='within-group mirrored pair mean in ns; mean log(B/A) across five groups',
            conditioning='keep all on-condition positions fixed; permute the three off labels among their three mirrored pairs within each group',
            assignment_count='(3!)^5 = 7776; each schedule assignment counts, even if values tie',
            hypothesis='off label does not alter runtime parameters; conditional uniform label assignment per randomized group',
            two_sided_rule='count abs(null mean log ratio) >= abs(observed mean log ratio), including ties; exhaustive, no Monte Carlo +1 correction',
            secondary_sign_flip='swap only A/B within each group: 2^5 = 32 assignments'),
        metrics=metrics,
        measurement_gap=dict(controller_phase_timestamps='present in all 60 configurations',
            guest_runner_phase_files=phase_files,
            consequence='no recorded initial Git preparation boundaries or dual-clock runner envelope in this batch; duration differences are not pure preparation or RPC time'),
        limitations=['Post-hoc sensitivity; original planned t95 estimates and public evidence are unchanged',
            'Three metrics inspected, no multiplicity adjustment and no performance threshold',
            'Tail fraction is conditional on the stated randomization model; not probability that the null is true',
            'Non-small tail fraction is not proof of equality, no monitoring impact, or eliminated bias',
            'Only off-label assignment tested; no new significance claim about host/guest/both effects',
            'No new VM run, checkpoint, physical-page scan, or resource-policy change'])


def figure(value,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(9,8),constrained_layout=True)
    titles=('Controller runner call','Guest action duration sum','Controller including cleanup')
    for axis,metric,title in zip(axes,METRICS,titles):
        item=value['metrics'][metric]
        observed,values=distribution([tuple(row[label] for label in LABELS) for row in item['group_means_ns']])
        axis.hist([100*math.expm1(v) for v in values],bins=70,color='#5485a2',alpha=.85)
        for boundary in (-abs(observed),abs(observed)):
            axis.axvline(100*math.expm1(boundary),color='#c34f31',linewidth=1.2)
        axis.set(title=f"{title}: two-sided tail fraction = {item['two_sided_tail_fraction']:.4f}",
                 xlabel='Mean log-ratio converted to geometric difference (%)',ylabel='Assignment count')
    fig.savefig(path);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--figure',type=Path)
    args=parser.parse_args()
    if args.figure and args.figure.exists():parser.error('figure already exists')
    value=report(args.batch)
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    if args.figure:figure(value,args.figure)
    print(f"status={value['status']} output={args.output}")
