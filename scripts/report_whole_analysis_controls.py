"""Offline audit and sanitized memory/timing report for whole-analysis pairs."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from archive_trenvx_run_data import sha256
from memory_composition import series
from run_whole_analysis_controls import audit


def load(path):
    return json.loads(path.read_text())


def tsv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def memory(raw, backend, analysis):
    rows = tsv(raw/'replay/guest-memory.tsv')
    receipt = load(raw/'replay/collector-status.json')
    assert receipt['status'] == 'PASS'
    assert receipt.get('samples_in_file', receipt['samples']) == len(rows)
    samples = [dict(timestamp_ns=int(row['timestamp_ns']), phase=row['phase'], step=row['step'],
                    meminfo_bytes={k.removesuffix('_kib'): int(v)*1024
                                   for k, v in row.items() if k.endswith('_kib')}) for row in rows]
    periodic = series(samples)
    assert periodic['status'] == 'PASS'
    boundaries = []
    if analysis:
        import re
        for label in ('before', 'after'):
            row = load(raw/'whole-cache'/f'{label}.json')
            values = {k: int(v)*1024 for k, v in re.findall(r'^(\w+):\s+(\d+) kB$', row['meminfo'], re.M)}
            boundaries.append(dict(timestamp_ns=row['timestamp_ns'], phase='whole-cache-'+label,
                                   meminfo_bytes=values))
    boundary_series = series(boundaries)
    host_path = raw/('host-vmm.tsv' if backend == 'agentenv' else 'host/memory-samples.tsv')
    host_rows = tsv(host_path)
    assert len(host_rows) >= 2
    columns = ('pss_bytes', 'rss_bytes', 'service_cgroup_current_bytes') if backend == 'agentenv' else (
        'cgroup_current_bytes', 'cgroup_file_bytes', 'cgroup_anon_bytes', 'cgroup_kernel_bytes')
    host = dict(sample_count=len(host_rows),
        observed_peak_bytes={key: max(int(row[key]) for row in host_rows) for key in columns},
        scope=('owned Firecracker PSS/RSS; service cgroup also includes pool and shared provider'
               if backend == 'agentenv' else 'owned sandbox parent cgroup; excludes orchestrator/scanner'),
        samples=[dict(elapsed_seconds=(int(row['timestamp_ns'])-int(host_rows[0]['timestamp_ns']))/1e9,
                      **{key: int(row[key]) for key in columns}) for row in host_rows])
    if backend == 'trenvx':
        host['max_sample_read_ns'] = max(int(row['sample_read_ns']) for row in host_rows)
    return dict(periodic_guest=periodic, analysis_guest_boundaries=boundary_series, host=host,
        sampling_scope='guest periodic samples only during runner; analysis has two later boundaries; host samples continue',
        sampling_interval_seconds=.1, unit='bytes', peak='observed sample maximum, not instantaneous peak')


def report(driver):
    checked = audit(driver)
    saved = load(driver/'result.json')
    backend = saved['backend']
    pairs = []
    hashes = {'driver/result.json': sha256(driver/'result.json'),
              'driver/code/sha256.json': sha256(driver/'code/sha256.json')}
    for pair in saved['pairs']:
        public_pair = []
        for row in pair:
            raw = Path(row['raw_dir'])
            on = row['label'] == 'on'
            public = {key: value for key, value in row.items() if key != 'raw_dir'}
            public['memory'] = memory(raw, backend, on)
            names = ['result.json', 'config.json', 'replay/steps.tsv', 'replay/actions.tsv',
                     'replay/patch.diff', 'replay/guest-memory.tsv', 'replay/collector-status.json']
            public['functional_checks'] = load(raw/'result.json')['checks']
            if backend == 'agentenv':
                events = {r['phase']: r for r in map(json.loads, (raw/'events.jsonl').read_text().splitlines())}
                phases = {key: r['end_monotonic_ns']-r['start_monotonic_ns'] for key, r in events.items()}
                public['phase_breakdown_ns'] = dict(guest_actions=row['guest_tools_ns'],
                    controller_runner_calls=row['runner_calls_ns'],
                    cold_start_interface=phases['start'],
                    create_to_first_success=events['guest-environment']['end_monotonic_ns']-events['start']['start_monotonic_ns'],
                    official_before=phases['official-before-scan'], official_after=phases['official-post-scan'],
                    sdk_delete=phases['delete'], template_build=None, checkpoint=None, restore=None,
                    full_run_including_cleanup=row['full_run_ns'])
                names += ['events.jsonl', 'host-vmm.tsv', 'official-before.json', 'official-after.json',
                          'source-before.stdout', 'source-after.stdout', 'exp1-correctness.json']
                public['trajectory_semantics'] = {key: load(raw/'exp1-correctness.json')[key]
                    for key in ('cwd', 'session_semantics', 'environment', 'cache_policy')}
            else:
                from audit_exp1_metrics import audit_run
                audited = audit_run(dict(backend=backend, raw=str(raw), mode='full-light'))
                public['phase_breakdown_ns'] = {key: value['duration_ns']
                    for key, value in audited['phase_breakdown'].items()}
                public['host_boundary_pss'] = [{key: value[key] for key in ('label', 'pss_bytes', 'scan_elapsed_ns', 'scope')}
                    for value in audited['host_memory'].get('vmm_boundary_observations', [])]
                events = {r['phase']: r for r in map(json.loads, (raw/'diagnostic-events.jsonl').read_text().splitlines())}
                phases = {key: r['elapsed_ns'] for key, r in events.items()}
                public['official_tests_passed'] = load(raw/'official-task-result.json')['parser_results']['num_tests_passed']
                names += ['diagnostic-events.jsonl', 'host/memory-samples.tsv', 'official-task-result.json', 'image-cleanup.json']
            public['recorded_phase_timing_ns'] = phases
            if on:
                physical = load(raw/'whole-cache/result.json')
                public['fine_analysis_controller_ns'] = physical['controller_elapsed_ns']
                if backend == 'agentenv':
                    public['collector_process'] = {key: physical[key] for key in (
                        'collector_cpu_user_seconds', 'collector_cpu_system_seconds',
                        'collector_process_maxrss_kib', 'collector_scope')}
                    public['backing_file_count'] = len(load(raw/'whole-cache/layer-inventory.json')['files'])
                names += ['whole-cache/result.json', 'whole-cache/before.json', 'whole-cache/after.json',
                          'whole-cache/before-flags.bin', 'whole-cache/after-flags.bin']
            else:
                public['fine_analysis_controller_ns'] = None
            if saved['protocol'].get('version')=='exact-kernel-frozen-v1':
                public['final_frozen_kernel_cache']=row['physical'] if on else None
                if backend=='trenvx':
                    from report_file_inventory import kernel_audit
                    public['common_pre_replay_calibration']=kernel_audit(raw/'kernel-cache-calibration-files',load(raw/'kernel-cache-calibration/ram-attestation.json'),backend='trenvx')
                if on:
                    prefix='file-inventory' if backend=='agentenv' else 'whole-cache-files'
                    names += [prefix+'/'+suffix for suffix in ('kernel-file-types.json','kernel-page-owners.json','frozen-page-structs.bin','frozen-typed-ram.bin','allocation-owner-proof.json','allocation-owner-pages.bin')]
                    if backend=='agentenv':
                        from report_file_inventory import audit_frozen_fixture
                        public['frozen_fixture']=audit_frozen_fixture(raw/prefix)
                        names += [prefix+'/frozen-fixture-result.json']
                    else:names += [prefix+'/snapshot-page-fallback.json']
            for name in names:
                hashes[f'{raw.name}/{name}'] = sha256(raw/name)
            public_pair.append(public)
        pairs.append(public_pair)
    metrics = tuple(saved['analysis'])
    means = {label: {name: statistics.mean(row[name] for pair in pairs for row in pair if row['label'] == label)
                    for name in metrics} for label in ('off', 'on')}
    identity = saved['identity']
    resources = {key: identity[key] for key in ('cpu', 'memory_mib', 'host_kernel')}
    resources['image' if backend == 'agentenv' else 'seed_sha256'] = identity['image' if backend == 'agentenv' else 'seed_sha256']
    return dict(status=checked['status'], backend=backend, driver_id=driver.name,
        task='Exp1 Prettier #14400; 26 frozen shell actions', rollout='single path; no checkpoint in this protocol',
        protocol=saved['protocol'], resources=resources,
        actual_boot_policy=identity.get('guest_boot_policy'),
        actual_vmm_memory=identity.get('vmm_memory_config',identity.get('machine_config')),
        actual_vmm_balloon=identity.get('vmm_balloon_config',identity.get('balloon_config')),
        effective_input_sha256={key: value for key, value in identity.items() if key.endswith('_sha256')},
        code_sha256=load(driver/'code/sha256.json'), pairs=pairs, mean_timing_ns=means,
        fine_analysis_mean_ns=statistics.mean(row['fine_analysis_controller_ns'] for pair in pairs for row in pair if row['label'] == 'on'),
        paired_comparison=checked['paired_comparison'],
        unit=dict(memory='bytes', timing='nanoseconds', relative_change='percent', collector_peak_rss='KiB'),
        monitoring_functional_validation='PASS; source, patch, independent checks, official tests and cleanup in every case',
        limitations=[
            'periodic guest/host collectors stay on in both conditions; this measures added whole-analysis cost only',
            'fine analysis is active and non-atomic; guest classification and host page changes are preserved',
            'guest counters, VMM PSS, service/cgroup memory and physical unions are separate scopes; never add',
            'shared AgentENV layers include other users of these layers; not exclusive per-sandbox bytes',
            'backing cache includes filesystem/layer metadata; not all inode-attributed task content',
            'provider anonymous cache, whole-task inode attribution and multi-VM sharing benefit are not measured',
            'guest periodic curve ends at runner exit; later scan boundaries must not be interpolated as continuous samples',
            ('Actual boot policy and VMM memory/balloon configuration matched; causal DAMON/balloon effects not isolated.' if saved['protocol'].get('version')=='exact-kernel-frozen-v1' else 'fresh guest does not prove cold host cache; default DAMON/balloon state not verified in this protocol'),
            saved['protocol']['limitation'],
            'no PPT performance threshold; confidence interval containing zero does not prove no impact'],
        evidence_sha256=hashes)


def plot(result, output):
    if output.exists():
        raise FileExistsError(output)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    on = next(row for row in result['pairs'][0] if row['label'] == 'on')
    fig, axes = plt.subplots(3, 1, figsize=(10, 10), constrained_layout=True)
    guest = on['memory']['periodic_guest']['samples']
    times = [(row['timestamp_ns']-guest[0]['timestamp_ns'])/1e9 for row in guest]
    for key in guest[0]['components_bytes']:
        axes[0].plot(times, [r['components_bytes'][key]/2**20 for r in guest], label=key)
    axes[0].plot(times, [r['nonfree_bytes']/2**20 for r in guest], color='black', label='nonfree')
    axes[0].set(title=f"{result['backend']} pair 1, analysis on: runner lifetime only", ylabel='Guest logical memory (MiB)', xlabel='Seconds since first guest sample')
    axes[0].legend(ncol=3, fontsize=7)
    host = on['memory']['host']['samples']
    for key in on['memory']['host']['observed_peak_bytes']:
        # The shared AgentENV service is a separate, much larger scope.
        if key == 'service_cgroup_current_bytes':
            continue
        axes[1].plot([r['elapsed_seconds'] for r in host], [r[key]/2**20 for r in host], label=key)
    axes[1].set(ylabel='Host memory (MiB)', xlabel='Seconds since first host sample')
    axes[1].legend(fontsize=8)
    for name, values in result['paired_comparison'].items():
        axes[2].plot(range(1, 6), values['group_percent'], 'o-', label=name.removesuffix('_ns'))
    axes[2].axhline(0, color='gray', linewidth=1)
    axes[2].set(xlabel='Matched pair', ylabel='Analysis on/off change (%)', xticks=range(1, 6))
    axes[2].legend(fontsize=8)
    for axis in axes:
        axis.grid(alpha=.2)
    fig.savefig(output)
    plt.close(fig)


if __name__ == '__main__':
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
