"""Replay Exp1 around one persistent AgentENV snapshot and verify continuation."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import tomllib

root = pathlib.Path(__file__).resolve().parent.parent
run_started_ns = time.monotonic_ns()
cfg = json.loads((root / 'configs/exp1-agentenv.json').read_text())
repo = root / cfg['repository']
workload = repo / 'motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
runner = repo / 'motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
monitor_script = repo / 'motivation/experiments/lib/host-memory-monitor.sh'
baseline = root / cfg['output_root'] / 'raw/20260924T035910Z-1677178/replay/patch.diff'
run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/platform-exp1-checkpoint'
raw = out / 'raw' / run_id
audit = out / 'audit' / run_id
os.umask(0o077)
raw.mkdir(parents=True)
audit.mkdir(parents=True)
env = dict(os.environ, PATH=str(root / 'runtime/bin') + ':' + os.environ['PATH'],
           XDG_CONFIG_HOME=str(root / 'runtime/config'))
events = []
sandbox_id = None
snapshot_alias = f'exp1-checkpoint-{run_id}'
snapshot_attempted = False
monitor = None
phase = raw / '.host-monitor-phase'
checks = {}
status = 'FAIL'


def call(label, argv, timeout=120):
    begin = time.monotonic_ns()
    with (audit / f'{label}.log').open('w') as log:
        try:
            result = subprocess.run(argv, env=env, cwd=repo, stdout=subprocess.PIPE,
                                    stderr=log, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            events.append(dict(stage=label, duration_ns=time.monotonic_ns() - begin, status='TIMEOUT'))
            raise
    events.append(dict(stage=label, duration_ns=time.monotonic_ns() - begin,
                       status='PASS' if result.returncode == 0 else f'EXIT_{result.returncode}'))
    (audit / f'{label}.stdout').write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f'{label} exited {result.returncode}; see audit log')
    return result.stdout.strip()


def probe(text):
    return dict(line.split('=', 1) for line in text.splitlines())


def exec_guest(label, command, timeout=120):
    return call(label, ['aenv', 'exec', sandbox_id, 'bash', '-lc', command], timeout)


def list_items(kind):
    return json.loads(call('list-' + kind, ['aenv', *kind.split(), 'list', '--output', 'json'], 20))


print(f'status=RUNNING run_id={run_id} result_dir={raw}', flush=True)
try:
    cid = (root / cfg['server_result'] / 'container-id.txt').read_text().strip()
    info = json.loads(call('docker-inspect', ['docker', 'inspect', cid], 20))[0]
    assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment') == 'initial-smoke'
    credentials = tomllib.loads((root / 'runtime/config/aenv/credentials').read_text())
    assert credentials['url'] == 'http://' + (root / cfg['server_result'] / 'address.txt').read_text().strip()
    assert list_items('') == [] and list_items('template') == []
    io_engine = json.loads(call('overlaybd-config', ['docker', 'exec', cid,
        'cat', '/workspace/env/overlaybd/overlaybd-global.json'], 20))['ioEngine']
    assert baseline.is_file()
    rows = [line.split('\t', 2) for line in (workload / 'actions.tsv').read_text().splitlines()]
    assert [row[0] for row in rows] == [f'{i:03}' for i in range(1, 27)]
    for step, digest, _ in rows:
        assert hashlib.sha256((workload / 'actions' / f'{step}.sh').read_bytes()).hexdigest() == digest
    checks['input_hashes'] = 'PASS'
    config_text = (repo / 'motivation/experiments/exp1-single-app-smoke/systems/agentenv/config.env').read_text()
    image = re.search(r"WORKLOAD_IMAGE=\$\{WORKLOAD_IMAGE:-'([^']+)'\}", config_text).group(1)
    (audit / 'effective-config.json').write_text(json.dumps(dict(cfg, run_id=run_id,
        checkpoint_after_step='013', continuation_start_step='014', image=image,
        server_container=cid, server_image_id=info['Image'], cache_policy='preserve',
        backend_state='complete VM memory and filesystem snapshot; process continuity tested',
        comparison_run='20260924T035910Z-1677178'), indent=2) + '\n')
    (audit / 'source-version.txt').write_text(call('source-version', ['git', '-C', str(repo), 'rev-parse', 'HEAD']))
    (audit / 'source-changes.patch').write_text(call('source-changes', ['git', '-C', str(repo), 'diff']))
    (raw / 'metadata.env').write_text(f'run_id={run_id}\nworkload_image={image}\n'
        f'sandbox_cpu={cfg["sandbox_cpu"]}\nsandbox_memory_mib={cfg["sandbox_memory_mib"]}\n'
        f'overlaybd_io_engine={io_engine}\ndrop_guest_caches=0\nsample_interval_seconds={cfg["sample_interval_seconds"]}\n')
    begin = time.monotonic_ns()
    sandbox_id = call('cold-start', ['aenv', 'start', '--cold', image, '--detach', '--timeout', '1800',
                         '--cpu', str(cfg['sandbox_cpu']), '--memory', str(cfg['sandbox_memory_mib'])], 150)
    assert re.fullmatch(r'[0-9a-fA-F-]{36}', sandbox_id)
    (raw / 'cold-start-duration-ns.txt').write_text(str(time.monotonic_ns() - begin) + '\n')
    (raw / 'sandbox-ids.txt').write_text(sandbox_id + '\n')
    exec_guest('initial-ready', 'true')
    for label, source, dest in (
        ('upload-actions', workload / 'actions', '/workspace/replay-actions'),
        ('upload-manifest', workload / 'actions.tsv', '/workspace/replay-actions.tsv'),
        ('upload-runner', runner, '/workspace/guest-runner.sh'),
        ('upload-probe', root / 'scripts/exp1-process-probe.sh', '/workspace/exp1-process-probe.sh')):
        call(label, ['aenv', 'upload', sandbox_id, str(source), dest])
    first_probe = probe(exec_guest('probe-before', 'bash /workspace/exp1-process-probe.sh start'))
    phase.write_text('idle\n')
    host_path = raw / 'host/memory-samples.tsv'
    host_path.parent.mkdir()
    with (audit / 'host-monitor.log').open('w') as log:
        monitor = subprocess.Popen(['bash', str(monitor_script), str(host_path), str(phase),
                   str(cfg['sample_interval_seconds']), cid], env=env, cwd=repo, stdout=log, stderr=log)
    time.sleep(0.2)
    common = (f"ACTION_TIMEOUT_SECONDS={cfg['action_timeout_seconds']} "
              f"MEMORY_SAMPLE_INTERVAL_SECONDS={cfg['sample_interval_seconds']} "
              'DROP_GUEST_CACHES=0 ')
    phase.write_text('running\n')
    exec_guest('actions-001-013', common + 'START_STEP=001 END_STEP=013 bash /workspace/guest-runner.sh', 180)
    before = probe(exec_guest('probe-at-checkpoint', 'bash /workspace/exp1-process-probe.sh'))
    assert all(before[key] == first_probe[key] for key in ('pid', 'start_ticks', 'boot_id'))
    phase.write_text('checkpoint\n')
    snapshot_attempted = True
    call('checkpoint', ['aenv', 'snapshot', 'create', sandbox_id, '--name', snapshot_alias], 150)
    checks['checkpoint_interface'] = 'PASS'
    call('delete-original', ['aenv', 'delete', sandbox_id], 60)
    with (raw / 'cleanup.log').open('a') as f:
        f.write(f'deleted={sandbox_id}\n')
    sandbox_id = None
    phase.write_text('restore\n')
    sandbox_id = call('restore', ['aenv', 'start', snapshot_alias, '--detach', '--timeout', '1800'], 150)
    assert re.fullmatch(r'[0-9a-fA-F-]{36}', sandbox_id)
    with (raw / 'sandbox-ids.txt').open('a') as f:
        f.write(sandbox_id + '\n')
    exec_guest('restored-ready', 'true')
    after = probe(exec_guest('probe-after-restore', 'sleep 0.3; bash /workspace/exp1-process-probe.sh'))
    assert all(after[key] == before[key] for key in ('pid', 'start_ticks', 'boot_id'))
    assert int(after['count']) > int(before['count'])
    checks['process_continuity'] = 'PASS'
    (audit / 'process-continuity.json').write_text(json.dumps(dict(before=before, after=after), indent=2) + '\n')
    phase.write_text('running\n')
    exec_guest('actions-014-026', common + 'START_STEP=014 END_STEP=026 bash /workspace/guest-runner.sh', 180)
    phase.write_text('download\n')
    call('download-replay', ['aenv', 'download', sandbox_id, '/workspace/artifacts/replay', str(raw)], 90)
    with (raw / 'replay/steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    assert [row['step'] for row in steps] == [row[0] for row in rows]
    assert {row['step']: int(row['exit_code']) for row in steps if int(row['exit_code'])} == {'008': 127, '016': 127}
    output24 = (raw / 'replay/stdout/024.log').read_text()
    assert '    document.addEventListener("DOMContentLoaded", () => {' in output24
    assert '      const node = document.getElementById("lastStroke");' in output24
    (raw / 'oracle-exit-code.txt').write_text('0\n')
    patch = (raw / 'replay/patch.diff').read_bytes()
    assert patch == baseline.read_bytes()
    checks['replay_correctness'] = 'PASS'
    checks['patch_matches_full_replay'] = 'PASS'
    call('delete-restored', ['aenv', 'delete', sandbox_id], 60)
    with (raw / 'cleanup.log').open('a') as f:
        f.write(f'deleted={sandbox_id}\n')
    sandbox_id = None
    call('delete-snapshot', ['aenv', 'template', 'delete', snapshot_alias], 60)
    with (raw / 'cleanup.log').open('a') as f:
        f.write(f'deleted_snapshot={snapshot_alias}\n')
    snapshot_attempted = False
    assert list_items('') == [] and list_items('template') == []
    checks['cleanup'] = 'PASS'
    phase.write_text('done\n')
    monitor.send_signal(signal.SIGTERM)
    monitor.wait(timeout=10)
    monitor = None
    with host_path.open() as f:
        assert len(list(csv.DictReader(f, delimiter='\t'))) > 0
    checks['host_sampling'] = 'PASS'
    (out / 'latest-run.txt').write_text(run_id + '\n')
    call('report', ['python3', str(repo / 'motivation/experiments/exp1-single-app-smoke/systems/agentenv/analyze.py'), str(out)])
    status = 'PASS'
except Exception as exc:
    import traceback
    (audit / 'failure.txt').write_text(traceback.format_exc())
    print(f'failure={type(exc).__name__}: {exc}', flush=True)
finally:
    if monitor is not None:
        monitor.send_signal(signal.SIGTERM)
        try: monitor.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor.kill(); monitor.wait()
    phase.unlink(missing_ok=True)
    if sandbox_id:
        try: call('cleanup-sandbox', ['aenv', 'delete', sandbox_id], 60)
        except Exception: checks['cleanup_sandbox'] = 'FAIL'
    if snapshot_attempted:
        try: call('cleanup-snapshot', ['aenv', 'template', 'delete', snapshot_alias], 60)
        except Exception: checks['cleanup_snapshot'] = 'FAIL'
    (audit / 'events.json').write_text(json.dumps(events, indent=2) + '\n')
    (audit / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    (audit / 'result.json').write_text(json.dumps(dict(status=status, raw_dir=str(raw),
        controller_elapsed_ns=time.monotonic_ns() - run_started_ns), indent=2) + '\n')
    print(f'status={status} run_id={run_id} result_dir={raw} audit_dir={audit}', flush=True)
sys.exit(0 if status == 'PASS' else 1)
