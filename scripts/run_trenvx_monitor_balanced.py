"""Bounded TrEnv-X same-batch monitoring controls; discard successful private images."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

from archive_trenvx_run_data import allocated_bytes, sha256
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from run_exp1_monitor_balanced import schedule, summarize

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('trenvx_pairs', ROOT/'scripts/46-run-trenvx-exp1-monitor-pairs.py')
pairs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pairs)
IDENTITY = ('seed_sha256', 'ch_sha256', 'kernel_sha256', 'envd_sha256', 'binaries',
            'patches', 'source_revisions', 'cpu', 'memory_mib', 'host_kernel',
            'disk_reserve_bytes', 'disk_poll_seconds', 'script_sha256', 'archive_script_sha256')
SOURCES = ('run_trenvx_monitor_balanced.py', '46-run-trenvx-exp1-monitor-pairs.py',
           '45-replay-trenvx-exp1.sh', '44-smoke-trenvx-service.sh', 'smoke_trenvx_service.py',
           'replay_trenvx_exp1.py', 'archive_trenvx_run_data.py', 'experiment_disk_guard.py',
           'plan_trenvx_storage.py', 'run_exp1_monitor_balanced.py')


def inspect_run(run, label):
    mode = 'light' if label == 'light' else 'off'
    row = pairs.verify(run, mode, discard_images=True)
    config = json.loads((run/'config.json').read_text())
    correctness = json.loads((run/'correctness.json').read_text())
    assert correctness['patch_reference_match'] == 'PASS'
    assert correctness['official_full_tests'] == 'NOT_RUN'
    retained = allocated_bytes(run)
    assert retained <= 128*1024**2, 'raw result exceeds the per-run storage allowance'
    row.update(label=label, raw_dir=str(run), retained_allocated_bytes=retained,
               task_window_ns=row['controller_runner_call_ns'])
    return row, {key:config[key] for key in IDENTITY}


def execute(out, label, remaining):
    plan = successful_image_cleanup_budget(shutil.disk_usage(out).free, remaining)
    (out/'next-storage-plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    if plan['status'] != 'READY':
        raise RuntimeError(f'insufficient space: {plan}')
    argv = ['bash', str(pairs.ENTRY), '--discard-success-images',
            '--reference', str(pairs.REFERENCE.parent)]
    if label == 'light':
        argv += ['--host-sample-interval', '1']
    with (out/'driver.log').open('w') as log:
        guarded_run(argv, directory=out, log=log, timeout=600)
    found = re.findall(r'result_dir=(\S+)', (out/'driver.log').read_text())
    if len(found) != 1:
        raise RuntimeError('missing or ambiguous result directory')
    run = Path(found[0]).resolve(strict=True)
    assert run.parent == pairs.RUNS.resolve(strict=True)
    return inspect_run(run, label)


def audit(directory):
    saved = json.loads((directory/'result.json').read_text())
    assert saved['status'] == 'PASS'
    protocol = json.loads((directory/'protocol.json').read_text())
    assert protocol == saved['protocol']
    assert protocol['orders'] == schedule(protocol['seed'], protocol['groups'])
    assert len(saved['runs']) == 8*protocol['groups']
    for i, row in enumerate(saved['runs']):
        assert row['group'] == i//8+1 and row['position'] == i%8+1
        assert row['label'] == protocol['orders'][i//8][i%8]
        checked, identity = inspect_run(Path(row['raw_dir']), row['label'])
        # The driver may add its own log after this size was observed.
        for key in checked:
            if key != 'retained_allocated_bytes':
                assert checked[key] == row[key], key
        assert identity == saved['fixed_environment']
    assert summarize(saved['runs']) == saved['analysis']
    return dict(status='PASS', run_count=len(saved['runs']), analysis=saved['analysis'],
                protocol=protocol, runs=saved['runs'])


def main(groups, seed):
    os.umask(0o077)
    out = ROOT/'.artifacts/trenvx-monitor-balanced'/(
        time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    orders = schedule(seed, groups)
    protocol = dict(groups=groups, seed=seed, orders=orders,
        primary_metric='controller_runner_call_ns',
        primary_scope='controller tool call executing original 26-step guest runner, including Git preparation; '
                      'sampler runs across create/upload/replay/download/independent-check/delete; '
                      'sampler startup/stop is outside this metric',
        host_sampling='1 Hz run-owned sandbox parent cgroup; excludes orchestrator; guest periodic sampling off',
        cache_policy='fresh private template/VM per replay; no guest or host cache clearing; harness defaults',
        cpu_policy='existing host frequency/affinity; no new CPU binding or frequency changes',
        decision='five independent group log-ratio t95 intervals; control within [-5,5], monitor upper <=5',
        stop_policy='first failure or any group absolute off/off contrast above 5%; no automatic retries',
        retention='successful non-checkpoint private images discarded after hash inventory; raw results retained; '
                  'failure archived or preserved; historical reference archive unchanged',
        cross_backend_comparison='NOT_VALIDATED; measurement scope and CPU/cache policies differ',
        official_full_tests='NOT_RUN')
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    sources = {name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    snapshot = out/'code-snapshot'
    snapshot.mkdir()
    for name in SOURCES:
        shutil.copy2(ROOT/'scripts'/name, snapshot/name)
    (snapshot/'sha256.json').write_text(json.dumps(sources, indent=2)+'\n')
    result = dict(status='FAIL', protocol=protocol, runs=[])
    try:
        for group, order in enumerate(orders, 1):
            for position, label in enumerate(order, 1):
                assert sources == {name:sha256(ROOT/'scripts'/name) for name in SOURCES}, 'source changed'
                directory = out/f'group-{group}-run-{position}-{label}'
                directory.mkdir()
                row, identity = execute(directory, label, 8*groups-len(result['runs']))
                if 'fixed_environment' not in result:
                    result['fixed_environment'] = identity
                assert identity == result['fixed_environment'], 'effective environment changed'
                row.update(group=group, position=position)
                result['runs'].append(row)
                (out/'progress.json').write_text(json.dumps(result['runs'], indent=2)+'\n')
                print(f"status=RUNNING group={group} position={position} mode={label} result_dir={out}", flush=True)
            result['analysis'] = summarize(result['runs'])
            if abs(result['analysis']['control']['group_percent'][-1]) > 5:
                result.update(status='STOPPED_UNSTABLE_CONTROL',
                              reason='same-batch monitoring-off control differs by more than 5%')
                break
        else:
            result['status'] = 'PASS'
    except BaseException as exc:
        result['error'] = repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    if result['status'] == 'PASS':
        (out/'offline-audit.json').write_text(json.dumps(audit(out), indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}", flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=int, choices=(1,5), default=1)
    parser.add_argument('--seed', type=int, default=20261001)
    parser.add_argument('--audit', type=Path)
    args = parser.parse_args()
    if args.audit:
        print(json.dumps(audit(args.audit.resolve())))
    else:
        sys.exit(main(args.groups, args.seed))
