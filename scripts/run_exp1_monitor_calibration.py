"""Same-VM calibration using repeated frozen Exp1 action 024 after full replay."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import signal
import statistics
import subprocess
import sys
import time
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert cfg['variant'] in ('smoke', 'calibration')
assert cfg['workload_image'].endswith('@sha256:e625c9b9776870e2cc87172e886bbc90cfc3d6ce1521492f113a46b3f6dfcf44')
assert cfg['sandbox_cpu'] == 2 and cfg['sandbox_memory_mib'] == 4096
assert cfg['phase_iterations'] in (4, 32) and cfg['warmup_iterations'] in (2, 8)
assert len({p['name'] for p in cfg['phases']}) == len(cfg['phases'])
if cfg['variant'] == 'smoke':
    assert [(p['name'], p['mode']) for p in cfg['phases']] == [('off', 'off'), ('performance', 'performance')]
else:
    expected_modes = {'aa-A1': 'off', 'aa-A2': 'off', 'aa-B1': 'off', 'aa-B2': 'off',
                      'ab-off1': 'off', 'ab-off2': 'off',
                      'ab-performance1': 'performance', 'ab-performance2': 'performance'}
    assert {p['name']: p['mode'] for p in cfg['phases']} == expected_modes

senior = root / 'IncrementalDAX_moti'
workload = senior / 'motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
guest_runner = senior / 'motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
monitor_script = senior / 'motivation/experiments/lib/host-memory-monitor.sh'
reference = root / '.artifacts/platform-exp1-agentenv-default/raw/20260924T035910Z-1677178/replay'
expected_patch = hashlib.sha256((reference / 'patch.diff').read_bytes()).hexdigest()
expected_stdout = hashlib.sha256((reference / 'stdout/024.log').read_bytes()).hexdigest()
series_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/exp1-monitor-calibration' / series_id
os.umask(0o077)
out.mkdir(parents=True, exist_ok=False)
(out / 'effective-config.json').write_text(json.dumps(cfg, indent=2) + '\n')
snapshot = out / 'code-snapshot'
snapshot.mkdir()
for path in (pathlib.Path(__file__), root / 'scripts/24-run-exp1-monitor-calibration.sh',
             pathlib.Path(sys.argv[1]), guest_runner, monitor_script,
             workload / 'actions/024.sh', workload / 'actions.tsv'):
    shutil.copy2(path, snapshot / path.name)
(snapshot / 'sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in snapshot.iterdir()}, indent=2) + '\n')

server_dir = root / '.artifacts/server-smoke-20260922T114006Z-1387374'
cid = (server_dir / 'container-id.txt').read_text().strip()
env = dict(os.environ, PATH=str(root / 'runtime/bin') + ':' + os.environ['PATH'],
           XDG_CONFIG_HOME=str(root / 'runtime/config'), AGENTENV_CONTAINER=cid)
action_command = ['bash', '-lc', 'cd /testbed && bash /workspace/replay-actions/024.sh']
sid = None
active_monitor = None
phases = []
status = 'FAIL'


def stop_requested(signum, frame):
    raise RuntimeError(f'received signal {signum}')


signal.signal(signal.SIGTERM, stop_requested)


def run(args, timeout, stdout_path=None, stderr_path=None):
    result = subprocess.run(args, env=env, cwd=root, capture_output=True, timeout=timeout)
    if stdout_path:
        stdout_path.write_bytes(result.stdout)
    if stderr_path:
        stderr_path.write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f'command failed rc={result.returncode}: {args[:3]} stderr={result.stderr[-400:].decode(errors="replace")}')
    return result


def host_context():
    return dict(loadavg=os.getloadavg(),
                pressure={name: (pathlib.Path('/proc/pressure') / name).read_text().strip()
                          for name in ('cpu', 'io', 'memory')})


def stop_monitor():
    global active_monitor
    if active_monitor is None:
        return
    try:
        os.killpg(active_monitor.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        active_monitor.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(active_monitor.pid, signal.SIGKILL)
        active_monitor.wait()
    active_monitor = None


def run_action(stdout_path, stderr_path):
    start = time.monotonic_ns()
    result = subprocess.run(['aenv', 'exec', sid, *action_command], env=env, cwd=root,
                            capture_output=True, timeout=90)
    returned = time.monotonic_ns()
    stdout_path.write_bytes(result.stdout)
    stderr_path.write_bytes(result.stderr)
    archived = time.monotonic_ns()
    result.check_returncode()
    elapsed = archived - start
    digest = hashlib.sha256(result.stdout).hexdigest()
    assert digest == expected_stdout, (digest, expected_stdout, stdout_path)
    return elapsed, digest, hashlib.sha256(result.stderr).hexdigest(), returned-start, archived-returned


def run_phase(index, phase):
    global active_monitor
    name, sampled = phase['name'], phase['mode'] == 'performance'
    phase_dir = out / 'phases' / f'{index:02}-{name}'
    phase_dir.mkdir(parents=True)
    (phase_dir / 'stdout').mkdir()
    (phase_dir / 'stderr').mkdir()
    before = host_context()
    setup_ns = 0
    monitor_path = phase_dir / 'host-memory.tsv'
    phase_file = phase_dir / 'phase.txt'
    if sampled:
        setup_start = time.monotonic_ns()
        phase_file.write_text('running\n')
        monitor_log = (phase_dir / 'monitor.log').open('wb')
        active_monitor = subprocess.Popen(['bash', str(monitor_script), str(monitor_path),
                                           str(phase_file), str(cfg['host_sample_interval_seconds']), cid],
                                          env=dict(env, HOST_MONITOR_LIGHT='1'), cwd=root,
                                          stdout=monitor_log, stderr=subprocess.STDOUT,
                                          start_new_session=True)
        monitor_log.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if monitor_path.exists() and len(monitor_path.read_text().splitlines()) >= 2:
                break
            assert active_monitor.poll() is None, f'monitor exited: {monitor_path}'
            time.sleep(0.05)
        else:
            raise TimeoutError(f'no first host sample: {monitor_path}')
        setup_ns = time.monotonic_ns() - setup_start
    step_rows = []
    window_start = time.monotonic_ns()
    try:
        for n in range(1, cfg['phase_iterations'] + 1):
            elapsed, stdout_sha, stderr_sha, invocation_ns, archive_ns = run_action(
                phase_dir / 'stdout' / f'{n:03}.log',
                phase_dir / 'stderr' / f'{n:03}.log')
            step_rows.append((n, elapsed, stdout_sha, stderr_sha, invocation_ns, archive_ns))
            with (phase_dir / 'calls.tsv').open('a') as f:
                if n == 1:
                    f.write('index\tduration_ns\tstdout_sha256\tstderr_sha256\tinvocation_ns\tarchive_ns\n')
                f.write(f'{n}\t{elapsed}\t{stdout_sha}\t{stderr_sha}\t{invocation_ns}\t{archive_ns}\n')
    finally:
        window_ns = time.monotonic_ns() - window_start
        teardown_start = time.monotonic_ns()
        stop_monitor()
        teardown_ns = time.monotonic_ns() - teardown_start if sampled else 0
    after = host_context()
    sample_count = 0
    if sampled:
        with monitor_path.open() as f:
            samples = list(csv.DictReader(f, delimiter='\t'))
        sample_count = len(samples)
        assert sample_count >= 2
        assert all(int(x['firecracker_pid']) == 0 for x in samples)
    else:
        assert not monitor_path.exists()
    assert len(step_rows) == cfg['phase_iterations']
    return dict(index=index, name=name, mode=phase['mode'],
                tool_window_ns=window_ns, monitor_setup_ns=setup_ns,
                monitor_teardown_ns=teardown_ns,
                total_phase_ns=setup_ns + window_ns + teardown_ns,
                call_duration_sum_ns=sum(x[1] for x in step_rows),
                invocation_sum_ns=sum(x[4] for x in step_rows),
                archive_sum_ns=sum(x[5] for x in step_rows),
                host_sample_count=sample_count, host_context_before=before,
                host_context_after=after, raw_dir=str(phase_dir))


def report():
    result = dict(status='PASS', timing_schema=2, series_id=series_id, variant=cfg['variant'],
                  sandbox_id=sid, expected_patch_sha256=expected_patch,
                  expected_stdout_024_sha256=expected_stdout, phases=phases,
                  phase_iterations=cfg['phase_iterations'],
                  interpretation='same-VM repeated-action calibration; not full-trajectory overhead acceptance')
    lines = [f'# Exp1 action 024 同 VM 监控校准：{cfg["variant"]}', '',
             '先在本沙箱完整重放 Exp1 的 26 步，再在相同文件状态下暖机并重复调用原轨迹第 024 步。每次输出与完整重放的该步 stdout SHA-256 一致。',
             '各阶段均在同一 VM 中顺序执行；性能阶段只采 host 服务 cgroup，每 1 秒一次。工具窗口不含监控启停，后者分别计时。', '',
             '计时 schema=2：invocation_ns 是 CLI 启动至返回，archive_ns 是 stdout/stderr 本地写入；duration_ns 保留两者之和。工具窗口还包含哈希检查和 TSV 写入。CLI 时间不能称为纯 RPC 或 guest 执行时间。', '',
             '| 阶段 | 模式 | 调用数 | 工具窗口 (ms) | 启动监控 (ms) | 停止监控 (ms) | host 样本数 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for p in phases:
        lines.append(f"| {p['name']} | {p['mode']} | {cfg['phase_iterations']} | {p['tool_window_ns']/1e6:.1f} | {p['monitor_setup_ns']/1e6:.1f} | {p['monitor_teardown_ns']/1e6:.1f} | {p['host_sample_count']} |")
    if cfg['variant'] == 'calibration':
        aa = {x['name']: x for x in phases if x['name'].startswith('aa-')}
        ab = {x['name']: x for x in phases if x['name'].startswith('ab-')}
        aa_a = statistics.mean(aa[x]['tool_window_ns'] for x in ('aa-A1', 'aa-A2'))
        aa_b = statistics.mean(aa[x]['tool_window_ns'] for x in ('aa-B1', 'aa-B2'))
        ab_off = statistics.mean(ab[x]['tool_window_ns'] for x in ('ab-off1', 'ab-off2'))
        ab_perf = statistics.mean(ab[x]['tool_window_ns'] for x in ('ab-performance1', 'ab-performance2'))
        result['aa_placebo_percent'] = 100 * (aa_b / aa_a - 1)
        result['ab_performance_vs_off_percent'] = 100 * (ab_perf / ab_off - 1)
        lines += ['', f"同 VM A/A 安慰剂窗口变化 {result['aa_placebo_percent']:+.2f}%；A/B 性能/无采样窗口变化 {result['ab_performance_vs_off_percent']:+.2f}%。这是单次校准，没有跨沙箱方差估计，不能据此判定 5% 验收目标。"]
    else:
        off, perf = phases
        result['smoke_performance_vs_off_percent'] = 100 * (perf['tool_window_ns'] / off['tool_window_ns'] - 1)
        lines += ['', f"smoke 工具窗口变化 {result['smoke_performance_vs_off_percent']:+.2f}%；只检查流程与输出，不作开销结论。"]
    lines += ['', '原始每次调用的 stdout/stderr、耗时、host 样本、主机压力快照、生效配置与代码快照均保留在本目录。该重复动作诊断与完整 26 步轨迹的端到端开销是不同口径。', '']
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'report.md').write_text('\n'.join(lines))


print(f'status=RUNNING series_id={series_id} result_dir={out}', flush=True)
try:
    info = json.loads(run(['docker', 'inspect', cid], timeout=15).stdout)[0]
    assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment') == 'initial-smoke'
    assert json.loads(run(['aenv', 'list', '--output', 'json'], timeout=15).stdout) == []
    manifest_rows = [line.split('\t', 2) for line in (workload / 'actions.tsv').read_text().splitlines()]
    assert [x[0] for x in manifest_rows] == [f'{i:03}' for i in range(1, 27)]
    for step, digest, _ in manifest_rows:
        assert hashlib.sha256((workload / 'actions' / f'{step}.sh').read_bytes()).hexdigest() == digest
    (out / 'server-image-id.txt').write_text(info['Image'] + '\n')
    (out / 'senior-git-head.txt').write_text(run(['git', '-C', str(senior), 'rev-parse', 'HEAD'], timeout=15).stdout.decode())
    sid = run(['aenv', 'start', '--cold', cfg['workload_image'], '--detach', '--timeout', '1800',
               '--cpu', str(cfg['sandbox_cpu']), '--memory', str(cfg['sandbox_memory_mib'])],
              timeout=180, stdout_path=out / 'cold-start.stdout.log',
              stderr_path=out / 'cold-start.stderr.log').stdout.decode().strip()
    assert len(sid) == 36
    (out / 'sandbox-id.txt').write_text(sid + '\n')
    for attempt in range(60):
        try:
            run(['aenv', 'exec', sid, 'true'], timeout=15)
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(1)
    run(['aenv', 'upload', sid, str(workload / 'actions'), '/workspace/replay-actions'], timeout=120)
    run(['aenv', 'upload', sid, str(workload / 'actions.tsv'), '/workspace/replay-actions.tsv'], timeout=120)
    run(['aenv', 'upload', sid, str(guest_runner), '/workspace/guest-runner.sh'], timeout=120)
    run(['aenv', 'exec', sid, 'bash', '-lc',
         'ACTION_TIMEOUT_SECONDS=60 MEMORY_SAMPLE_INTERVAL_SECONDS=1.0 GUEST_MEMORY_SAMPLING=0 DROP_GUEST_CACHES=0 bash /workspace/guest-runner.sh'],
        timeout=300, stdout_path=out / 'prep.stdout.log', stderr_path=out / 'prep.stderr.log')
    prep = out / 'prep'
    prep.mkdir()
    run(['aenv', 'download', sid, '/workspace/artifacts/replay', str(prep)], timeout=120,
        stdout_path=out / 'download.stdout.log', stderr_path=out / 'download.stderr.log')
    replay = prep / 'replay'
    assert hashlib.sha256((replay / 'patch.diff').read_bytes()).hexdigest() == expected_patch
    assert hashlib.sha256((replay / 'stdout/024.log').read_bytes()).hexdigest() == expected_stdout
    with (replay / 'steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    assert [x['step'] for x in steps] == [f'{i:03}' for i in range(1, 27)]
    assert {x['step']: int(x['exit_code']) for x in steps if int(x['exit_code'])} == {'008': 127, '016': 127}
    warmup_dir = out / 'warmup'
    warmup_dir.mkdir()
    for n in range(1, cfg['warmup_iterations'] + 1):
        run_action(warmup_dir / f'{n:03}.stdout.log', warmup_dir / f'{n:03}.stderr.log')
    print(f'status=PREP_PASS warmups={cfg["warmup_iterations"]} result_dir={out}', flush=True)
    for index, phase in enumerate(cfg['phases'], 1):
        phases.append(run_phase(index, phase))
        (out / 'partial-phases.json').write_text(json.dumps(phases, indent=2) + '\n')
        print(f'status=PHASE_PASS phase={index}/{len(cfg["phases"])} result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
finally:
    stop_monitor()
    if sid is not None:
        try:
            run(['aenv', 'delete', sid], timeout=90,
                stdout_path=out / 'cleanup.stdout.log', stderr_path=out / 'cleanup.stderr.log')
            listing = json.loads(run(['aenv', 'list', '--output', 'json'], timeout=15).stdout)
            assert not any(x['sandboxID'] == sid for x in listing)
            (out / 'cleanup-status.txt').write_text('PASS\n')
        except Exception:
            (out / 'cleanup-status.txt').write_text('FAIL\n')
            (out / 'cleanup-failure.txt').write_text(traceback.format_exc())
            status = 'FAIL'
if status == 'PASS':
    try:
        report()
    except Exception:
        (out / 'report-failure.txt').write_text(traceback.format_exc())
        status = 'FAIL'
print(f'status={status} series_id={series_id} result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
