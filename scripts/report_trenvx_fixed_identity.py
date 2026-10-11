"""Offline fixed-identity smoke/ABBA evidence; no acceptance threshold or extrapolation."""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean

from archive_trenvx_run_data import sha256
from report_memory_metrics import report as memory_report
from run_trenvx_fixed_identity import audit, ROOT
from fixed_runner_git_identity import instrument
from persistent_guest_sampling import instrument as persistent


def report(out):
    checked=audit(out);saved=json.loads((out/'result.json').read_text())
    rows=[saved['smoke'],*saved['runs']]
    original=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
    manifests=[];steps=[];memories=[]
    for row in rows:
        raw=Path(row['raw_dir'])
        expected=original.read_text()
        if row['label']=='both':expected=persistent(expected)
        expected=instrument(expected)
        cfg=json.loads((raw/'config.json').read_text())
        if cfg.get('runner_lifecycle_diagnostic',False):
            from guest_runner_lifecycle import instrument as lifecycle
            expected=lifecycle(expected,collector=row['label']=='both')
        assert (raw/'inputs/runner.sh').read_text()==expected
        assert row['action_manifest_sha256']==saved['smoke']['action_manifest_sha256']
        assert row['patch_sha256']==saved['smoke']['patch_sha256']
        official=json.loads((raw/'official-task-result.json').read_text());manifests.append(official['manifest'])
        with (raw/'replay/steps.tsv').open() as stream:
            steps.append(dict(run_id=row['run_id'],position=row['position'],label=row['label'],
                steps=[dict(step=s['step'],exit_code=int(s['exit_code']),duration_ns=int(s['duration_ns'])) for s in csv.DictReader(stream,delimiter='\t')]))
        if row['label']=='both':
            memory=memory_report(dict(backend='trenvx',mode='full-light',raw=str(raw)))
            with (raw/'host/memory-samples.tsv').open() as stream:
                host=[{k:int(s[k]) for k in ('timestamp_ns','cgroup_current_bytes','cgroup_file_bytes','cgroup_anon_bytes','cgroup_kernel_bytes')} for s in csv.DictReader(stream,delimiter='\t')]
            peak=max(host,key=lambda s:s['cgroup_current_bytes']);current=peak['cgroup_current_bytes']
            components={k:int(peak['cgroup_'+k+'_bytes']) for k in ('file','anon','kernel')}
            residual=current-sum(components.values())
            events=[json.loads(line) for line in (raw/'diagnostic-events.jsonl').read_text().splitlines()]
            runner=next(e for e in events if e['phase']=='tool:replay-all')
            start=runner['timestamp_ns'];end=start+runner['elapsed_ns']
            task_host=[s for s in host if start<=s['timestamp_ns']<=end]
            assert task_host,'host runner window has no observed samples'
            memories.append(dict(run_id=row['run_id'],guest_composition=memory['guest_composition'],
                guest_collector=memory['guest_collector'],host_samples=host,
                host_runner_window=dict(sample_count=len(task_host),observed_peak_current_bytes=max(s['cgroup_current_bytes'] for s in task_host),
                    start_timestamp_ns=start,estimated_end_timestamp_ns=end,
                    scope='host controller frozen runner call; excludes official tests; wall start plus monotonic duration gives approximate sample boundaries'),
                host_at_observed_peak=dict(current_bytes=current,components_bytes=components,residual_bytes=residual,
                    percentages={k:100*v/current for k,v in dict(components,residual=residual).items()},
                    scope='same sampler row; sequential counters; residual is not automatically untracked physical memory'),
                phase_breakdown=memory['phase_breakdown']))
    assert all(m==manifests[0] for m in manifests)
    differences={}
    formal=steps[1:]
    for step in range(26):
        values={label:mean(row['steps'][step]['duration_ns'] for row in formal if row['label']==label) for label in ('off','both')}
        differences[f'{step+1:03d}']=dict(off_mean_ns=values['off'],both_mean_ns=values['both'],difference_ns=values['both']-values['off'])
    keys=('run_id','position','label','task_window_ns','guest_action_sum_ns','controller_end_to_end_ns',
          'guest_sample_count','host_sample_count','guest_peak_nonfree_bytes','host_peak_current_bytes',
          'action_manifest_sha256','patch_sha256','runner_sha256','retained_allocated_bytes','observed_min_free_bytes')
    exported=[{k:r[k] for k in keys} for r in rows]
    for r,value in zip(rows,exported):
        if 'runner_lifecycle' in r:value['runner_lifecycle']=r['runner_lifecycle']
    return dict(status='PASS',third_acceptance='PARTIAL',backend='TrEnv-X',protocol=saved['protocol'],
        meaning='functional correctness, data integrity and cleanup verified; contrasts descriptive only',
        units=dict(time='ns; figures use seconds',memory='bytes; figures use MiB'),
        analysis=checked['analysis'],lifecycle_analysis=saved.get('lifecycle_analysis'),runs=exported,
        source_sha256=json.loads((out/'code/sha256.json').read_text()),original_runner_sha256=sha256(original),
        image_sha256=saved['identity']['seed_sha256'],fixed_identity=saved['identity']['fixed_git_identity_values'],
        cpu_policy_before_equals_after=True,official_task_manifest=manifests[0],steps=steps,
        step_mean_differences=differences,memory=memories,
        limitations=['Two observations per condition in one ABBA group; no variance or threshold conclusion',
            'Guest values are logical /proc/meminfo totals; host values are owned sandbox cgroup counters',
            'No physical file-cache attribution, multi-VM deduplication, checkpoint/restore or scan impact in this batch',
            'No global cache drop; fresh private VM does not establish cold host cache',
            'All original frozen actions and action environment preserved; identity changes apply only to preparation/finalization Git invocations',
            'Off runner has sampling disabled; both uses persistent guest instrumentation and host sampling; contrasts cover the entire sampler implementation',
            'Observed peaks only; guest, host cgroup, VMM PSS and physical pages must not be added',
            'Previous original-identity statistics remain independent; no retrospective subtraction of DNS waits',
            'Lifecycle option adds diagnostic clocks/boundaries; its contrasts do not establish uninstrumented monitoring overhead'])


def figure(value,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(10,9),constrained_layout=True)
    rows=value['runs'][1:]
    for key,label in (('task_window_ns','Runner call'),('guest_action_sum_ns','Tool sum')):
        axes[0].plot([r['position'] for r in rows],[r[key]/1e9 for r in rows],marker='o',label=label)
    axes[0].set(xticks=[1,2,3,4],xticklabels=['off','both','both','off'],ylabel='Seconds');axes[0].legend()
    if value['protocol'].get('runner_lifecycle_diagnostic',False):
        axes[0].set_title('Diagnostic clocks in both conditions; descriptive comparison')
    memory=value['memory'][-1];guest=memory['guest_composition']['samples']
    for key in guest[0]['components_bytes']:
        axes[1].plot([(s['timestamp_ns']-guest[0]['timestamp_ns'])/1e9 for s in guest],
            [s['components_bytes'][key]/2**20 for s in guest],label=key)
    axes[1].set(xlabel='Guest seconds since first sample',ylabel='Guest logical memory (MiB)');axes[1].legend(ncol=3,fontsize=8)
    host=memory['host_samples']
    for key in ('current','file','anon','kernel'):
        axes[2].plot([(s['timestamp_ns']-host[0]['timestamp_ns'])/1e9 for s in host],
            [s['cgroup_'+key+'_bytes']/2**20 for s in host],label=key)
    window=memory['host_runner_window'];origin=host[0]['timestamp_ns']
    axes[2].axvspan((window['start_timestamp_ns']-origin)/1e9,(window['estimated_end_timestamp_ns']-origin)/1e9,
        color='gray',alpha=.18,label='Runner call window (approx.)')
    axes[2].set(xlabel='Host seconds since first sample; includes official tests',ylabel='Owned cgroup memory (MiB)');axes[2].legend(ncol=3,fontsize=8)
    fig.savefig(path);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('batch',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--figure',type=Path)
    args=parser.parse_args();value=report(args.batch)
    with args.output.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')
    if args.figure:
        assert not args.figure.exists();figure(value,args.figure)
    print(f"status={value['status']} third_acceptance=PARTIAL output={args.output}")
