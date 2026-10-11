"""Calibrate runner timestamps and outer clock in an owned warm-fixture guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from guest_runner_phases import instrument, instrument_baseline, collect

ROOT = Path(__file__).resolve().parents[1]
ORDER = [('aa', 'A', False), ('aa', 'B', False), ('aa', 'B', False), ('aa', 'A', False),
         ('ab', 'A', False), ('ab', 'B', True), ('ab', 'B', True), ('ab', 'A', False)]
ITERATIONS = 32


def guest(base, phase, enabled, baseline_commands=False, baseline_add_trace=False):
    original = (base / 'guest-runner-original.sh').read_text()
    source = instrument(original) if enabled or baseline_commands else original
    if baseline_commands and (enabled or baseline_add_trace): source = instrument_baseline(source,add_trace=baseline_add_trace and enabled)
    source = source.replace('/testbed', str(base / 'fixture'))
    directory = base / phase; directory.mkdir()
    runner = directory / 'runner.sh'; runner.write_text(source)
    env = dict(os.environ, ACTIONS_DIR=str(base / 'actions'), GUEST_MEMORY_SAMPLING='0', DROP_GUEST_CACHES='0')
    records = []; started = time.monotonic_ns()
    for number in range(ITERATIONS):
        out = directory / f'iteration-{number:03}'; clock = directory / f'clock-{number:03}.json'
        command = ['bash', str(runner)]
        if enabled or baseline_commands: command = [str(base / 'command-clock'), str(out / 'runner-envelope.json')] + command
        p = subprocess.run([str(base / 'command-clock'), str(clock)] + command,
                           env=dict(env, OUTPUT_DIR=str(out)), capture_output=True, timeout=15)
        assert p.returncode == 0 and not p.stdout and not p.stderr
        records.append(dict(iteration=number, exit_code=p.returncode))
    elapsed = time.monotonic_ns() - started  # validation below is offline from the measured phase
    for item in records:
        number = item['iteration']; out = directory / f'iteration-{number:03}'
        item['clock'] = json.loads((directory / f'clock-{number:03}.json').read_text())
        item['stdout_sha256'] = hashlib.sha256((out / 'stdout/025.log').read_bytes()).hexdigest()
        assert not (out / 'stderr/025.log').read_bytes() and not (out / 'patch.diff').read_bytes()
        if enabled or baseline_commands: item['runner_phases'] = collect(out,baseline_commands=baseline_commands and (enabled or baseline_add_trace),baseline_add_trace=baseline_add_trace and enabled,baseline_repository=base / 'fixture')
    assert len({r['stdout_sha256'] for r in records}) == 1
    result = dict(status='PASS', phase=phase, phase_ns=elapsed, iterations=records,
                  command_total_ns=sum(r['clock']['monotonic_raw_ns'] for r in records))
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')


def report(directory):
    result = json.loads((directory / 'result.json').read_text())
    protocol=json.loads((directory / 'protocol.json').read_text())
    baseline_commands=protocol.get('baseline_commands',False)
    baseline_add_trace=protocol.get('baseline_add_trace',False)
    assert not baseline_add_trace or baseline_commands
    assert result['status'] == 'PASS' and result['cleanup'] == 'PASS'
    for name, digest in json.loads((directory / 'inputs.json').read_text()).items():
        assert hashlib.sha256((directory / 'tools' / name).read_bytes()).hexdigest() == digest
    assert len(result['runs']) == len(ORDER)
    stdout_hashes = set()
    for number, (row, expected) in enumerate(zip(result['runs'], ORDER)):
        comparison, label, enabled = expected
        assert (row['comparison'], row['label'], row['enabled']) == expected
        raw = directory / 'raw' / row['phase']
        measured = json.loads((raw / 'result.json').read_text()); assert measured == row['measurement']
        original = (directory / 'tools/guest-runner-original.sh').read_text()
        expected_source = instrument(original) if enabled or baseline_commands else original
        if baseline_commands and (enabled or baseline_add_trace): expected_source = instrument_baseline(expected_source,add_trace=baseline_add_trace and enabled)
        assert (raw / 'runner.sh').read_text() == expected_source.replace('/testbed', '/workspace/runner-phase/fixture')
        assert [r['iteration'] for r in measured['iterations']] == list(range(ITERATIONS))
        for item in measured['iterations']:
            out = raw / f"iteration-{item['iteration']:03}"
            clock = json.loads((raw / f"clock-{item['iteration']:03}.json").read_text())
            assert clock == item['clock'] and clock['exit_code'] == 0 and clock['monotonic_raw_ns'] > 0
            assert abs(clock['realtime_ns'] - clock['monotonic_raw_ns']) <= 2_000_000
            assert hashlib.sha256((out / 'stdout/025.log').read_bytes()).hexdigest() == item['stdout_sha256']
            assert not (out / 'stderr/025.log').read_bytes() and not (out / 'patch.diff').read_bytes()
            stdout_hashes.add(item['stdout_sha256'])
            if enabled or baseline_commands: assert collect(out,baseline_commands=baseline_commands and (enabled or baseline_add_trace),baseline_add_trace=baseline_add_trace and enabled,baseline_repository='/workspace/runner-phase/fixture') == item['runner_phases']
            else: assert not (out / 'runner-phases.tsv').exists() and not (out / 'runner-envelope.json').exists()
        assert measured['command_total_ns'] == sum(r['clock']['monotonic_raw_ns'] for r in measured['iterations'])
    assert len(stdout_hashes) == 1
    freq = json.loads((directory / 'fixed-frequency/result.json').read_text())
    assert freq == result['frequency'] and freq['status'] == 'PASS' and freq['restoration']['status'] == 'PASS'
    differences = {}
    for metric in ('command_total_ns', 'phase_ns'):
        differences[metric] = {}
        for comparison in ('aa', 'ab'):
            values = {label: sum(r['measurement'][metric] for r in result['runs'] if r['comparison'] == comparison and r['label'] == label) / 2 for label in ('A', 'B')}
            differences[metric][comparison] = 100 * (values['B'] / values['A'] - 1)
    gate = 'PASS' if all(abs(v['aa']) <= 5 and v['ab'] <= 5 for v in differences.values()) else 'NOT_ACCEPTED'
    summary = dict(status='PASS', diagnostic_gate=gate, differences_percent=differences,
                   scope='incremental five Bash timestamps plus outer dual clock; warm runner fixture in DAMON-disabled guest only; not formal monitoring acceptance',
                   iterations_per_phase=ITERATIONS, phases=len(ORDER), source_result_sha256=hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest())
    if baseline_commands:summary['scope']='incremental initial Git timestamps and Bash command CPU/wall timers; existing phase/envelope probes in both groups; warm DAMON-disabled fixture; not monitoring acceptance'
    if baseline_add_trace:summary['scope']='incremental initial add native performance/Trace2 and guest diskstats; existing phase/command timers in both groups; warm DAMON-disabled fixture only; not monitoring acceptance'
    (directory / 'offline-report.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'status=PASS diagnostic_gate={gate} result_dir={directory}', flush=True)
    return summary


def host(baseline_commands=False, baseline_add_trace=False):
    assert not baseline_add_trace or baseline_commands
    from cpu_frequency_lease import save
    from diagnose_exp1_cpu_variance import cgroup
    from experiment_disk_guard import writer_lock
    out = ROOT / ('.artifacts/baseline-add-trace-calibration' if baseline_add_trace else ('.artifacts/baseline-git-calibration' if baseline_commands else '.artifacts/runner-phase-calibration')) / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    os.umask(0o077); out.mkdir(parents=True)
    result = dict(status='FAIL', cleanup='NOT_NEEDED', runs=[])
    env = dict(os.environ, PATH=str(ROOT / 'runtime/bin') + ':' + os.environ['PATH'], XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))
    sid = None; helper = None; helper_log = None
    def call(label, argv, timeout=30):
        from experiment_disk_guard import guarded_run
        # Keep machine-readable stdout separate; cleanup remains possible below the disk reserve.
        log_path = out / (label + '.stdout')
        with log_path.open('w') as log, (out / (label + '.stderr')).open('w') as stderr:
            guarded_run(argv, directory=out, log=log, stderr_log=stderr, timeout=timeout, env=env,
                        reserve=0 if label in ('delete', 'list-after') else 20 * 1024**3)
        return log_path.read_text().strip()
    print(f'status=RUNNING result_dir={out}', flush=True)
    with writer_lock():
        try:
            assert shutil.disk_usage(ROOT).free > 32 * 1024**3
            assert json.loads(call('list-before', ['aenv', 'list', '--output', 'json'])) == []
            tools = out / 'tools'; tools.mkdir()
            for name in ('guest_runner_phases.py', 'calibrate_runner_phases.py', 'calibrate_git_host_wait.py', 'command_clock.c', 'guest_damon_control.py', 'git_step_diagnostic.py', 'guest_diskstats.py'):
                shutil.copy2(ROOT / 'scripts' / name, tools / name)
            senior = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke'
            shutil.copy2(senior / 'systems/agentenv/guest-runner.sh', tools / 'guest-runner-original.sh')
            actions = tools / 'actions'; actions.mkdir(); shutil.copy2(senior / 'workloads/prettier-14400/actions/025.sh', actions / '025.sh')
            shutil.copy2(senior / 'workloads/prettier-14400/actions.tsv', tools / 'actions.tsv')
            call('build', ['gcc', '-O2', '-static', '-Wall', '-Wextra', '-Werror', str(tools / 'command_clock.c'), '-o', str(tools / 'command-clock')])
            save(out / 'inputs.json', {str(p.relative_to(tools)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tools.rglob('*') if p.is_file()})
            save(out / 'protocol.json', dict(baseline_commands=baseline_commands,baseline_add_trace=baseline_add_trace,order=ORDER, iterations=ITERATIONS, warmup=ITERATIONS, cache_policy='DAMON disabled in new owned guest, no drop_caches', gate='abs AA <=5% and AB <=5% for command and controller phase', parsing='outside measured phase', monitoring_acceptance=False))
            cfg = json.loads((ROOT / 'configs/exp1-agentenv.json').read_text())
            image = json.loads((ROOT / 'configs/exp1-monitor-calibration.json').read_text())['workload_image']
            cid = (ROOT / cfg['server_result'] / 'container-id.txt').read_text().strip()
            group = cgroup(call('service-pid', ['docker', 'inspect', '--format', '{{.State.Pid}}', cid]))
            sid = call('create', ['aenv', 'start', '--cold', image, '--detach', '--timeout', '300', '--cpu', '2', '--memory', '4096'], 90)
            result.update(sandbox_id=sid, image=image)
            call('damon', ['python3', str(tools / 'guest_damon_control.py'), sid, 'damon_disabled', 'prepare', str(out / 'damon-prepare.json')])
            call('upload', ['aenv', 'upload', sid, str(tools), '/workspace/runner-phase'], 45)
            call('chmod', ['aenv', 'exec', sid, 'chmod', '+x', '/workspace/runner-phase/command-clock'])
            call('prepare', ['aenv', 'exec', sid, 'python3', '/workspace/runner-phase/calibrate_git_host_wait.py', '--guest', '/workspace/runner-phase', '--phase', 'prepare'], 60)
            helper_log = (out / 'frequency.log').open('w')
            helper = subprocess.Popen([sys.executable, str(ROOT / 'scripts/fixed_frequency_window.py'), sid, str(out / 'fixed-frequency'), str(ROOT / 'runtime'), group], env=env, stdout=helper_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 45
            while not (out / 'fixed-frequency/ready.json').exists():
                assert helper.poll() is None and time.monotonic() < deadline
                time.sleep(.05)
            for number, (comparison, label, enabled) in enumerate([('warmup', 'A', False)] + ORDER):
                phase = f'{comparison}-{number}-{label}'
                call(phase, ['aenv', 'exec', sid, 'python3', '/workspace/runner-phase/calibrate_runner_phases.py', '--guest', '/workspace/runner-phase', '--phase', phase] + (['--enabled'] if enabled else []) + (['--baseline-commands'] if baseline_commands else []) + (['--baseline-add-trace'] if baseline_add_trace else []), 45)
                if comparison != 'warmup':
                    measurement = json.loads(call(phase + '-result', ['aenv', 'exec', sid, 'cat', f'/workspace/runner-phase/{phase}/result.json']))
                    result['runs'].append(dict(comparison=comparison, label=label, enabled=enabled, phase=phase, measurement=measurement)); save(out / 'progress.json', result)
                print(f'phase={phase} status=PASS', flush=True)
            call('download', ['aenv', 'download', sid, '/workspace/runner-phase', str(out / 'raw')], 45)
            result['status'] = 'PASS'
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            if helper:
                try:
                    (out / 'fixed-frequency/stop').touch()
                    try: helper.wait(timeout=25)
                    except subprocess.TimeoutExpired: helper.terminate(); helper.wait(timeout=20)
                    result['frequency'] = json.loads((out / 'fixed-frequency/result.json').read_text())
                    if result['frequency']['status'] != 'PASS' or result['frequency'].get('restoration', {}).get('status') != 'PASS': result['status'] = 'FAIL'
                except Exception as exc:
                    result.update(status='FAIL', frequency_cleanup_error=repr(exc))
            if helper_log: helper_log.close()
            if sid:
                try:
                    call('delete', ['aenv', 'delete', sid], 90)
                    assert not any(x['sandboxID'] == sid for x in json.loads(call('list-after', ['aenv', 'list', '--output', 'json'])))
                    result['cleanup'] = 'PASS'
                except Exception as exc: result.update(status='FAIL', cleanup='FAIL', cleanup_error=repr(exc))
            result['free_bytes_after'] = shutil.disk_usage(ROOT).free; save(out / 'result.json', result)
    print(f"status={result['status']} cleanup={result['cleanup']} result_dir={out}", flush=True)
    if result['status'] != 'PASS': return 1
    return 0 if report(out)['diagnostic_gate'] == 'PASS' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', type=Path); parser.add_argument('--phase'); parser.add_argument('--enabled', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--baseline-commands',action='store_true')
    parser.add_argument('--baseline-add-trace',action='store_true')
    args = parser.parse_args()
    assert not args.baseline_add_trace or args.baseline_commands
    if args.guest: guest(args.guest, args.phase, args.enabled,args.baseline_commands,args.baseline_add_trace)
    elif args.report: report(args.report.resolve())
    else: raise SystemExit(host(args.baseline_commands,args.baseline_add_trace))
