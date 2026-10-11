"""Calibrate buffered libc timing in a disposable, owned AgentENV guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def guest(base, coarse=False):
    fixture = base / 'fixture'
    fixture.mkdir()
    for i in range(256):
        directory = fixture / f'd{i:03d}'
        directory.mkdir()
        for j in range(32):
            (directory / f'f{j:03d}').touch()
    rows = []
    reference = None

    def one(label, enabled, rounds=32, exit_code=0):
        nonlocal reference
        output = base / label
        output.mkdir()
        env = dict(os.environ)
        env.pop('LD_PRELOAD', None)
        env.pop('DIRECTORY_PROFILE_DIR', None)
        if enabled:
            env.update(LD_PRELOAD=str(base / 'directory-profile.so'),
                       DIRECTORY_PROFILE_DIR=str(output))
        started = time.perf_counter_ns()
        p = subprocess.run([str(base / 'directory-fixture'), str(fixture),
                            str(rounds), str(exit_code)], env=env,
                           capture_output=True, text=True, timeout=20)
        elapsed = time.perf_counter_ns() - started
        (output / 'stdout').write_text(p.stdout)
        (output / 'stderr').write_text(p.stderr)
        assert p.returncode == exit_code and not p.stderr, (label, p.returncode, p.stderr)
        value = json.loads(p.stdout)
        assert value['entries'] == rounds * 256 * 32
        assert value['errno_checks'] == 'PASS'
        if reference is None:
            reference = value
        if rounds == 32:
            assert value == reference
        records = list(output.glob('directory-*.json'))
        assert len(records) == int(enabled)
        if enabled:
            calls = json.loads(records[0].read_text())['calls']
            assert calls['opendir']['count'] == rounds * 256 + 1
            assert calls['closedir']['count'] == rounds * 256
            assert calls['opendir']['null_with_errno'] == 1
            if not coarse:
                assert calls['readdir']['count'] + calls['readdir64']['count'] == rounds * 256 * 36
                assert sum(calls[k]['null_without_errno'] for k in ('readdir', 'readdir64')) == rounds * 256
                assert sum(calls[k]['null_with_errno'] for k in ('readdir', 'readdir64')) == rounds * 256
        row = dict(label=label, enabled=enabled, elapsed_ns=elapsed, exit_code=p.returncode,
                   stdout_sha256=hashlib.sha256(p.stdout.encode()).hexdigest())
        rows.append(row)
        save(base / 'progress.json', rows)
        return elapsed

    for i, enabled in enumerate((False, True, True, False)):
        one(f'warmup-{i}', enabled)
    one('nonzero-exit', True, exit_code=19)
    differences = {'aa': [], 'ab': []}
    for group in range(5):
        # Alternate AA/AB group order; each mode occupies early and late positions.
        for comparison in (('aa', 'ab') if group % 2 == 0 else ('ab', 'aa')):
            order = ('A', 'B', 'B', 'A') if group % 2 == 0 else ('B', 'A', 'A', 'B')
            values = {'A': [], 'B': []}
            for position, label in enumerate(order):
                elapsed = one(f'{comparison}-{group}-{position}-{label}',
                              comparison == 'ab' and label == 'B')
                values[label].append(elapsed)
            differences[comparison].append(100 * (statistics.mean(values['B']) / statistics.mean(values['A']) - 1))
    aa = statistics.mean(differences['aa'])
    ab = statistics.mean(differences['ab'])
    # Conservative diagnostic gate, not formal platform monitoring acceptance.
    aa_max = max(map(abs, differences['aa']))
    ab_max = max(differences['ab'])
    gate = 'PASS' if aa_max <= 5 and ab_max <= 5 else 'NOT_ACCEPTED'
    result = dict(status='PASS', correctness='PASS', diagnostic_gate=gate, coarse=coarse,
                  gate_rule='each of five AA differences within +/-5%; each AB increase <=5%',
                  aa_percent=differences['aa'], ab_percent=differences['ab'],
                  aa_mean_percent=aa, ab_mean_percent=ab, runs=rows,
                  timing='controller perf_counter_ns around child including load and exit summary I/O',
                  scope='warm synthetic directories; CPU policy recorded in host protocol; not Exp1 monitoring acceptance')
    save(base / 'result.json', result)


def host(coarse=False, fixed_frequency=False):
    from experiment_disk_guard import writer_lock
    out = ROOT / ('.artifacts/directory-open-close' if coarse else '.artifacts/directory-profile') / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    out.mkdir(parents=True)
    bundle = out / 'tools'
    bundle.mkdir()
    result = dict(status='FAIL', cleanup='NOT_NEEDED')
    save(out / 'protocol.json', dict(coarse=coarse, fixed_frequency=fixed_frequency, groups=5, rounds_per_child=32,
         directories=256, files_per_directory=32, timeout_seconds=120,
         gate_rule='each of five AA differences within +/-5%; each AB increase <=5%',
         scope='warm fixture diagnostic gate, not platform monitoring acceptance'))
    env = dict(os.environ, PATH=str(ROOT / 'runtime/bin') + ':' + os.environ['PATH'],
               XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))

    def call(label, args, timeout=30):
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
        (out / (label + '.stdout')).write_text(p.stdout)
        (out / (label + '.stderr')).write_text(p.stderr)
        p.check_returncode()
        return p.stdout.strip()

    print(f'status=RUNNING result_dir={out}', flush=True)
    with writer_lock():
        sid = None
        helper = None
        helper_log = None
        try:
            assert shutil.disk_usage(ROOT).free > 32 * 1024**3
            assert json.loads(call('list-before', ['aenv', 'list', '--output', 'json'])) == []
            for name in ('directory_profile.c', 'directory_fixture.c', 'calibrate_directory_profile.py'):
                shutil.copy2(ROOT / 'scripts' / name, bundle / name)
            call('build-profile', ['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                                  *(['-DDIRECTORY_OPEN_CLOSE_ONLY'] if coarse else []), str(bundle / 'directory_profile.c'), '-ldl', '-pthread', '-o', str(bundle / 'directory-profile.so')])
            call('build-fixture', ['gcc', '-O2', '-Wall', '-Wextra', '-Werror', str(bundle / 'directory_fixture.c'), '-o', str(bundle / 'directory-fixture')])
            for binary in ('directory-fixture', 'directory-profile.so'):
                call(binary + '-versions', ['readelf', '--version-info', str(bundle / binary)])
            save(out / 'inputs.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()})
            cfg = json.loads((ROOT / 'configs/exp1-monitor-calibration.json').read_text())
            result['image'] = cfg['workload_image']
            sid = call('create', ['aenv', 'start', '--cold', cfg['workload_image'], '--detach', '--timeout', '240', '--cpu', '2', '--memory', '4096'], 90)
            result['sandbox_id'] = sid
            save(out / 'progress.json', result)
            call('upload', ['aenv', 'upload', sid, str(bundle), '/workspace/directory-profile'], 45)
            call('chmod', ['aenv', 'exec', sid, 'chmod', '+x', '/workspace/directory-profile/directory-fixture'])
            result['guest_versions'] = call('versions', ['aenv', 'exec', sid, 'bash', '-lc', 'uname -r; getconf GNU_LIBC_VERSION; python3 --version'])
            if fixed_frequency:
                from diagnose_exp1_cpu_variance import cgroup
                cfg = json.loads((ROOT / 'configs/exp1-agentenv.json').read_text())
                cid = (Path(cfg['server_result']) / 'container-id.txt').read_text().strip()
                service_pid = call('service-pid', ['docker', 'inspect', '--format', '{{.State.Pid}}', cid])
                sources = out / 'host-sources'
                sources.mkdir()
                for name in ('fixed_frequency_window.py', 'cpu_frequency_lease.py', 'diagnose_exp1_cpu_variance.py'):
                    shutil.copy2(ROOT / 'scripts' / name, sources / name)
                save(out / 'host-inputs.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources.iterdir()})
                helper_log = (out / 'frequency.log').open('w')
                helper = subprocess.Popen([sys.executable, str(ROOT / 'scripts/fixed_frequency_window.py'), sid,
                    str(out / 'fixed-frequency'), str(ROOT / 'runtime'), cgroup(service_pid)],
                    stdout=helper_log, stderr=subprocess.STDOUT, env=env)
                deadline = time.monotonic() + 45
                while not (out / 'fixed-frequency/ready.json').exists():
                    assert helper.poll() is None and time.monotonic() < deadline, 'frequency helper not ready'
                    time.sleep(.05)
                result['frequency_identity'] = json.loads((out / 'fixed-frequency/ready.json').read_text())
            try:
                call('calibrate' , ['aenv', 'exec', sid, 'timeout', '--signal=TERM', '--kill-after=5s', '120s', 'python3', '/workspace/directory-profile/calibrate_directory_profile.py', '--guest', '/workspace/directory-profile'] + (['--coarse'] if coarse else []), 140)
            finally:
                call('download', ['aenv', 'download', sid, '/workspace/directory-profile', str(out / 'guest')], 45)
            if helper:
                assert helper.poll() is None, 'frequency lease ended before calibration completed'
            measured = json.loads((out / 'guest/result.json').read_text())
            result.update(status=measured['status'], calibration=measured)
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            if helper:
                (out / 'fixed-frequency/stop').touch()
                try:
                    assert helper.wait(timeout=55) == 0
                    result['frequency'] = json.loads((out / 'fixed-frequency/result.json').read_text())
                    assert result['frequency']['restoration']['status'] == 'PASS'
                except Exception as exc:
                    result.update(status='FAIL', frequency_error=repr(exc))
                helper_log.close()
            if sid:
                try:
                    call('delete', ['aenv', 'delete', sid], 90)
                    assert not any(x['sandboxID'] == sid for x in json.loads(call('list-after', ['aenv', 'list', '--output', 'json'])))
                    result['cleanup'] = 'PASS'
                except Exception as exc:
                    result.update(status='FAIL', cleanup='FAIL', cleanup_error=repr(exc))
            result['free_bytes_after'] = shutil.disk_usage(ROOT).free
            save(out / 'result.json', result)
    print(f"status={result['status']} gate={result.get('calibration', {}).get('diagnostic_gate', 'NOT_RUN')} cleanup={result['cleanup']} result_dir={out}", flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', type=Path)
    parser.add_argument('--coarse', action='store_true', help='Only time opendir and closedir; do not intercept readdir')
    parser.add_argument('--fixed-frequency', action='store_true', help='Use previously authorized temporary CPU policy with automatic restoration')
    args = parser.parse_args()
    if args.guest:
        guest(args.guest, args.coarse)
    else:
        raise SystemExit(host(args.coarse, args.fixed_frequency))
