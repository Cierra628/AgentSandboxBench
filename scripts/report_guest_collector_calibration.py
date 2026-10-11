"""Offline fixed-work collector evidence; descriptive single-VM contrasts only."""
import argparse
import csv
import json
from pathlib import Path

from archive_trenvx_run_data import sha256
from memory_composition import series
from run_guest_collector_calibration import audit


def report(batch):
    checked = audit(batch)
    saved = json.loads((batch / 'result.json').read_text())
    raw = Path(saved['run']['raw_dir'])
    base = raw / 'collector-calibration'
    metadata = json.loads((base / 'metadata.json').read_text())
    for name, key in [('fixed-work', 'work_binary_sha256'), ('command-clock', 'clock_binary_sha256')]:
        assert sha256(raw / 'inputs' / name) == metadata[key]
    source = json.loads((raw / 'collector-calibration-result.json').read_text())
    rows = []
    for row in source['runs']:
        replay = base / f'case-{row["index"]:02d}' / 'replay'
        samples = []
        with (replay / 'guest-memory.tsv').open() as stream:
            for sample in csv.DictReader(stream, delimiter='\t'):
                samples.append(dict(timestamp_ns=int(sample['timestamp_ns']), phase=sample['phase'],
                    step=sample['step'], meminfo_bytes={key[:-4]: int(value) * 1024
                        for key, value in sample.items() if key.endswith('_kib')}))
        assert len(samples) == row['sample_count']
        rows.append(dict(row, guest_composition=series(samples)))
    correctness = json.loads((raw / 'correctness.json').read_text())
    official = json.loads((raw / 'official-task-result.json').read_text())
    result = json.loads((raw / 'result.json').read_text())
    return dict(status='PASS', third_acceptance='PARTIAL', backend='TrEnv-X',
        protocol=saved['protocol'], units=dict(time='ns; collector CPU seconds', memory='bytes'),
        analysis=checked['calibration']['analysis'], runs=rows,
        input_sha256={key: value for key, value in metadata.items() if key.endswith('_sha256')},
        source_sha256=json.loads((batch / 'code/sha256.json').read_text()),
        image_sha256=saved['identity']['seed_sha256'], cpu=saved['identity']['cpu'],
        memory_mib=saved['identity']['memory_mib'], host_kernel=saved['identity']['host_kernel'],
        cpu_policy_before_equals_after=True, cache_policy=metadata['cache_policy'],
        full_exp1_functional_check=dict(status=result['status'], checks=result['checks'],
            correctness=correctness, official_task_manifest=official['manifest'],
            official_task_result=official['parser_results'], host_cleanup=result['host_cleanup'],
            sdk_cleanup=json.loads((raw / 'sdk-cleanup.json').read_text()),
            scope='one full frozen Exp1 replay after calibration; excluded from performance contrasts'),
        storage=dict(retained_allocated_bytes=saved['run']['retained_allocated_bytes'],
            observed_min_free_bytes=saved['run']['observed_min_free_bytes'],
            private_images_discarded=True, image_inventory='image-cleanup.json'),
        limitations=[
            'Four smoke calls excluded; 32 formal calls are eight ABBA blocks in one warm VM, not independent VM replications',
            'Native fixed CPU work with two diagnostic clocks in both conditions; not an Exp1 monitoring-overhead verdict',
            'Host periodic sampling disabled; no checkpoint/restore or fine physical scan',
            'Startup/stop times are contained in guest/controller elapsed times; never add nested scopes',
            'Byte-identical outputs verified against first off call; independent arithmetic checked at 257 iterations only',
            'Guest /proc/meminfo is logical composition with an unclassified residual; not host physical page-cache residency',
            'Sample maxima are observed peaks; different calls are separated by downloads and have no continuous trace',
            'No whole-task upper/unmapped cache attribution or multi-VM PFN deduplication',
            'No global cache drop; warm fixture does not establish all host/guest cache states',
            'No performance threshold or confidence interval inferred from this single VM'])


def figure(value, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = [row for row in value['runs'] if row['stage'] == 'measured']
    fig, axes = plt.subplots(3, 1, figsize=(10, 10), constrained_layout=True)
    for label, color in [('off', '#276FBF'), ('on', '#D95F02')]:
        subset = [row for row in rows if row['label'] == label]
        for key, style in [('work_ns', 'o'), ('guest_envelope_ns', 'x')]:
            times = [row['work_clock']['monotonic_raw_ns'] if key == 'work_ns'
                else row['lifecycle']['clock']['monotonic_raw_ns'] for row in subset]
            axes[0].scatter([row['index'] - 3 for row in subset], [v / 1e9 for v in times],
                marker=style, color=color, label=f'{label}: {key}')
    axes[0].set(xlabel='Formal call order (ABBA)', ylabel='Seconds',
        title='Fixed CPU work vs entire guest runner; one warm VM')
    axes[0].legend(ncol=2)
    for key, label in [('work_ns', 'Fixed work'), ('guest_envelope_ns', 'Guest runner'), ('controller_ns', 'Controller')]:
        axes[1].plot(range(1, 9), value['analysis'][key]['within_vm_block_percent'], marker='o', label=label)
    axes[1].axhline(0, color='gray', linewidth=.7)
    axes[1].set(xlabel='Within-VM ABBA block', ylabel='On/off mean difference (%)',
        title='Descriptive contrasts; no independent-VM or threshold inference')
    axes[1].legend()
    samples = rows[-2]['guest_composition']['samples']
    for key in samples[0]['components_bytes']:
        axes[2].plot([(s['timestamp_ns'] - samples[0]['timestamp_ns']) / 1e9 for s in samples],
            [s['components_bytes'][key] / 2**20 for s in samples], label=key)
    axes[2].set(xlabel='Guest seconds since first sample of last on call', ylabel='Logical memory (MiB)',
        title='Guest counters only; not host physical residency')
    axes[2].legend(ncol=3, fontsize=8)
    fig.savefig(path)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--figure', type=Path)
    args = parser.parse_args()
    if args.figure:
        assert not args.figure.exists()
    value = report(args.batch)
    with args.output.open('x') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
    if args.figure:
        figure(value, args.figure)
    print(f'status=PASS third_acceptance=PARTIAL output={args.output}')
