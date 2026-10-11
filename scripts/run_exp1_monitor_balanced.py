"""Same-batch AA/AB, mirrored random order, complete frozen Exp1 replays."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import signal
import statistics
import subprocess
import time
import traceback
from experiment_disk_guard import writer_lock

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2'


def schedule(seed, groups):
    rng = random.Random(seed)
    result = []
    for _ in range(groups):
        first = ['control_A', 'control_B', 'off', 'light']
        rng.shuffle(first)
        result.append(first + first[::-1])
    return result


def estimate(values):
    """Five independent group contrasts, t interval on log ratios (df=4)."""
    logs = [math.log1p(value / 100) for value in values]
    mean = statistics.mean(logs)
    interval = None
    if len(logs) == 5:
        radius = 2.776445105 * statistics.stdev(logs) / math.sqrt(5)
        interval = [100 * math.expm1(mean - radius), 100 * math.expm1(mean + radius)]
    return dict(group_percent=values, geometric_mean_percent=100 * math.expm1(mean),
                mean_log_ratio_t95_percent=interval)


def summarize(rows):
    contrasts = {'monitor': [], 'control': []}
    for number in sorted({r['group'] for r in rows}):
        selected = [r for r in rows if r['group'] == number]
        assert len(selected) == 8
        means = {label: statistics.mean(r['task_window_ns'] for r in selected if r['label'] == label)
                 for label in ('control_A', 'control_B', 'off', 'light')}
        contrasts['monitor'].append(100 * (means['light'] / means['off'] - 1))
        contrasts['control'].append(100 * (means['control_B'] / means['control_A'] - 1))
    results = {name: estimate(values) for name, values in contrasts.items()}
    control = results['control']['mean_log_ratio_t95_percent']
    monitor = results['monitor']['mean_log_ratio_t95_percent']
    verdict = 'INCONCLUSIVE'
    if control and control[0] >= -5 and control[1] <= 5:
        if monitor[1] <= 5:
            verdict = 'WITHIN_5_PERCENT_FOR_THIS_PROTOCOL'
        elif monitor[0] > 5:
            verdict = 'ABOVE_5_PERCENT_FOR_THIS_PROTOCOL'
    results['verdict'] = verdict
    return results


def run_one(out, cfg, number, label, *, sample_guest=False, sample_host=None, sample_interval_seconds=1.0):
    directory = out / f'run-{number:02}-{label}'
    directory.mkdir()
    host_enabled=label=='light' if sample_host is None else sample_host
    effective = dict(cfg, output_root=str(directory), guest_sampling=sample_guest and label == 'light',
                     host_sampling=host_enabled, host_monitor_light=host_enabled,
                     sample_interval_seconds=sample_interval_seconds, generate_analysis=False,
                     variant='balanced-full-trajectory')
    config = directory / 'config.json'
    config.write_text(json.dumps(effective, indent=2) + '\n')
    before = dict(loadavg=os.getloadavg(), free_bytes=shutil.disk_usage(out).free)
    assert before['free_bytes'] >= 32 * 1024**3, 'less than 32 GiB before launch'
    started = time.monotonic()
    minimum_free = before['free_bytes']
    with (directory / 'driver.log').open('w') as log:
        process = subprocess.Popen(['python3', str(ROOT / 'scripts/run_exp1.py'), str(config)],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                minimum_free = min(minimum_free, shutil.disk_usage(out).free)
                if minimum_free < 20 * 1024**3 or time.monotonic() - started > 730:
                    raise RuntimeError('disk reserve or run timeout reached')
                time.sleep(.25)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=80)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
    assert process.returncode == 0, f'run failed; see {directory}'
    raw, = (directory / 'raw').iterdir()
    audit = directory / 'audit' / raw.name
    checks = json.loads((audit / 'checks.json').read_text())
    assert all(checks[key] == 'PASS' for key in
               ('input_hashes', 'runner', 'replay_correctness', 'cleanup', 'task_window'))
    assert checks['guest_sampling'] == ('PASS' if sample_guest and label == 'light' else 'DISABLED')
    assert checks['host_sampling'] == ('PASS' if host_enabled else 'DISABLED')
    assert hashlib.sha256((raw / 'replay/patch.diff').read_bytes()).hexdigest() == PATCH
    with (raw / 'replay/steps.tsv').open() as stream:
        steps = list(csv.DictReader(stream, delimiter='\t'))
    assert [s['step'] for s in steps] == [f'{n:03}' for n in range(1, 27)]
    assert {s['step']: int(s['exit_code']) for s in steps if int(s['exit_code'])} == {'008':127, '016':127}
    host = raw / 'host/memory-samples.tsv'
    samples = list(csv.DictReader(host.open(), delimiter='\t')) if host.exists() else []
    assert len(samples) >= 2 if host_enabled else len(samples) == 0
    metadata = dict(line.split('=', 1) for line in (raw / 'metadata.env').read_text().splitlines())
    assert metadata['workload_image'].endswith('@sha256:e625c9b9776870e2cc87172e886bbc90cfc3d6ce1521492f113a46b3f6dfcf44')
    guest_count = len(list(csv.DictReader((raw/'replay/guest-memory.tsv').open(), delimiter='\t')))
    return dict(label=label, raw_dir=str(raw), audit_dir=str(audit), checks=checks,
                task_window_ns=int((raw / 'task-window-duration-ns.txt').read_text()),
                controller_exec_ns=int((raw / 'controller-exec-duration-ns.txt').read_text()),
                guest_action_sum_ns=sum(int(s['duration_ns']) for s in steps),
                sample_count=len(samples), guest_sample_count=guest_count,
                controller_end_to_end_ns=json.loads((audit/'result.json').read_text())['controller_elapsed_ns'],
                host_before=before, host_load_after=os.getloadavg(),
                observed_min_free_bytes=minimum_free, elapsed_seconds=time.monotonic()-started,
                patch_sha256=PATCH, metadata=metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--groups', type=int, choices=(1, 5), default=1)
    parser.add_argument('--seed', type=int, default=20260929)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    os.umask(0o077)
    out = ROOT / '.artifacts/exp1-monitor-balanced' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    out.mkdir(parents=True)
    orders = schedule(args.seed, args.groups)
    protocol = dict(groups=args.groups, seed=args.seed, orders=orders, primary_metric='task_window_ns',
                    primary_scope='26 frozen actions including host sampler startup/stop; excludes sandbox creation/upload/download',
                    control='control_A/control_B both sampling off; off/light is actual comparison',
                    threshold_percent=5, cache_policy='no cache clearing; fresh VM per replay',
                    decision='95% t interval on five group log ratios; control interval must fit [-5,5]; monitor upper bound <=5',
                    limitations='approximate normal independent group contrasts; balances linear ordinal drift, not arbitrary load changes; no automatic extra groups',
                    independent_full_task_tests='NOT_RUN', historical_raw_dependency=False)
    (out / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    sources = [Path(__file__), ROOT/'scripts/run_exp1.py', args.config]
    snapshot = out/'code-snapshot'
    snapshot.mkdir()
    for source in sources:
        shutil.copy2(source, snapshot/source.name)
    (snapshot/'sha256.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.iterdir()}, indent=2)+'\n')
    repo = (ROOT/cfg['repository']).resolve()
    source_files = [ROOT/'scripts/run_exp1.py', Path(__file__), ROOT/'scripts/experiment_disk_guard.py']
    source_files += [repo/'motivation/experiments/exp1-single-app-smoke/systems/agentenv'/name
                     for name in ('run.sh', 'guest-runner.sh', 'config.env')]
    source_files += [repo/'motivation/experiments/lib/host-memory-monitor.sh']
    def source_hashes():
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    frozen_sources = source_hashes()
    (out/'source-hashes.json').write_text(json.dumps(frozen_sources, indent=2)+'\n')
    fixed_environment = None
    rows = []
    result = dict(status='FAIL', protocol=protocol, runs=rows)
    print(f'status=RUNNING result_dir={out}', flush=True)
    try:
        with writer_lock():
            for group, order in enumerate(orders, 1):
                for position, label in enumerate(order, 1):
                    assert source_hashes() == frozen_sources, 'source changed during batch'
                    row = run_one(out, cfg, len(rows)+1, label)
                    assert source_hashes() == frozen_sources, 'source changed during replay'
                    identity = {key:row['metadata'][key] for key in
                                ('workload_image', 'sandbox_cpu', 'sandbox_memory_mib',
                                 'action_manifest_sha256', 'aenv_version', 'host_kernel',
                                 'agentenv_container_image', 'agentenv_container_id',
                                 'overlaybd_io_engine', 'overlaybd_direct_io')}
                    if fixed_environment is None:
                        fixed_environment = identity
                        (out/'fixed-environment.json').write_text(json.dumps(identity, indent=2)+'\n')
                    assert identity == fixed_environment, 'effective environment changed'
                    row.update(group=group, position=position)
                    rows.append(row)
                    (out/'progress.json').write_text(json.dumps(rows, indent=2)+'\n')
                    print(f'status=REPLAY_PASS group={group}/{args.groups} position={position}/8 result_dir={out}', flush=True)
        result.update(status='PASS', analysis=summarize(rows))
    except BaseException:
        (out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}", flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
