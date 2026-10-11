"""Recount persistent sampler runs and export scoped, address-free evidence."""
import argparse
import hashlib
import csv
import statistics
import json
from pathlib import Path

from report_memory_metrics import report as memory_report
from run_trenvx_persistent_comparison import audit, METRICS
from run_exp1_monitor_balanced import estimate


def report(smoke,balanced=None):
    smoke=smoke.resolve(strict=True);short=audit(smoke)
    assert short['protocol']['stage']=='smoke' and short['run_count']==8
    output=dict(status='PARTIAL',backend='TrEnv-X',smoke=short,
        units=dict(time='ns; plots use seconds',memory='bytes; plots use MiB'),
        limitations=['No checkpoint/restore in this protocol',
            'Original CPU frequency/affinity and guest defaults; no fair cross-backend comparison',
            'End-to-end includes official task tests and private-image hash inventory/removal',
            'No physical page scanning; guest logical memory and host cgroup are not added',
            'Five group log-ratio t95 intervals are approximate; no PPT performance threshold',
            'Mirrored order balances linear ordinal drift, not arbitrary shared-host load changes'])
    if balanced is None:return output
    balanced=balanced.resolve(strict=True);checked=audit(balanced)
    assert checked['protocol']['stage']=='balanced' and checked['run_count']==60
    before=json.loads((smoke/'result.json').read_text());saved=json.loads((balanced/'result.json').read_text())
    assert before['identity']==saved['identity']
    sources=json.loads((balanced/'code/sha256.json').read_text())
    assert sources==json.loads((smoke/'code/sha256.json').read_text())
    rows=[row for group in saved['groups'] for row in group]
    runner_variants={};official_manifest=None
    assert len({row['action_manifest_sha256'] for row in rows})==1
    assert len({row['patch_sha256'] for row in rows})==1
    for row in rows:
        raw=Path(row['raw_dir']);config=json.loads((raw/'config.json').read_text())
        replay=json.loads((raw/'replay-config.json').read_text())
        digest=hashlib.sha256((raw/'inputs/runner.sh').read_bytes()).hexdigest()
        assert digest==replay['runner_sha256']
        variant=config['guest_collector']
        if variant not in runner_variants:runner_variants[variant]=digest
        assert runner_variants[variant]==digest, 'effective runner changed within a condition'
        manifest=json.loads((raw/'official-task-result.json').read_text())['manifest']
        if official_manifest is None:official_manifest=manifest
        assert official_manifest==manifest, 'official task inputs or parser versions changed'
    absolute_metrics={}
    for label in ('control_A','control_B','off','host','guest','both'):
        selected_rows=[row for row in rows if row['label']==label]
        assert len(selected_rows)==10
        absolute_metrics[label]=dict(run_count=len(selected_rows),
            timing_ns={metric:dict(mean=statistics.mean(row[metric] for row in selected_rows),
                                  minimum=min(row[metric] for row in selected_rows),
                                  maximum=max(row[metric] for row in selected_rows)) for metric in METRICS},
            guest_sample_count=sum(row['guest_sample_count'] for row in selected_rows),
            host_sample_count=sum(row['host_sample_count'] for row in selected_rows))
    step_variation=[]
    for number,group in enumerate(saved['groups'],1):
        means={}
        for label in ('off','host','both','control_A','control_B'):
            observations=[]
            for row in group:
                if row['label']!=label:continue
                with (Path(row['raw_dir'])/'replay/steps.tsv').open() as stream:
                    observations.append({item['step']:int(item['duration_ns']) for item in csv.DictReader(stream,delimiter='\t')})
            assert len(observations)==2 and all(len(item)==26 for item in observations)
            means[label]={step:statistics.mean(item[step] for item in observations) for step in observations[0]}
        for label,baseline in (('host','off'),('both','off'),('control_B','control_A')):
            differences=sorted(((step,means[label][step]-means[baseline][step]) for step in means[baseline]),
                               key=lambda item:abs(item[1]),reverse=True)
            step_variation.append(dict(group=number,label=label,baseline=baseline,
                total_difference_ns=sum(value for step,value in differences),
                largest_absolute_differences=[dict(step=step,difference_ns=value,
                    baseline_mean_ns=means[baseline][step],condition_mean_ns=means[label][step]) for step,value in differences[:5]]))
    selected=next(row for row in reversed(rows) if row['label']=='both')
    memory=memory_report(dict(backend='trenvx',mode='full-light',raw=selected['raw_dir']))
    keys=('run_id','group','position','label','task_window_ns','guest_action_sum_ns','controller_end_to_end_ns',
          'guest_sample_count','host_sample_count','guest_peak_nonfree_bytes','host_peak_current_bytes',
          'observed_min_free_bytes','retained_allocated_bytes','action_manifest_sha256','patch_sha256')
    sensitivity={}
    for metric in METRICS:
        changes={label:[] for label in ('host','guest','both','off')}
        for group in saved['groups']:
            pooled=statistics.mean(row[metric] for row in group if row['label'] in ('off','control_A','control_B'))
            for label in changes:
                value=statistics.mean(row[metric] for row in group if row['label']==label)
                changes[label].append(100*(value/pooled-1))
        sensitivity[metric]={label:estimate(values) for label,values in changes.items()}
    with (Path(selected['raw_dir'])/'host/memory-samples.tsv').open() as stream:
        samples=list(csv.DictReader(stream,delimiter='\t'))
    peak=max(samples,key=lambda row:int(row['cgroup_current_bytes']))
    current=int(peak['cgroup_current_bytes'])
    host_composition=dict(timestamp_ns=int(peak['timestamp_ns']),current_bytes=current,
        components_bytes={key:int(peak['cgroup_'+key+'_bytes']) for key in ('file','anon','kernel')},
        scope='same host sampler row; sequential counter reads, no normalization; run-owned sandbox parent cgroup')
    host_composition['percentages']={key:100*value/current for key,value in host_composition['components_bytes'].items()}
    guards=[]
    for directory in balanced.glob('group-*-run-*'):
        guards.extend(json.loads(line) for line in (directory/'disk-guard.jsonl').read_text().splitlines())
    assert len(guards)==60 and all(row['status']=='PASS' for row in guards)
    output.update(status='PASS',meaning='correctness, data and offline recount pass; no numerical threshold verdict',
        formal=checked,absolute_metrics=absolute_metrics,source_hashes=sources,runner_variants=runner_variants,official_manifest=official_manifest,environment=saved['identity'],
        storage=dict(observed_min_free_bytes=min(row['observed_min_free_bytes'] for row in guards),
            scope='outer guarded command including private image inventory/removal; sampled every 0.25 seconds',
            retained_raw_allocated_bytes=sum(row['retained_allocated_bytes'] for row in rows)),runs=[{k:row[k] for k in keys} for row in rows],
        all_off_sensitivity=dict(scope='post-hoc descriptive sensitivity using all six monitoring-off observations per group; primary analysis unchanged',analysis=sensitivity),
        exploratory_step_variation=dict(scope='post-hoc descriptive localization; not causal evidence; primary analysis unchanged',groups=step_variation),
        representative=dict(run_id=selected['run_id'],host_composition_at_observed_peak=host_composition,guest_collector=memory['guest_collector'],guest_composition=memory['guest_composition'],
            host_memory=memory['host_memory'],phase_breakdown=memory['phase_breakdown'],
            monitoring_impact=memory['monitoring_impact']))
    return output


def figure(result,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows=result['runs'];colors=dict(off='#555555',host='#2878b5',guest='#e79628',both='#d64f38',
                                  control_A='#999999',control_B='#cccccc')
    fig,axes=plt.subplots(2,1,figsize=(10,6),constrained_layout=True)
    for label,color in colors.items():
        chosen=[(i+1,row['task_window_ns']/1e9) for i,row in enumerate(rows) if row['label']==label]
        axes[0].scatter([x for x,y in chosen],[y for x,y in chosen],color=color,label=label)
    axes[0].set(xlabel='Replay order',ylabel='Controller runner call (s)');axes[0].legend(ncol=3)
    for label in ('host','guest','both','control'):
        values=result['formal']['analysis']['task_window_ns'][label]['group_percent']
        axes[1].plot(range(1,len(values)+1),values,marker='o',label=label)
    axes[1].axhline(0,color='gray',linewidth=.7)
    axes[1].set(xlabel='Independent group',ylabel='Within-group difference (%)');axes[1].legend()
    fig.savefig(path);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke',type=Path,required=True)
    parser.add_argument('--balanced',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--figure',type=Path)
    args=parser.parse_args();value=report(args.smoke,args.balanced)
    with args.output.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')
    if args.figure:
        assert not args.figure.exists() and args.balanced
        figure(value,args.figure)
    print(f"status={value['status']} output={args.output}")
