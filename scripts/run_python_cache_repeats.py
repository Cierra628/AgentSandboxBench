"""Run the agreed six-round Python cache comparison through existing task entries."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TASKS = ['sphinx7590', 'xarray6992']
TARGETS = {'sphinx7590': {'L': '65', 'H': '98'}, 'xarray6992': {'L': '167', 'H': '251'}}
SEED = 20261018


def schedule():
    orders = ['BLH', 'BHL', 'LBH', 'LHB', 'HBL', 'HLB']
    random.Random(SEED).shuffle(orders)
    runs = []
    for number, order in enumerate(orders, 1):
        tasks = TASKS if number % 2 else TASKS[::-1]
        for task in tasks:
            for position, condition in enumerate(order, 1):
                target = 'baseline' if condition == 'B' else TARGETS[task][condition]
                runs.append(dict(index=len(runs)+1, round=number, task=task,
                    position=position, condition=condition, target=target,
                    key=f'r{number:02d}-{task}-{target}',
                    config=f'configs/platform-{task}-cache-{target}.json'))
    return dict(seed=SEED, rounds=6, run_count=36, actions_per_run=35,
                scope='Sphinx and Xarray; no active reclaim / low / high; fresh guest per run', runs=runs)


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def read(path):
    return json.loads(path.read_text())


def binding_from_log(log, item):
    paths = [Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)', log.read_text())
             if not Path(p).name.startswith('report-')]
    assert len(paths) == 1, paths
    run = paths[0]; state = read(run/'result.json')
    assert state['status'] == 'PASS'
    storage = Path(state['storage_result_dir']); receipt = read(storage/'result.json')
    child = read(storage/'python-backbone/result.json')
    assert receipt['status'] == child['status'] == 'PASS'
    assert all(receipt['cleanup'].values()) and not (storage/'volume.ext4').exists()
    assert child['replay']['action_count'] == 35
    if item['target'] == 'baseline':
        assert child['checks']['cache_baseline_no_reclaim_and_damon_restored'] == 'PASS'
    else:
        assert child['cache_target_mib'] == int(item['target'])
        assert child['checks']['cache_target_and_damon_restored'] == 'PASS'
    assert read(Path(state['report_dir'])/'live-cache/summary.json')['status'] == 'PASS'
    return dict(platform=str(run), storage=str(storage), report=state['report_dir'])


def source_ready(out):
    # Check the existing source before any input preparation or sandbox creation.
    from validate_isolated_agentenv import SERVICE, IMAGE
    result = subprocess.run(['docker', 'inspect', SERVICE], capture_output=True, text=True, timeout=25)
    available = shutil.disk_usage(ROOT).free
    evidence = dict(checked_utc=datetime.now(timezone.utc).isoformat(), service=SERVICE,
                    inspect_exit_code=result.returncode, host_available_bytes=available,
                    loadavg=Path('/proc/loadavg').read_text().strip())
    if result.returncode:
        evidence.update(status='BLOCKED', reason='prepared source service missing',
                        inspect_error=result.stderr.strip())
    else:
        info = json.loads(result.stdout)[0]
        evidence.update(status='READY' if info['State']['Running'] and info['Image'] == IMAGE else 'BLOCKED',
                        source_running=info['State']['Running'], source_image_matches=info['Image'] == IMAGE,
                        source_pid=info['State']['Pid'], source_started_at=info['State']['StartedAt'],
                        source_kind=info['Config']['Labels'].get('agentenv.experiment'))
        if evidence['status'] == 'BLOCKED': evidence['reason'] = 'prepared source service/image unavailable'
    write(out/'source-preflight.json', evidence)
    return evidence


def round_reports(out, runs):
    # Reuse the same per-run correctness and metric checks; pair only within a round.
    from report_python_cache_baseline import report as baseline_report
    from report_python_cache_targets import report as target_report, ORDER
    for number in range(1, 7):
        directory = out/f'round-{number:02d}'; directory.mkdir(exist_ok=True)
        baseline = {task: runs[f'r{number:02d}-{task}-baseline'] for task in TASKS}
        targets = {key: runs[f'r{number:02d}-{key}'] for key in ORDER}
        write(directory/'baseline-runs.json', baseline); write(directory/'target-runs.json', targets)
        if not (directory/'baseline/summary.json').exists():
            baseline_report(directory/'baseline-runs.json', directory/'baseline')
        if not (directory/'targets/summary.json').exists():
            target_report(directory/'target-runs.json', directory/'baseline/summary.json',
                          directory/'baseline-runs.json', directory/'targets')


def execute(out):
    plan = read(out/'plan.json'); assert plan == schedule()
    runs_path = out/'runs.json'; runs = read(runs_path) if runs_path.exists() else {}
    result = dict(status='RUNNING', stage='source-preflight', completed_runs=len(runs), result_dir=str(out))
    write(out/'result.json', result)
    try:
        ready = source_ready(out)
        if ready['status'] != 'READY':
            result.update(status='BLOCKED', reason=ready['reason']); return 2
        for item in plan['runs']:
            result['stage'] = item['key']; write(out/'result.json', result)
            log = out/(item['key']+'.log')
            if not log.exists():
                if (out/'pause-after-current').exists():
                    result.update(status='PAUSED', next_run=item['key'])
                    return 0
                write(out/(item['key']+'-host-before.json'), dict(
                    utc=datetime.now(timezone.utc).isoformat(), host_available_bytes=shutil.disk_usage(ROOT).free,
                    loadavg=Path('/proc/loadavg').read_text().strip()))
                # The established entry has bounded stages and owned-resource cleanup.
                with log.open('x') as stream:
                    completed = subprocess.run(['bash', 'scripts/use-platform.sh', 'run', item['config']],
                                               cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                write(out/(item['key']+'-exit.json'), dict(exit_code=completed.returncode))
                if completed.returncode: raise RuntimeError('task/platform entry failed; retain log and do not retry')
            exit_receipt = out/(item['key']+'-exit.json')
            if not exit_receipt.exists() or read(exit_receipt)['exit_code'] != 0:
                raise RuntimeError('existing log has no successful exit receipt; inspect without rerunning')
            binding = binding_from_log(log, item)
            if item['key'] in runs: assert runs[item['key']] == binding
            runs[item['key']] = binding; write(runs_path, runs)
            result['completed_runs'] = len(runs); write(out/'result.json', result)
            print(f"status=PASS stage={item['key']} completed={len(runs)}/36 result_dir={out}", flush=True)
        result['stage'] = 'round-reports'; write(out/'result.json', result)
        round_reports(out, runs)
        result.update(status='PASS', stage='complete', actual_actions=36*35)
        return 0
    except Exception as exc:
        result.update(status='FAIL', error=repr(exc)); return 1
    finally:
        write(out/'result.json', result)
        print('status='+result['status']+' stage='+result['stage']+' result_dir='+str(out), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--prepare-only', action='store_true', help='Write the fixed plan; no host interfaces or task inputs')
    args = parser.parse_args()
    if args.resume:
        out = args.resume.resolve()
        assert out.parent == ROOT/'.artifacts' and out.name.startswith('python-cache-repeats-')
        assert read(out/'plan.json') == schedule()
    else:
        os.umask(0o077)
        out = ROOT/'.artifacts'/('python-cache-repeats-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
        out.mkdir(); write(out/'plan.json', schedule())
    if args.prepare_only:
        print('status=PREPARED result_dir='+str(out)); return 0
    if args.resume:
        (out/'pause-after-current').unlink(missing_ok=True)
    return execute(out)


if __name__ == '__main__':
    sys.exit(main())
