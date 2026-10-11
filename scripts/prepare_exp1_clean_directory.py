"""Build an empty-directory Exp1 reproduction bundle without old raw results or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--v1', action='store_true', help='include both backends and the selected host-only profile')
    parser.add_argument('--backend', choices=('agentenv', 'trenvx'), default='agentenv',
                        help='select the fresh-directory backend; keep TrEnv-X patch checks independent of AgentENV hooks')
    parser.add_argument('--official-task', type=Path, help='prepared pinned task inputs, copied without full dataset')
    args = parser.parse_args()
    dest = args.destination.resolve()
    dest.mkdir(parents=True, exist_ok=False)
    paths = ['scripts/run_exp1.py', 'scripts/run_exp1_monitor_balanced.py',
             'scripts/experiment_disk_guard.py', 'scripts/prepare_exp1_clean_directory.py',
             'patches/incrementaldax-platform.patch', 'configs/source-revisions.json']
    if args.v1:
        paths += ['scripts/'+name for name in (
            'run_exp1_checkpoint.py', 'report_exp1_checkpoint.py', 'exp1-process-probe.sh', 'exp1_inputs.py',
            'exp1_independent_check.cjs', 'guest_damon_control.py', 'fixed_frequency_window.py',
            'cpu_frequency_lease.py', 'diagnose_exp1_cpu_variance.py', 'run_fixed_frequency_monitor_smoke.py',
            'smoke_trenvx_service.py', 'replay_trenvx_exp1.py', 'archive_trenvx_run_data.py',
            'trenvx_diagnostic_metrics.py', '40-apply-trenvx-task-cgroup.py', 'exp1_official_task.py',
            'run_exp1_official_control.py', 'storage_lifecycle.py')]
        paths += [str(p.relative_to(ROOT)) for p in (ROOT/'patches').glob('*.patch')
                  if str(p.relative_to(ROOT)) not in paths]
        paths += [str(p.relative_to(ROOT)) for p in (ROOT/'backend/trenvx').rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
    for name in paths:
        target = dest/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, target)
    revision = json.loads((ROOT/'configs/source-revisions.json').read_text())['IncrementalDAX_moti']['commit']
    with (dest/'prepare.log').open('w') as log:
        def run(command):
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        run(['git', 'clone', '--no-hardlinks', '--no-recurse-submodules', str(ROOT/'IncrementalDAX_moti'), str(dest/'IncrementalDAX_moti')])
        run(['git', '-C', str(dest/'IncrementalDAX_moti'), 'checkout', '--detach', revision])
        run(['git', '-C', str(dest/'IncrementalDAX_moti'), 'apply', str(dest/'patches/incrementaldax-platform.patch')])
        if args.v1:
            fork = 'baselines/TrEnv-X'
            revisions = json.loads((ROOT/'configs/source-revisions.json').read_text())
            run(['git', 'clone', '--no-hardlinks', '--no-recurse-submodules', str(ROOT/'IncrementalDAX_moti'/fork), str(dest/'IncrementalDAX_moti'/fork)])
            run(['git', '-C', str(dest/'IncrementalDAX_moti'/fork), 'checkout', '--detach', revisions['IncrementalDAX_moti']['submodules'][fork]])
            run(['python3', str(dest/'scripts/40-apply-trenvx-task-cgroup.py')])
    # Verify that the portable patch reproduces every used source, not just the commit ID.
    used = ['motivation/experiments/exp1-single-app-smoke/systems/agentenv/'+name
            for name in ('run.sh', 'guest-runner.sh', 'config.env')]
    used += ['motivation/experiments/lib/host-memory-monitor.sh']
    workload = 'motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
    used += [str(p.relative_to(ROOT/'IncrementalDAX_moti')) for p in (ROOT/'IncrementalDAX_moti'/workload).rglob('*') if p.is_file()]
    for name in used:
        assert (ROOT/'IncrementalDAX_moti'/name).read_bytes() == (dest/'IncrementalDAX_moti'/name).read_bytes(), name
    if args.v1:
        for name in ('controller.py', 'checkpoint_dax.py', 'quiesced_checkpoint.py'):
            relative = 'motivation/experiments/exp3-RL-fork/'+name
            assert (ROOT/'IncrementalDAX_moti'/relative).read_bytes() == (dest/'IncrementalDAX_moti'/relative).read_bytes()
        # Runtime, credentials and compiled binaries remain explicit external dependencies.
        (dest/'runtime').symlink_to((ROOT/'runtime').resolve(), target_is_directory=True)
    cfg = json.loads((ROOT/'configs/exp1-agentenv.json').read_text())
    cfg.update(runtime_dir=str(ROOT/'runtime'), server_result=str((ROOT/cfg['server_result']).resolve()),
               repository='IncrementalDAX_moti', output_root='.artifacts/exp1-clean',
               drop_guest_caches=0)
    if args.v1:
        cfg.update(sample_interval_seconds=1, guest_sampling=False, host_sampling=True,
                   host_monitor_light=True, generate_analysis=False, damon_control_mode='damon_disabled',
                   variant='v1-host-only-damon-disabled-functional-closeout',
                   fixed_frequency_window=True, checkpoint_output_root='.artifacts/exp1-checkpoint-clean',
                   expected_patch_sha256='f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2')
        if args.backend == 'agentenv':
            cid = (Path(cfg['server_result'])/'container-id.txt').read_text().strip()
            info = json.loads(subprocess.run(['docker', 'inspect', cid], check=True, capture_output=True, text=True, timeout=15).stdout)[0]
            pid = info['State']['Pid']
            group = next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
            from run_fixed_frequency_monitor_smoke import instrument
            from guest_damon_control import instrument as damon_instrument
            host = dest/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh'
            host.write_text(damon_instrument(instrument(host.read_text(), dest/'scripts/fixed_frequency_window.py',
                ROOT/'runtime', group, dest/'scripts/exp1_independent_check.cjs'), dest/'scripts/guest_damon_control.py', 'damon_disabled'))
            subprocess.run(['bash', '-n', str(host)], check=True, timeout=10)
    if args.official_task:
        assert args.v1
        from exp1_official_task import load
        load(args.official_task.resolve())
        shutil.copytree(args.official_task, dest/'inputs/official-task')
        cfg['official_task'] = 'inputs/official-task'
    (dest/'configs/exp1-agentenv.json').write_text(json.dumps(cfg, indent=2)+'\n')
    manifest = dict(status='PREPARED', senior_commit=revision,
                    source_sha256={name:hashlib.sha256((dest/name).read_bytes()).hexdigest() for name in paths},
                    dependency_sha256={name:hashlib.sha256((dest/'IncrementalDAX_moti'/name).read_bytes()).hexdigest() for name in used},
                    external_dependencies=['existing AgentENV service and cached digest-pinned image',
                                           'external runtime/bin CLI and runtime/config credentials',
                                           'host system tools and kernel; not a fresh-machine deployment'],
                    historical_raw_copied=False, credentials_copied=False,
                    submodules_initialized=['TrEnv-X from fixed local checkout'] if args.v1 else False,
                    backend=args.backend,
                    scope=f'{args.backend} replay and checkpoint; existing services/templates/runtime required' if args.v1 else 'AgentENV full trajectory only; no checkpoint reproduction',
                    runtime_link=str((ROOT/'runtime').resolve()) if args.v1 else None,
                    effective_host_runner_sha256=hashlib.sha256((dest/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh').read_bytes()).hexdigest(),
                    official_inputs_copied=bool(args.official_task))
    (dest/'reproduction-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'status=PREPARED result_dir={dest}')


if __name__ == '__main__':
    main()
