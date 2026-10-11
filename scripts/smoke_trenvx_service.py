"""One isolated full-service file-checkpoint smoke; no performance claims."""
import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from experiment_disk_guard import guarded_run, writer_lock, RESERVE_BYTES
from trenvx_diagnostic_metrics import DiagnosticMetrics
from storage_lifecycle import Lifecycle, storage_phase
from isolated_storage import configured_boundary

ROOT = Path(__file__).resolve().parents[1]
FORK = ROOT / 'IncrementalDAX_moti/baselines/TrEnv-X'
EXP = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
BIN = ROOT / 'runtime/trenvx-service-bin'
ENVD = ROOT / 'runtime/bin/envd-task-cgroup'
SEED = Path('/var/lib/trenvx/templates/prettier-14400-ch-dax/image/rootfs.ext4')
CH = Path('/var/lib/trenvx/deps/cloud-hypervisor/target/release/cloud-hypervisor')
KERNEL = Path('/var/lib/trenvx/kernels/ch-6.1.134/vmlinux')


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def command(out, label, argv, timeout=90):
    with storage_phase(out, label):
        return _command(out, label, argv, timeout)


def _command(out, label, argv, timeout):
    with (out / (label + '.log')).open('wb') as log:
        if label == 'namespace':
            guarded_run(argv, directory=out, log=log, timeout=timeout)
            return
        result = subprocess.run(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{label}: exit {result.returncode}; see {label}.log')


def outer(args):
    run_started_ns = time.monotonic_ns()
    assert os.geteuid() == 0, 'root required for isolated namespaces and private image mounts'
    os.umask(0o077)
    ident = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}'
    boundary = configured_boundary()
    raw_root = boundary.raw_root if boundary else ROOT/'.artifacts/trenvx-service-smoke'
    out = raw_root / ident
    out.mkdir(parents=True, exist_ok=False)
    lifecycle = Lifecycle(out)
    with lifecycle.signals():
        return _outer_owned(args, out, ident, run_started_ns, lifecycle)


def _outer_owned(args, out, ident, run_started_ns, lifecycle):
    shutil.copy2(Path(__file__), out/'smoke_trenvx_service.py')
    checkpoint_code = out / 'checkpoint-code'
    checkpoint_code.mkdir()
    for name in ('controller.py', 'checkpoint_dax.py', 'quiesced_checkpoint.py'):
        shutil.copy2(EXP/name, checkpoint_code/name)
    shutil.copy2(ROOT/'patches/incrementaldax-task-checkpoint.patch', checkpoint_code/'incrementaldax-task-checkpoint.patch')
    shutil.copy2(ROOT/'scripts/archive_trenvx_run_data.py', out/'archive_trenvx_run_data.py')
    shutil.copy2(ROOT/'scripts/experiment_disk_guard.py', out/'experiment_disk_guard.py')
    shutil.copy2(ROOT/'scripts/trenvx_diagnostic_metrics.py', out/'trenvx_diagnostic_metrics.py')
    shutil.copy2(ROOT/'scripts/storage_lifecycle.py', out/'storage_lifecycle.py')
    shutil.copy2(ROOT/'scripts/isolated_storage.py', out/'isolated_storage.py')
    if args.file_physical_diagnostic:
        for name in ('trenvx_file_diagnostic.py', 'measure_file_physical.py'):
            shutil.copy2(ROOT/'scripts'/name, out/name)
    if args.file_physical_manifest:
        shutil.copy2(args.file_physical_manifest,out/'named-files-input.json')
        shutil.copy2(ROOT/'scripts/guest_layer_file_state.py',out/'guest_layer_file_state.py')
    if args.file_origin_diagnostic:
        shutil.copy2(ROOT/'scripts/guest_file_origins.py',out/'guest_file_origins.py')
        shutil.copy2(ROOT/'configs/trenvx-exp1-origin-files.json',out/'file-origin-input.json')
    if args.guest_file_physical_diagnostic:
        for name in ('trenvx_named_guest_files.py','trenvx_kvm_capture.py','named_guest_file_physical.py',
                     'guest_named_file_probe.py','guest_file_type_check.py','measure_file_physical.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.whole_cache_diagnostic or args.kernel_cache_calibration:
        for name in ('guest_whole_cache_probe.py','trenvx_whole_cache.py','trenvx_kvm_capture.py',
                     'trenvx_named_guest_files.py','named_guest_file_physical.py','measure_file_physical.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.paused_cache_boundary:
        for name in ('paused_agentenv_boundary.py','guest_cache_marker.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.kernel_file_types or args.kernel_cache_calibration:
        for name in ('kernel_file_cache.py','trenvx_kernel_cache.py','guest_cached_file_inventory.py','guest_named_file_probe.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.scenario == 'exp1':
        shutil.copy2(ROOT/'scripts/replay_trenvx_exp1.py', out/'replay_trenvx_exp1.py')
    if args.guest_collector=='persistent':
        for name in ('guest_memory_collector.py','persistent_guest_sampling.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.fixed_git_identity:
        for name in ('fixed_runner_git_identity.py','guest_runner_phases.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.runner_phases:
        for name in ('guest_runner_phases.py','command_clock.c','calibrate_trenvx_runner.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.runner_lifecycle_diagnostic:
        for name in ('guest_runner_lifecycle.py','command_clock.c'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.collector_calibration:
        for name in ('calibrate_guest_collector.py','collector_fixed_work.c','persistent_guest_sampling.py','guest_memory_collector.py'):
            shutil.copy2(ROOT/'scripts'/name,out/name)
    if args.git_wait_diagnostic:
        shutil.copy2(ROOT/'scripts/guest_git_wait.py',out/'guest_git_wait.py')
    result = dict(status='FAIL', run_id=ident, restore_semantics='file-state-only',
                  full_trajectory_verified=False, performance_measured=False)
    group = Path('/sys/fs/cgroup') / ('asb-trenvx-' + ident)
    try:
        for p in (SEED, CH, KERNEL, ENVD, BIN/'orchestrator', BIN/'template-manager', BIN/'bind_mount'):
            assert p.is_file(), f'missing prerequisite: {p}'
        if not args.keep_data and args.archive_compressor == 'pigz-fast':
            assert shutil.which('pigz'), 'pigz-fast archive requested but pigz is unavailable'
        boundary = configured_boundary(out)
        free_before = boundary.initial_host_available_bytes if boundary else shutil.disk_usage(out).free
        if boundary: boundary.check(volume_headroom=6*1024**3)
        minimum_free_gib = (48 if args.checkpoint_after else 44 if args.scenario == 'smoke'
                            else 36)
        minimum_free = minimum_free_gib * 1024**3
        assert free_before > minimum_free, f'insufficient free space: {free_before} <= {minimum_free}'
        assert not group.exists()
        command(out, 'patch-check', [sys.executable, ROOT/'scripts/40-apply-trenvx-task-cgroup.py', '--check'])
        dump(out/'config.json', dict(cgroup=group.name, seed=str(SEED), seed_sha256=sha(SEED),
             ch_sha256=sha(CH), kernel_sha256=sha(KERNEL), envd_sha256=sha(ENVD),
             binaries={p.name:sha(p) for p in BIN.iterdir() if p.is_file()},
             patches={p.name:sha(p) for p in (ROOT/'patches').glob('*.patch')},
             source_revisions=json.loads((ROOT/'configs/source-revisions.json').read_text()),
             scenario=args.scenario, checkpoint_after=args.checkpoint_after,
             host_sample_interval_seconds=args.host_sample_interval,
             guest_memory_sampling=args.guest_memory_sampling,
             guest_collector=args.guest_collector,
             guest_collector_sources={name:sha(ROOT/'scripts'/name) for name in
                 ('guest_memory_collector.py','persistent_guest_sampling.py')} if args.guest_collector=='persistent' else {},
             guest_sample_interval_seconds=args.guest_sample_interval,
             diagnostic_metrics=args.diagnostic_metrics,
             record_phases=args.record_phases,
             runner_phases=args.runner_phases,
             runner_lifecycle_diagnostic=args.runner_lifecycle_diagnostic,
             lifecycle_calibration=args.lifecycle_calibration,
             collector_calibration=args.collector_calibration,
             collector_calibration_sources={name:sha(out/name) for name in
                 ('calibrate_guest_collector.py','collector_fixed_work.c','persistent_guest_sampling.py','guest_memory_collector.py')}
                 if args.collector_calibration else {},
             runner_lifecycle_sources={name:sha(out/name) for name in
                 ('guest_runner_lifecycle.py','command_clock.c')} if args.runner_lifecycle_diagnostic else {},
             fixed_git_identity=args.fixed_git_identity,
             fixed_git_identity_values={'user.name':'asb-fixture','user.email':'asb-fixture@localhost'} if args.fixed_git_identity else None,
             fixed_git_identity_sources={name:sha(out/name) for name in
                 ('fixed_runner_git_identity.py','guest_runner_phases.py')} if args.fixed_git_identity else {},
             git_wait_diagnostic=args.git_wait_diagnostic,
             git_identity_diagnostic=args.git_identity_diagnostic,
             git_wait_source_sha256=sha(out/'guest_git_wait.py') if args.git_wait_diagnostic else None,
             runner_phase_calibration=args.runner_phase_calibration,
             runner_phase_sources={name:sha(out/name) for name in
                 ('guest_runner_phases.py','command_clock.c','calibrate_trenvx_runner.py')} if args.runner_phases else {},
             official_task=str(args.official_task.resolve()) if args.official_task else None,
             file_physical_diagnostic=args.file_physical_diagnostic,
             file_origin_diagnostic=args.file_origin_diagnostic,
             guest_file_physical_diagnostic=args.guest_file_physical_diagnostic,
             monitoring_acceptance=args.monitoring_acceptance,
             base_file_physical_diagnostic=args.base_file_physical_diagnostic,
             whole_cache_diagnostic=args.whole_cache_diagnostic,
             paused_cache_boundary=args.paused_cache_boundary,
             kernel_file_types=args.kernel_file_types,
             kernel_cache_calibration=args.kernel_cache_calibration,
             kernel_cache_sources={name:sha(out/name) for name in
                 ('kernel_file_cache.py','trenvx_kernel_cache.py','guest_cached_file_inventory.py','guest_named_file_probe.py')} if args.kernel_file_types or args.kernel_cache_calibration else {},
             whole_cache_sources={name:sha(out/name) for name in
                 (('guest_whole_cache_probe.py','trenvx_whole_cache.py','paused_agentenv_boundary.py','guest_cache_marker.py')
                  if args.paused_cache_boundary else ('guest_whole_cache_probe.py','trenvx_whole_cache.py'))} if args.whole_cache_diagnostic or args.kernel_cache_calibration else {},
             guest_file_physical_sources={name:sha(out/name) for name in
                 ('trenvx_named_guest_files.py','trenvx_kvm_capture.py','named_guest_file_physical.py',
                  'guest_named_file_probe.py','guest_file_type_check.py','measure_file_physical.py')}
                 if args.guest_file_physical_diagnostic else {},
             file_origin_source_sha256=sha(out/'guest_file_origins.py') if args.file_origin_diagnostic else None,
             file_origin_manifest_sha256=sha(out/'file-origin-input.json') if args.file_origin_diagnostic else None,
             file_physical_manifest='named-files-input.json' if args.file_physical_manifest else None,
             file_physical_manifest_sha256=sha(args.file_physical_manifest) if args.file_physical_manifest else None,
             guest_layer_state_sha256=sha(ROOT/'scripts/guest_layer_file_state.py') if args.file_physical_manifest else None,
             file_physical_sources={name:sha(ROOT/'scripts'/name) for name in
                 ('trenvx_file_diagnostic.py', 'measure_file_physical.py')} if args.file_physical_diagnostic else {},
             diagnostic_script_sha256=sha(ROOT/'scripts/trenvx_diagnostic_metrics.py'),
             storage_diagnostics=os.environ.get('ASB_STORAGE_DIAGNOSTICS') == '1',
             storage_lifecycle_sha256=sha(ROOT/'scripts/storage_lifecycle.py'),
             keep_data=args.keep_data,
             discard_success_images=args.discard_success_images,
             discard_verified_checkpoint_images=args.discard_verified_checkpoint_images,
             monitoring_control=args.monitoring_control,
             archive_compressor=args.archive_compressor,
             disk_reserve_bytes=RESERVE_BYTES, disk_poll_seconds=.25,
             reference=str(args.reference.resolve()) if args.reference else None,
             script_sha256=sha(Path(__file__)),
             archive_script_sha256=sha(ROOT/'scripts/archive_trenvx_run_data.py'),
             storage_boundary=boundary.value if boundary else None,
             free_before_scope='host before fixed-volume allocation' if boundary else 'shared result filesystem',
             host_kernel=os.uname().release,
             cpu=2, memory_mib=4096, free_before_bytes=free_before, minimum_free_bytes=minimum_free,
             isolation='private network/mount/PID namespaces; no external network',
             seed_provenance='existing local template, identified by file hash; not a fresh Docker digest rebuild'))
        lifecycle.update('running')
        command(out, 'namespace', ['unshare','--mount','--net','--pid','--fork','--mount-proc','--kill-child',
                sys.executable, Path(__file__), '--inner', out], timeout=420)
        result.update(json.loads((out/'inner-result.json').read_text()))
    except BaseException as exc:
        result['outer_error'] = repr(exc)
        (out/'outer-error.txt').write_text(traceback.format_exc())
    finally:
        # guarded_run has waited for its owned namespace process after any failure.
        lifecycle.update('resource-cleanup', writers_stopped=True)
        if (out/'inner-result.json').exists():
            result.update(json.loads((out/'inner-result.json').read_text()))
        if 'outer_error' in result:
            result['status'] = 'FAIL'
        result['full_trajectory_verified'] = result.get('checks',{}).get('exp1_replay_correctness') == 'PASS'
        # Only this run's cgroup tree; namespace exit terminates its VM descendants.
        cleanup = dict(cgroup_removed=True)
        try:
            if group.exists():
                for p in sorted(group.rglob('*'), key=lambda x:len(x.parts), reverse=True):
                    if p.is_dir():
                        p.rmdir()
                group.rmdir()
        except OSError as exc:
            cleanup.update(cgroup_removed=False, error=repr(exc))
        loops = subprocess.run(['losetup','--list','--noheadings','--output','BACK-FILE'],
                               capture_output=True, text=True, timeout=10)
        cleanup['loop_query_exit_code'] = loops.returncode
        cleanup['owned_loop_backings'] = [x.strip() for x in loops.stdout.splitlines() if str(out) in x]
        result['host_cleanup'] = cleanup
        if not cleanup['cgroup_removed'] or loops.returncode or cleanup['owned_loop_backings']:
            result['status'] = 'FAIL'
        dump(out/'result.json', result)
        if not args.keep_data and (out/'data').is_dir():
            try:
                from archive_trenvx_run_data import archive_run_data, discard_success_images
                if (args.discard_success_images or args.discard_verified_checkpoint_images) and result['status']=='PASS':
                    lifecycle.update('image-discard', writers_stopped=True)
                    with storage_phase(out, 'image-discard'):
                        manifest = discard_success_images(out,root=out.parent,verified_checkpoint=args.discard_verified_checkpoint_images)
                    result.setdefault('checks', {})['successful_image_cleanup']='PASS'
                    result['data_retention'] = dict(status='PASS', mode=manifest['mode'],
                        manifest='image-cleanup.json', source_removed=manifest['source_removed'],
                        elapsed_seconds=manifest['elapsed_seconds'])
                elif lifecycle.stop_requested():
                    # A stop must not start another large write to archive failed/checkpoint data.
                    result['status'] = 'FAIL'
                    result['data_retention'] = dict(status='RETAINED', mode='uncompressed-after-stop',
                        source_exists=True, reason='stop requested; archive not started')
                    lifecycle.update('retained', writers_stopped=True)
                else:
                    lifecycle.update('image-archive', writers_stopped=True)
                    with storage_phase(out, 'image-archive'):
                        manifest = archive_run_data(out, root=out.parent, compressor=args.archive_compressor)
                    result.setdefault('checks', {})['data_archive']='PASS'
                    result['data_retention'] = dict(status='PASS', mode='verified-archive',
                        archive='data.tar.gz', compressor=manifest['compressor'],
                        archive_sha256=manifest['archive_sha256'],
                        source_removed=manifest['source_removed'],
                        elapsed_seconds=manifest['archive_elapsed_seconds'])
            except BaseException as exc:
                result.setdefault('checks', {})['data_archive']='FAIL'
                result['data_retention'] = dict(status='FAIL', error=repr(exc),
                    source_exists=(out/'data').exists())
                result['status'] = 'FAIL'
                (out/'archive-error.txt').write_text(traceback.format_exc())
            dump(out/'result.json', result)
        elif args.keep_data:
            result['data_retention'] = dict(status='PASS', mode='keep-data')
            dump(out/'result.json', result)
        result['controller_elapsed_ns'] = time.monotonic_ns() - run_started_ns
        result['controller_elapsed_scope'] = 'outer launch through sandbox cleanup and private image retention'
        dump(out/'result.json', result)
        lifecycle.update('complete', writers_stopped=True)
    print(f"status={result['status']} result_dir={out}")
    return 0 if result['status']=='PASS' else 1


def config_text(out, mode):
    cfg = json.loads((out/'config.json').read_text())
    return f'''ch_binary_path = "{CH}"
data_root = "{out / 'data'}"
[orchestrator]
host = "127.0.0.1"
port = 15005
subnet = "10.173.0.0/16"
cgroup_name = "{cfg['cgroup']}"
[template_manager]
subnet = "10.165.0.0/30"
kernel_debug_output = true
rootfs_build_mode = "{mode}"
template_id = "asb-task-base"
envd_path = "{ENVD}"
[template."asb-task-base"]
vcpu = 2
mem_mb = 4096
disk_mb = 4096
rootfs_size = {(out/'data/templates/asb-task-base/cache/rootfs.ext4').stat().st_size}
kernel_version = "ch-6.1.134"
docker_img = "mixfs/prettier-14400-trenvx:e625c9b97768"
no_pull = true
huge_pages = false
overlay = true
vmm_type = "cloud-hypervisor"
'''


def prepare(out):
    command(out, 'mount-private', ['mount','--make-rprivate','/'])
    command(out, 'netns-private', ['mount','-t','tmpfs','tmpfs','/run/netns'])
    command(out, 'tmp-private', ['mount','-t','tmpfs','tmpfs','/tmp'])
    (out/'hosts').write_text('127.0.0.1 localhost\n127.0.1.1 '+os.uname().nodename+'\n')
    command(out, 'hosts-private', ['mount','--bind',out/'hosts','/etc/hosts'])
    for label, args in [('loopback',['link','set','lo','up']),
                        ('dummy',['link','add','asb-uplink','type','dummy']),
                        ('dummy-address',['addr','add','198.18.0.1/30','dev','asb-uplink']),
                        ('dummy-up',['link','set','asb-uplink','up']),
                        ('dummy-route',['route','add','default','via','198.18.0.2','dev','asb-uplink'])]:
        command(out, label, ['ip',*args])
    command(out, 'forwarding', ['sysctl','-w','net.ipv4.ip_forward=1'])
    data = out/'data'
    data.mkdir()
    (data/'kernels').symlink_to('/var/lib/trenvx/kernels', target_is_directory=True)
    cache = data/'templates/asb-task-base/cache'
    cache.mkdir(parents=True)
    base = cache/'rootfs.ext4'
    command(out, 'copy-seed', ['cp','--reflink=never','--sparse=always',SEED,base])
    assert sha(base)==json.loads((out/'config.json').read_text())['seed_sha256']
    command(out, 'seed-writable', ['tune2fs','-O','^read-only',base])
    # Compact tar-built ext4 has almost no allocator headroom for replacement.
    with base.open('r+b') as f:
        f.truncate(base.stat().st_size + 256 * 1024**2)
    command(out, 'seed-grow', ['resize2fs',base])
    mountpoint = out/'seed-mount'
    mountpoint.mkdir()
    command(out, 'mount-seed', ['mount','-o','loop',base,mountpoint])
    try:
        shutil.copy2(ENVD, mountpoint/'usr/bin/envd')
        (mountpoint/'usr/bin/envd').chmod(0o755)
        shutil.copy2(FORK/'packages/template-manager/build/overlay-init', mountpoint/'sbin/overlay-init')
        (mountpoint/'sbin/overlay-init').chmod(0o755)
        dropin = mountpoint/'etc/systemd/system/envd.service.d'
        dropin.mkdir(exist_ok=True)
        (dropin/'asb-tasks.conf').write_text('''[Service]
Delegate=yes
Environment=ASB_TASK_CGROUP_PARENT=/sys/fs/cgroup/system.slice/envd.service/tasks
ExecStartPre=/bin/mkdir -p /sys/fs/cgroup/system.slice/envd.service/tasks
ExecStartPre=/bin/chmod 0700 /sys/fs/cgroup/system.slice/envd.service/tasks
''')
    finally:
        command(out, 'umount-seed', ['umount',mountpoint])
    command(out, 'seed-readonly', ['tune2fs','-O','read-only',base])
    with (cache/'writable-rootfs.ext4').open('xb') as f:
        f.truncate(4*1024**3)
    command(out, 'mkfs-upper', ['mkfs.ext4','-q','-F',cache/'writable-rootfs.ext4'])
    (out/'build.toml').write_text(config_text(out, 'skip-build-rootfs'))
    (out/'controller.toml').write_text(config_text(out, 'normal'))
    dump(out/'prepared.json', dict(rootfs_sha256=sha(base), task_parent='/sys/fs/cgroup/system.slice/envd.service/tasks'))


async def exercise(out, checks, fault_mode=None):
    sys.path.insert(0, str(EXP))
    import controller
    from controller import Controller
    from quiesced_checkpoint import CaptureIO
    os.environ.update(DATA_ROOT=str(out/'data'), TEMPLATE_ID='asb-task-base',
        ORCHESTRATOR_PORT='15005', ASB_CH_SOCKET_DIR='/tmp',
        ASB_TRENVX_BUILD_CONFIG=str(out/'controller.toml'),
        ASB_TEMPLATE_MANAGER=str(BIN/'template-manager'), TRENVX_PRIVATE_UPPER_COPY='1')
    ctl = Controller(SimpleNamespace(raw_dir=out/'controller', system='trenvx', timeout=30))
    ctl._templates = {}
    await ctl.init_trenv()

    async def tool(sandbox, label, cmd, expected=0):
        cmd = 'set -e; ' + cmd
        p = await sandbox.simple_process.start(cmd, user='root')
        r = await p.wait(timeout=30)
        dump(out/(label+'.json'), dict(cmd=cmd, stdout=r.stdout, stderr=r.stderr, exit_code=r.exit_code))
        assert r.exit_code==expected, f'{label}: exit {r.exit_code}'
        return r.stdout

    try:
        parent = await ctl.sdk.create(template='asb-task-base', target_addr='127.0.0.1', timeout=30)
        ctl.live[parent.id] = parent
        dump(out/'parent.json',dict(id=parent.id,url=parent.get_sbx_url()))
        await tool(parent, 'guest-info', 'uname -r; cat /proc/sys/kernel/random/boot_id; cat /proc/mounts; cat /proc/self/cgroup')
        await tool(parent, 'expected-failure', 'printf asb-out; printf asb-err >&2; exit 127', expected=127)
        io = CaptureIO('http://'+parent.get_sbx_url(),Path('/tmp')/f'vmm-{parent.id}.socket',30)
        dump(out/'tasks.json', await asyncio.to_thread(io.tasks))
        checks['service_create_and_tool']='PASS'
        await tool(parent, 'prepare-files', "mkdir -p /root/asb-smoke; printf root-before > /root/asb-smoke/state; printf tmp-before > /tmp/asb-state; setsid bash -c 'while :; do echo tick >> /root/asb-smoke/writer; sleep 0.1; done' </dev/null >/dev/null 2>&1 &")
        await asyncio.sleep(.3)
        if fault_mode:
            original_capture_io = controller.CaptureIO
            class FaultCopyIO(original_capture_io):
                copy_calls = 0
                def copy(self, source, target):
                    type(self).copy_calls += 1
                    if fault_mode == 'timeout':
                        assert source.is_file()
                        # The upper is sparse and can copy in <20 ms. An endless
                        # input guarantees that the copy subprocess times out.
                        subprocess.run(['cp','--reflink=never','--sparse=never',
                                        '/dev/zero',str(target)],check=True,timeout=0.02)
                        raise AssertionError('unbounded copy unexpectedly finished')
                    raise OSError('ASB injected upper-copy failure')
            controller.CaptureIO = FaultCopyIO
            try:
                try:
                    await ctl.capture_template(parent, 'injected-copy-'+fault_mode)
                except BaseExceptionGroup as exc:
                    dump(out/'injected-failure.json',dict(error=repr(exc),
                         causes=[repr(cause) for cause in exc.exceptions],
                         copy_calls=FaultCopyIO.copy_calls,fault_mode=fault_mode))
                    assert FaultCopyIO.copy_calls == 1
                    assert len(exc.exceptions) == 1
                    if fault_mode == 'timeout':
                        assert isinstance(exc.exceptions[0], subprocess.TimeoutExpired)
                    else:
                        assert isinstance(exc.exceptions[0], OSError)
                        assert str(exc.exceptions[0]) == 'ASB injected upper-copy failure'
                else:
                    raise AssertionError('injected copy failure did not abort checkpoint')
            finally:
                controller.CaptureIO = original_capture_io
            checks['injected_copy_timeout_observed' if fault_mode == 'timeout'
                   else 'injected_copy_failure_observed']='PASS'
            if fault_mode == 'timeout':
                copy_process=subprocess.run(['pgrep','-x','cp'],capture_output=True,text=True,timeout=5)
                dump(out/'copy-process-after-timeout.json',dict(
                    exit_code=copy_process.returncode,stdout=copy_process.stdout,
                    stderr=copy_process.stderr))
                assert copy_process.returncode == 1
                checks['timed_out_copy_reaped']='PASS'
            audit=json.loads((out/'controller/checkpoint-1-quiesce.json').read_text())
            assert all(audit.get(key) is True for key in
                       ('frozen','paused','resumed','runtime_removed','thawed'))
            assert audit.get('complete') is False and audit.get('copied') is not True
            assert not list((out/'data/templates').glob('exp5-dax-*'))
            dump(out/'failure-audit-summary.json',dict(
                frozen=audit['frozen'],paused=audit['paused'],resumed=audit['resumed'],
                runtime_removed=audit['runtime_removed'],thawed=audit['thawed'],
                complete=audit['complete'],copied=audit.get('copied',False)))
            checks['capture_recovery_audit']='PASS'
            state=await asyncio.to_thread(io.vm,'info')
            dump(out/'ch-after-fault.json',dict(state=state['state']))
            assert state['state']=='Running'
            await tool(parent,'parent-after-fault',
                'a=$(wc -c < /root/asb-smoke/writer); sleep 0.3; b=$(wc -c < /root/asb-smoke/writer); test "$b" -gt "$a"; test "$(cat /root/asb-smoke/state)" = root-before; printf recovered')
            checks['parent_running_after_failure']='PASS'
            return
        template = await ctl.capture_template(parent, 'smoke')
        checks['controller_capture_and_seal']='PASS'
        dump(out/'checkpoint.json',dict(template=template))
        await tool(parent, 'parent-continues', 'a=$(wc -c < /root/asb-smoke/writer); sleep 0.3; b=$(wc -c < /root/asb-smoke/writer); test "$b" -gt "$a"; printf parent-after > /root/asb-smoke/state')
        child = await ctl.start('smoke-child', template=template, parent=parent.id)
        checks['restore_interface']='PASS'
        await tool(child, 'restored-files', "test \"$(cat /root/asb-smoke/state)\" = root-before; test \"$(cat /tmp/asb-state)\" = tmp-before; test -s /root/asb-smoke/writer; a=$(wc -c < /root/asb-smoke/writer); sleep 0.3; b=$(wc -c < /root/asb-smoke/writer); test \"$a\" = \"$b\"; printf child-after >> /root/asb-smoke/state; cat /root/asb-smoke/state; cat /proc/mounts")
        checks['file_restore_and_continue']='PASS'
        await tool(parent, 'parent-isolation', 'test "$(cat /root/asb-smoke/state)" = parent-after')
        checks['parent_child_write_isolation']='PASS'
    finally:
        errors=[]
        for sandbox in list(ctl.live.values()):
            try:
                await asyncio.wait_for(ctl.delete(sandbox), timeout=45)
            except BaseException as exc:
                errors.append(repr(exc))
        checks['sdk_cleanup']='PASS' if not errors else 'FAIL'
        dump(out/'sdk-cleanup.json',dict(errors=errors))
        if errors:
            raise RuntimeError(f'sandbox cleanup failed: {errors}')


async def sample_host_cgroup(out, group, interval, stop):
    """Sample only the run-owned sandbox parent cgroup; never scan shared VMMs."""
    path = out/'host/memory-samples.tsv'
    path.parent.mkdir()
    samples = 0
    read_ns = 0
    fields = ('timestamp_ns', 'phase', 'cgroup_current_bytes', 'cgroup_anon_bytes',
              'cgroup_file_bytes', 'cgroup_kernel_bytes', 'sample_read_ns')
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t')
        writer.writeheader()
        deadline = time.monotonic() + 5
        while not group.joinpath('memory.current').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError(f'run cgroup unavailable: {group}')
            await asyncio.sleep(.1)
        while True:
            begin = time.monotonic_ns()
            current = int((group/'memory.current').read_text().strip())
            stat = dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
            duration = time.monotonic_ns() - begin
            writer.writerow(dict(timestamp_ns=time.time_ns(), phase='running',
                cgroup_current_bytes=current, cgroup_anon_bytes=int(stat['anon']),
                cgroup_file_bytes=int(stat['file']), cgroup_kernel_bytes=int(stat['kernel']),
                sample_read_ns=duration))
            stream.flush()
            samples += 1
            read_ns += duration
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
    dump(out/'host-sampling.json', dict(scope='run-owned TrEnv-X sandbox parent cgroup; excludes orchestrator',
        interval_seconds=interval, sample_count=samples, total_sample_read_ns=read_ns,
        vmm_pss='NOT_MEASURED', guest_memory='NOT_MEASURED'))


async def run_operation_with_samples(out, operation, interval):
    if not interval:
        return await operation
    cfg = json.loads((out/'config.json').read_text())
    group = Path('/sys/fs/cgroup') / cfg['cgroup']
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_host_cgroup(out, group, interval, stop))
    try:
        await operation
    finally:
        stop.set()
        await sampler


def inner(out):
    checks={}
    result=dict(status='FAIL',checks=checks)
    service=None;capture=None
    cfg=json.loads((out/'config.json').read_text())
    metrics=DiagnosticMetrics(out,cfg.get('diagnostic_metrics',False),record_phases=cfg.get('record_phases',False))
    try:
        assert os.getpid()==1, 'must run as PID 1 in private namespace'
        if cfg.get('guest_file_physical_diagnostic',False) or cfg.get('whole_cache_diagnostic',False) or cfg.get('kernel_cache_calibration',False):
            from trenvx_kvm_capture import Capture
            capture=Capture(out);capture.start()
        with metrics.phase('private-template-prepare'):
            prepare(out)
        checks['private_template_prepare']='PASS'
        with metrics.phase('template-build'):
            command(out, 'template-build', [BIN/'template-manager','--config',out/'build.toml'],timeout=90)
        checks['template_build']='PASS'
        # Env changes are inherited by orchestrator and its VMM children.
        os.environ['TRENVX_PRIVATE_UPPER_COPY']='1'
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            os.environ.pop(key,None)
        with (out/'orchestrator.log').open('wb') as log:
            service=subprocess.Popen([str(BIN/'orchestrator'),'--config',str(out/'controller.toml')],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+15
            while True:
                try:
                    with socket.create_connection(('127.0.0.1',15005),timeout=.3):
                        break
                except OSError:
                    assert service.poll() is None and time.monotonic()<deadline, 'orchestrator unavailable'
                    time.sleep(.1)
            if json.loads((out/'config.json').read_text())['scenario'] == 'exp1':
                from replay_trenvx_exp1 import exercise_exp1
                operation = exercise_exp1(out, checks,kvm_capture=capture)
            else:
                scenario=json.loads((out/'config.json').read_text())['scenario']
                operation = exercise(out,checks,fault_mode=('timeout' if scenario=='fault-copy-timeout'
                                      else 'error' if scenario=='fault-copy' else None))
            interval=json.loads((out/'config.json').read_text())['host_sample_interval_seconds']
            asyncio.run(asyncio.wait_for(run_operation_with_samples(out,operation,interval),timeout=240))
            if interval:
                summary=json.loads((out/'host-sampling.json').read_text())
                assert summary['sample_count'] >= 2, 'host sampling produced too few observations'
                checks['host_light_sampling']='PASS'
        result['status']='PASS'
    except BaseException as exc:
        result['error']=repr(exc)
        (out/'inner-error.txt').write_text(traceback.format_exc())
    finally:
        if capture is not None:
            try:capture.stop();checks['owned_kvm_capture_shutdown']='PASS'
            except BaseException as exc:
                checks['owned_kvm_capture_shutdown']='FAIL';result.update(status='FAIL',capture_error=repr(exc))
        if service is not None:
            service.terminate()
            try:
                service.wait(timeout=15)
                checks['service_shutdown']='PASS' if service.returncode==0 else 'FAIL'
            except subprocess.TimeoutExpired:
                service.kill(); service.wait(timeout=5)
                checks['service_shutdown']='FAIL'
            if checks['service_shutdown']!='PASS':
                result['status']='FAIL'
        dump(out/'inner-result.json',result)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--inner',type=Path)
    parser.add_argument('--scenario',choices=['smoke','exp1','fault-copy','fault-copy-timeout'],default='smoke')
    parser.add_argument('--check-only',action='store_true',help='validate arguments only; do not create files or launch services')
    parser.add_argument('--checkpoint-after',choices=['013','022'])
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--host-sample-interval',type=float,default=0,
                        help='seconds between run-owned service cgroup samples; zero disables sampling')
    parser.add_argument('--guest-memory-sampling',action='store_true',
                        help='use the frozen runner guest memory collector; impact must be measured separately')
    parser.add_argument('--guest-collector',choices=('original','persistent'),default='original',
                        help='explicit sampler variant; persistent requires guest memory sampling')
    parser.add_argument('--guest-sample-interval',type=float,default=.1,
                        help='guest sample interval in seconds when enabled (>= 0.1)')
    parser.add_argument('--diagnostic-metrics',action='store_true',
                        help='record controller phases and boundary VMM PSS; not a low-overhead run')
    parser.add_argument('--record-phases',action='store_true', help='phase timestamps only; no PSS or guest scans; separate functional run')
    parser.add_argument('--runner-phases',action='store_true', help='diagnostic guest phases, initial Git CPU/wall timers and dual clock; not baseline')
    parser.add_argument('--git-wait-diagnostic',action='store_true', help='native Git traces, final command timers and owned guest process probes; diagnostic only')
    parser.add_argument('--git-identity-diagnostic',action='store_true', help='ABBA command-scoped identity probe before replay; diagnostic only')
    parser.add_argument('--fixed-git-identity',action='store_true', help='separate full replay variant: Git identity on setup/finalization commands only')
    parser.add_argument('--runner-lifecycle-diagnostic',action='store_true', help='guest outer dual clock and collector start/stop boundaries; diagnostic only')
    parser.add_argument('--lifecycle-calibration',action='store_true', help='32 small guest fixture calls before diagnostic replay')
    parser.add_argument('--collector-calibration',action='store_true', help='one owned VM: four smoke then 32 fixed-work guest collector controls; host sampling disabled')
    parser.add_argument('--runner-phase-calibration',action='store_true', help='calibrate guest timers on a separate small fixture before replay')
    parser.add_argument('--official-task',type=Path, help='pinned task inputs; official tests after frozen replay')
    parser.add_argument('--file-physical-diagnostic',action='store_true',
                        help='measure the sealed source file after step 022 restore and continuation')
    parser.add_argument('--file-physical-manifest',type=Path,
                        help='explicit task files in one checkpoint/base; requires file physical diagnostic')
    parser.add_argument('--file-origin-diagnostic',action='store_true',
                        help='three merged file origins after full replay; no physical memory verdict')
    parser.add_argument('--guest-file-physical-diagnostic',action='store_true',
                        help='owned KVM capture and initially resident upper-file RAM proof after replay')
    parser.add_argument('--monitoring-acceptance',action='store_true',
                        help='combine persistent guest/host sampling and upper proof; recheck task after scans')
    parser.add_argument('--base-file-physical-diagnostic',action='store_true',
                        help='extend monitoring acceptance with two declared immutable base files')
    parser.add_argument('--whole-cache-diagnostic',action='store_true',
                        help='all guest file-LRU and owned image cache at the final boundary; analysis mode')
    parser.add_argument('--paused-cache-boundary',action='store_true',
                        help='one owned VM: heartbeat-verified in-place pause during the whole-cache host scan')
    parser.add_argument('--kernel-cache-calibration',action='store_true',help='common exact-kernel calibration before replay, independent of final analysis switch')
    parser.add_argument('--kernel-file-types',action='store_true',
                        help='exact guest kernel inode typing; known-file calibration before frozen replay')
    parser.add_argument('--keep-data',action='store_true',
                        help='keep private template images unpacked after the run')
    parser.add_argument('--discard-success-images',action='store_true',
                        help='successful non-checkpoint Exp1 only: keep raw results and image hashes, discard private images')
    parser.add_argument('--discard-verified-checkpoint-images',action='store_true',
                        help='explicit ordered controls only: verified step-022 restoration; keep raw/hash, discard successful images')
    parser.add_argument('--monitoring-control',choices=('off','on'),
                        help='same persistent runner for ordered off/on combined-collection controls')
    parser.add_argument('--archive-compressor',choices=('gzip','pigz-fast'),default='pigz-fast',
                        help='lossless private-image archive format; both produce data.tar.gz')
    args=parser.parse_args()
    if args.monitoring_control and (args.scenario!='exp1' or not args.record_phases or
            not args.official_task or args.guest_collector!='persistent'):
        parser.error('monitoring controls require Exp1, phase records, official tests and the common persistent runner')
    if args.monitoring_control=='off' and (args.guest_memory_sampling or args.host_sample_interval or
            args.diagnostic_metrics or args.file_physical_diagnostic or args.guest_file_physical_diagnostic or args.whole_cache_diagnostic):
        parser.error('off control requires all periodic and physical collection disabled')
    if args.monitoring_control=='on' and not (args.guest_memory_sampling and args.host_sample_interval==.1 and
            args.guest_sample_interval==.1):
        parser.error('on control requires both 100ms samplers')
    if args.discard_verified_checkpoint_images and (args.checkpoint_after!='022' or
            not args.monitoring_control or args.keep_data or args.discard_success_images):
        parser.error('checkpoint image disposal requires explicit step-022 monitoring controls and no other retention mode')
    if args.kernel_file_types and not (args.whole_cache_diagnostic and args.paused_cache_boundary):
        parser.error('kernel file types require owned paused whole-cache analysis')
    if args.kernel_cache_calibration and not (args.scenario=='exp1' and args.paused_cache_boundary and args.record_phases and args.official_task and not args.checkpoint_after):
        parser.error('kernel calibration requires full Exp1, owned pause, phase records and official tests')
    if args.paused_cache_boundary and not (args.whole_cache_diagnostic or args.kernel_cache_calibration):
        parser.error('paused cache boundary requires whole-cache diagnostic')
    if args.whole_cache_diagnostic and (args.scenario!='exp1' or args.checkpoint_after or
            not args.official_task or not args.record_phases):
        parser.error('whole-cache diagnostic requires full Exp1, official task tests and phase records')
    if args.base_file_physical_diagnostic and not args.monitoring_acceptance:
        parser.error('base file physical proof requires monitoring acceptance')
    if args.file_origin_diagnostic and (args.scenario!='exp1' or args.checkpoint_after or
            args.file_physical_diagnostic or args.collector_calibration or args.lifecycle_calibration or
            args.runner_phase_calibration or args.git_wait_diagnostic or args.diagnostic_metrics):
        parser.error('file origins require full no-checkpoint Exp1 without other workload diagnostics')
    if args.monitoring_acceptance and not (args.guest_file_physical_diagnostic and args.file_origin_diagnostic and
            args.guest_memory_sampling and args.guest_collector=='persistent' and
            args.host_sample_interval==.1 and args.guest_sample_interval==.1 and
            args.fixed_git_identity and args.official_task and args.record_phases):
        parser.error('monitoring acceptance requires upper proof, fixed identity, official tests, phases and both persistent 100ms samplers')
    if args.guest_file_physical_diagnostic and (not args.file_origin_diagnostic or
            ((args.guest_memory_sampling or args.host_sample_interval) and not args.monitoring_acceptance)):
        parser.error('guest file physical proof requires file origins and no periodic sampling')
    if args.collector_calibration and (not args.runner_lifecycle_diagnostic or args.lifecycle_calibration or
            args.guest_memory_sampling or args.host_sample_interval):
        parser.error('collector calibration requires lifecycle diagnostic, no other calibration, guest/host periodic replay sampling off')
    if args.runner_lifecycle_diagnostic and (not args.fixed_git_identity or args.scenario!='exp1' or
            args.checkpoint_after or args.runner_phases or args.diagnostic_metrics or args.file_physical_diagnostic or
            (args.guest_memory_sampling and args.guest_collector!='persistent')):
        parser.error('lifecycle diagnostic requires fixed-identity full Exp1, optional persistent sampling, no other diagnostics')
    if args.lifecycle_calibration and (not args.runner_lifecycle_diagnostic or args.guest_memory_sampling):
        parser.error('lifecycle calibration requires lifecycle diagnostic with guest sampling off')
    if args.fixed_git_identity and (args.scenario!='exp1' or args.checkpoint_after or
            args.runner_phases or args.file_physical_diagnostic):
        parser.error('fixed Git identity currently requires full Exp1 without runner diagnostics or physical scans')
    if args.git_identity_diagnostic and not args.git_wait_diagnostic:
        parser.error('Git identity diagnostic requires --git-wait-diagnostic')
    if args.git_wait_diagnostic and (not args.runner_phases or args.runner_phase_calibration):
        parser.error('Git wait diagnostic requires --runner-phases without fixture calibration')
    if args.runner_phase_calibration and not args.runner_phases:
        parser.error('runner calibration requires --runner-phases')
    if args.runner_phases and (args.scenario!='exp1' or args.checkpoint_after or
            args.guest_memory_sampling or args.host_sample_interval or args.diagnostic_metrics or
            args.file_physical_diagnostic):
        parser.error('runner phases require full Exp1 with periodic sampling and physical scans disabled')
    if args.guest_collector=='persistent' and not args.guest_memory_sampling and args.monitoring_control!='off':
        parser.error('persistent guest collector requires --guest-memory-sampling')
    if args.guest_memory_sampling and (args.scenario!='exp1' or
            not math.isfinite(args.guest_sample_interval) or args.guest_sample_interval < .1):
        parser.error('guest sampling requires --scenario exp1 and interval >= 0.1 seconds')
    if args.official_task and args.scenario!='exp1':
        parser.error('official task evaluation requires --scenario exp1')
    if args.discard_success_images and (args.scenario!='exp1' or args.checkpoint_after or args.keep_data):
        parser.error('image cleanup requires non-checkpoint Exp1 without --keep-data')
    if args.file_physical_diagnostic and (args.checkpoint_after!='022' or not args.diagnostic_metrics):
        parser.error('file physical diagnostic requires --checkpoint-after 022 and --diagnostic-metrics')
    if args.file_physical_manifest and not args.file_physical_diagnostic:
        parser.error('file manifest requires --file-physical-diagnostic')
    if args.diagnostic_metrics and args.scenario!='exp1':
        parser.error('diagnostic metrics currently require --scenario exp1')
    if args.checkpoint_after and (args.scenario!='exp1' or not args.reference):
        parser.error('checkpoint requires --scenario exp1 and --reference to a successful full run')
    if args.host_sample_interval and (args.scenario!='exp1' or args.host_sample_interval < .1):
        parser.error('host sampling requires --scenario exp1 and interval >= 0.1 seconds')
    if args.check_only:
        print('status=VALIDATED scope=arguments-only');sys.exit(0)
    if args.inner:
        sys.exit(inner(args.inner))
    with writer_lock():
        sys.exit(outer(args))
