"""Audit independent, unchanged TrEnv-X balanced groups without pooling old pairs."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from run_exp1_monitor_balanced import summarize
from run_trenvx_monitor_balanced import audit
from archive_trenvx_run_data import sha256


def report(directories):
    directories = [Path(path).resolve(strict=True) for path in directories]
    if not 1 <= len(directories) <= 5 or len(set(directories)) != len(directories):
        raise ValueError('one to five distinct group directories required')
    rows = []
    identity = None
    sources = None
    protocols = []
    for number, directory in enumerate(directories,1):
        checked = audit(directory)
        protocol = checked['protocol']
        protocols.append(protocol)
        assert protocol['groups'] == 1 and protocol['seed'] == 20261000+number
        saved = json.loads((directory/'result.json').read_text())
        hashes = json.loads((directory/'code-snapshot/sha256.json').read_text())
        assert hashes == {name:sha256(directory/'code-snapshot'/name) for name in hashes}
        if identity is None:
            identity, sources = saved['fixed_environment'], hashes
        assert identity == saved['fixed_environment'] and sources == hashes
        for row in checked['runs']:
            assert sha256(Path(row['raw_dir'])/'replay/stdout/025.log') == (
                '9a64ed707c224734715f7af4fea2d84f0b51d128e5c8d05535fc9de7d5b6162f')
            rows.append(dict(row, group=number))
    analysis = summarize(rows)
    largest = max(range(1,len(directories)+1), key=lambda g:abs(analysis['control']['group_percent'][g-1]))
    selected = [row for row in rows if row['group']==largest and row['label'] in ('control_A','control_B')]
    timings = {'control_A':[], 'control_B':[]}
    for row in selected:
        with (Path(row['raw_dir'])/'replay/steps.tsv').open() as stream:
            steps = list(csv.DictReader(stream,delimiter='\t'))
        timings[row['label']].append(dict(row, steps={step['step']:int(step['duration_ns']) for step in steps}))
    def delta(field):
        return (statistics.mean(field(row) for row in timings['control_B'])-
                statistics.mean(field(row) for row in timings['control_A']))/1e6
    diagnostic = dict(group=largest, controller_call_difference_ms=delta(lambda row:row['task_window_ns']),
        guest_action_sum_difference_ms=delta(lambda row:row['guest_action_sum_ns']),
        step_difference_ms={step:delta(lambda row:row['steps'][step]) for step in ('023','024','025')},
        interpretation='observed differences only; no CPU/cache/scheduling causal attribution')
    public_rows = [{key:row[key] for key in ('run_id','group','position','label','mode','task_window_ns',
        'guest_action_sum_ns','host_sample_count','retained_allocated_bytes','observed_min_free_bytes',
        'input_sha256','patch_sha256')} for row in rows]
    return dict(status='PASS', backend='TrEnv-X', run_count=len(rows), analysis=analysis,
                groups=[str(path) for path in directories], group_protocols=protocols,
                fixed_environment=identity, source_hashes=sources, runs=public_rows,
                largest_control_diagnostic=diagnostic,
                limitations=['original frequency and affinity; no claim of equality to AgentENV CPU profile',
                    'sampler startup/stop and sandbox/template/cleanup are outside primary tool-call metric',
                    'private images discarded; this dataset cannot restore or reanalyse disk images',
                    'full task-specific tests NOT_RUN; independent SVG/nestedSVG/HTML properties checked',
                    'historical off/light pairs and retention smoke are not statistical observations here'])


def figures(result, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = result['runs']
    colors = {'control_A':'#777777','control_B':'#bbbbbb','off':'#2878b5','light':'#d64f38'}
    figure, axes = plt.subplots(2,1,figsize=(10,6),constrained_layout=True)
    for label,color in colors.items():
        selected = [(i+1,row['task_window_ns']/1e9) for i,row in enumerate(rows) if row['label']==label]
        axes[0].scatter([x for x,y in selected],[y for x,y in selected],color=color,label=label)
    axes[0].set(xlabel='Replay order',ylabel='Controller runner call (s)')
    axes[0].legend(ncol=4)
    for key,marker in [('monitor','o'),('control','s')]:
        values = result['analysis'][key]['group_percent']
        axes[1].plot(range(1,len(values)+1),values,marker=marker,label=key)
    axes[1].axhline(5,color='red',linestyle='--',label='5% threshold')
    axes[1].axhline(-5,color='red',linestyle='--')
    axes[1].axhline(0,color='gray',linewidth=.7)
    axes[1].set(xlabel='Independent group',ylabel='Difference (%)')
    axes[1].legend()
    figure.savefig(destination/'monitor-order.png',dpi=160)
    figure.savefig(destination/'monitor-order.svg')
    plt.close(figure)
    # One actual run, no averaging of guest/host or different instances.
    row = next(row for row in reversed(rows) if row['label']=='light')
    raw = None
    for directory in result['groups']:
        saved = json.loads((Path(directory)/'result.json').read_text())
        matching = [item for item in saved['runs'] if item['run_id']==row['run_id']]
        if matching:
            raw = Path(matching[0]['raw_dir'])
            break
    assert raw is not None
    with (raw/'host/memory-samples.tsv').open() as stream:
        samples = list(csv.DictReader(stream,delimiter='\t'))
    times = [(int(sample['timestamp_ns'])-int(samples[0]['timestamp_ns']))/1e9 for sample in samples]
    figure, axis = plt.subplots(figsize=(9,4),constrained_layout=True)
    for key,label in [('cgroup_current_bytes','Total'),('cgroup_anon_bytes','Anonymous'),
                      ('cgroup_file_bytes','File'),('cgroup_kernel_bytes','Kernel')]:
        axis.plot(times,[int(sample[key])/1024**2 for sample in samples],label=label)
    axis.set(xlabel='Seconds since first sample (entire exercise)',ylabel='Host cgroup (MiB)',
             title=f"TrEnv-X sandbox cgroup; orchestrator excluded, PSS not measured; {row['run_id']}")
    axis.legend()
    figure.savefig(destination/'host-memory.png',dpi=160)
    figure.savefig(destination/'host-memory.svg')
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--figures', action='store_true')
    args = parser.parse_args()
    result = report(args.directories)
    with args.output.open('x') as stream:
        json.dump(result,stream,indent=2)
        stream.write('\n')
    if args.figures:
        figures(result,args.output.parent)
    print(f"status=PASS verdict={result['analysis']['verdict']} result_dir={args.output.parent}")
