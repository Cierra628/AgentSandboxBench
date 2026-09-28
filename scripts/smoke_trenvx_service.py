"""One isolated full-service file-checkpoint smoke; no performance claims."""
import argparse
import asyncio
import csv
import hashlib
import json
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
    with (out / (label + '.log')).open('wb') as log:
        if label == 'namespace':
            guarded_run(argv, directory=out, log=log, timeout=timeout)
            return
        result = subprocess.run(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{label}: exit {result.returncode}; see {label}.log')


def outer(args):
    assert os.geteuid() == 0, 'root required for isolated namespaces and private image mounts'
    os.umask(0o077)
    ident = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}'
    out = ROOT / '.artifacts/trenvx-service-smoke' / ident
    out.mkdir(parents=True)
    shutil.copy2(Path(__file__), out/'smoke_trenvx_service.py')
    shutil.copy2(ROOT/'scripts/archive_trenvx_run_data.py', out/'archive_trenvx_run_data.py')
    shutil.copy2(ROOT/'scripts/experiment_disk_guard.py', out/'experiment_disk_guard.py')
    shutil.copy2(ROOT/'scripts/trenvx_diagnostic_metrics.py', out/'trenvx_diagnostic_metrics.py')
    if args.scenario == 'exp1':
        shutil.copy2(ROOT/'scripts/replay_trenvx_exp1.py', out/'replay_trenvx_exp1.py')
    result = dict(status='FAIL', run_id=ident, restore_semantics='file-state-only',
                  full_trajectory_verified=False, performance_measured=False)
    group = Path('/sys/fs/cgroup') / ('asb-trenvx-' + ident)
    try:
        for p in (SEED, CH, KERNEL, ENVD, BIN/'orchestrator', BIN/'template-manager', BIN/'bind_mount'):
            assert p.is_file(), f'missing prerequisite: {p}'
        if not args.keep_data and args.archive_compressor == 'pigz-fast':
            assert shutil.which('pigz'), 'pigz-fast archive requested but pigz is unavailable'
        free_before = shutil.disk_usage(out).free
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
             diagnostic_metrics=args.diagnostic_metrics,
             diagnostic_script_sha256=sha(ROOT/'scripts/trenvx_diagnostic_metrics.py'),
             keep_data=args.keep_data,
             archive_compressor=args.archive_compressor,
             disk_reserve_bytes=RESERVE_BYTES, disk_poll_seconds=.25,
             reference=str(args.reference.resolve()) if args.reference else None,
             script_sha256=sha(Path(__file__)),
             archive_script_sha256=sha(ROOT/'scripts/archive_trenvx_run_data.py'),
             host_kernel=os.uname().release,
             cpu=2, memory_mib=4096, free_before_bytes=free_before, minimum_free_bytes=minimum_free,
             isolation='private network/mount/PID namespaces; no external network',
             seed_provenance='existing local template, identified by file hash; not a fresh Docker digest rebuild'))
        command(out, 'namespace', ['unshare','--mount','--net','--pid','--fork','--mount-proc','--kill-child',
                sys.executable, Path(__file__), '--inner', out], timeout=420)
        result.update(json.loads((out/'inner-result.json').read_text()))
    except BaseException as exc:
        result['outer_error'] = repr(exc)
        (out/'outer-error.txt').write_text(traceback.format_exc())
    finally:
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
                from archive_trenvx_run_data import archive_run_data
                manifest = archive_run_data(out, compressor=args.archive_compressor)
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
    service=None
    metrics=DiagnosticMetrics(out,json.loads((out/'config.json').read_text()).get('diagnostic_metrics',False))
    try:
        assert os.getpid()==1, 'must run as PID 1 in private namespace'
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
                operation = exercise_exp1(out, checks)
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
    parser.add_argument('--checkpoint-after',choices=['013','022'])
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--host-sample-interval',type=float,default=0,
                        help='seconds between run-owned service cgroup samples; zero disables sampling')
    parser.add_argument('--diagnostic-metrics',action='store_true',
                        help='record controller phases and boundary VMM PSS; not a low-overhead run')
    parser.add_argument('--keep-data',action='store_true',
                        help='keep private template images unpacked after the run')
    parser.add_argument('--archive-compressor',choices=('gzip','pigz-fast'),default='pigz-fast',
                        help='lossless private-image archive format; both produce data.tar.gz')
    args=parser.parse_args()
    if args.diagnostic_metrics and args.scenario!='exp1':
        parser.error('diagnostic metrics currently require --scenario exp1')
    if args.checkpoint_after and (args.scenario!='exp1' or not args.reference):
        parser.error('checkpoint requires --scenario exp1 and --reference to a successful full run')
    if args.host_sample_interval and (args.scenario!='exp1' or args.host_sample_interval < .1):
        parser.error('host sampling requires --scenario exp1 and interval >= 0.1 seconds')
    if args.inner:
        sys.exit(inner(args.inner))
    with writer_lock():
        sys.exit(outer(args))
