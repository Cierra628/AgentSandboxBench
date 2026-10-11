"""Audited, sanitized summary of the five matched monitoring-control pairs."""
import argparse
import json
from pathlib import Path
import statistics

from archive_trenvx_run_data import sha256
from audit_exp1_metrics import audit_run
from run_trenvx_combined_controls import audit, METRICS


def report(driver):
    checked = audit(driver)
    saved = json.loads((driver/'result.json').read_text())
    mode = saved['protocol']['mode']
    pairs = []
    hashes = {'driver/result.json': sha256(driver/'result.json'),
              'driver/code/sha256.json': sha256(driver/'code/sha256.json')}
    for number, pair in enumerate(saved['pairs'], 1):
        rows = []
        phase_events = {}
        for row in pair:
            raw = Path(row['raw_dir'])
            item = dict(backend='trenvx', raw=str(raw), mode=(
                'checkpoint-022-diagnostic' if row['label']=='on' else 'checkpoint-022'
            ) if mode=='checkpoint' else ('full-light' if row['label']=='on' else 'full'))
            metrics = audit_run(item)
            public = {key: value for key, value in row.items() if key!='raw_dir'}
            public.update(final_checks=metrics['final_checks'], cleanup=metrics['cleanup'],
                          phase_breakdown=metrics['phase_breakdown'],
                          named_file_mapping=metrics['named_file_mapping'])
            events = [json.loads(line) for line in (raw/'diagnostic-events.jsonl').read_text().splitlines()]
            phase_events[row['label']] = {event['phase']: event for event in events}
            public['recorded_phase_timing_ns'] = {event['phase']: event['elapsed_ns'] for event in events}
            if row['label']=='on':
                memory = json.loads((driver/f'pair-{number}-on/memory/summary.json').read_text())
                guest = memory['guest_composition']
                public['guest_memory'] = {key: guest[key] for key in (
                    'status', 'sample_count', 'observed_peak_nonfree_bytes',
                    'composition_at_observed_peak', 'peak_scope', 'sampling_mode', 'time_scope')}
                host = dict(memory['host_memory'])
                host['vmm_boundary_observations'] = [{key: vmm[key] for key in (
                    'label', 'pss_bytes', 'scan_elapsed_ns', 'scope')}
                    for vmm in host.get('vmm_boundary_observations', [])]
                public['host_memory'] = host
                public['guest_collector_segments'] = [
                    {key: value for key, value in segment.items() if key!='pid'}
                    for segment in memory.get('guest_collector_segments', [])]
            rows.append(public)
            for name in ('result.json', 'config.json', 'diagnostic-events.jsonl',
                         'replay/steps.tsv', 'image-cleanup.json', 'official-task-result.json'):
                hashes[f'{raw.name}/{name}'] = sha256(raw/name)
            if row['label']=='on':
                for name in ('replay/guest-memory.tsv', 'host/memory-samples.tsv'):
                    hashes[f'{raw.name}/{name}'] = sha256(raw/name)
        on = phase_events['on']
        start = on['tool:replay-prefix' if mode=='checkpoint' else 'tool:replay-all']['start_monotonic_ns']
        end = on['tool:official-task-tests' if mode=='checkpoint' else 'tool:official-task-post-scan']['end_monotonic_ns']
        extra = sorted((event for name, event in on.items() if name not in phase_events['off']
                        and event['start_monotonic_ns']>=start and event['end_monotonic_ns']<=end),
                       key=lambda event: event['start_monotonic_ns'])
        assert all(a['end_monotonic_ns']<=b['start_monotonic_ns'] for a, b in zip(extra, extra[1:]))
        next(row for row in rows if row['label']=='on')['extra_phases_in_task_window_ns'] = {
            event['phase']: event['elapsed_ns'] for event in extra}
        pairs.append(rows)
    means = {label: {metric: statistics.mean(row[metric] for pair in saved['pairs']
        for row in pair if row['label']==label) for metric in METRICS
        if saved['pairs'][0][0][metric] is not None} for label in ('off', 'on')}
    return dict(status=checked['status'], third_initial_acceptance='PARTIAL',
        driver_id=driver.name, task='Exp1 Prettier #14400',
        resources={key: saved['identity'][key] for key in ('cpu', 'memory_mib', 'host_kernel')},
        effective_input_sha256={key: value for key, value in saved['identity'].items()
                                if key.endswith('_sha256')},
        protocol=saved['protocol'], pairs=pairs,
        mean_timing_ns=means, paired_comparison=checked['paired_comparison'],
        unit=dict(memory='bytes', timing='nanoseconds', relative_change='percent'),
        monitoring_impact='matched combined-mode effect measured; all functional/restore checks passed',
        limitations=[checked['limitation'],
            'checkpoint controls include periodic sampling, boundary PSS and two named-file analyses',
            'whole-cache analysis performance is a different, unmeasured protocol',
            'named-file cache is not whole-task cache; guest/host/PSS/physical bytes are not added',
            'TrEnv-X restore inherits files, not arbitrary parent process or open-file state',
            'guest samples stop between runner segments; peaks are observed sample maxima'],
        evidence_sha256=hashes)


def plot(result, output):
    if output.exists():
        raise FileExistsError(output)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 3, figsize=(12, 6), constrained_layout=True)
    for axis, (name, values) in zip(axes.flat, result['paired_comparison'].items()):
        axis.plot(range(1, 6), values['group_percent'], 'o-', label='paired on/off change')
        axis.axhline(0, color='gray', linewidth=1)
        axis.set(title=name.removesuffix('_ns'), xlabel='Pair', ylabel='Change (%)')
        axis.set_xticks(range(1, 6)); axis.grid(alpha=.2)
    fig.suptitle('Combined monitoring; each duration is a separate scope')
    fig.savefig(output)
    plt.close(fig)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('driver', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--plot', type=Path)
    args = parser.parse_args()
    result = report(args.driver)
    with args.output.open('x') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    if args.plot:
        plot(result, args.plot)
    print(f'status={result["status"]} output={args.output}')
