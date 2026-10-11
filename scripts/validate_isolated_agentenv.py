"""One temporary fixed-capacity AgentENV lifecycle check; current service stays untouched."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import tomllib
import traceback
import threading
import urllib.request

from experiment_disk_guard import writer_lock
from isolated_storage import GIB, HOST_RESERVE, available, capacity_budget, mount_identity, write_mounts, verify_container_boundary, execution_window
from probe_storage_boundary import identities

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'agentenv-smoke-20260922T114006Z-1387374'
IMAGE = 'sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681'
CAPACITY = 20*GIB


def config_value(text, section, key, value):
    pattern = re.compile(r'(^\['+re.escape(section)+r'\]\n)(.*?)(?=^\[|\Z)',re.M|re.S)
    def replace(match):
        body = match[2]
        field = re.compile(r'^'+re.escape(key)+r'\s*=.*$',re.M)
        assert len(field.findall(body)) == 1, 'unexpected config field: '+section+'.'+key
        return match[1]+field.sub(key+' = '+value,body)
    changed,count = pattern.subn(replace,text)
    assert count == 1, 'unexpected config section: '+section
    return changed


def immutable_manifest(root):
    rows = {}
    for p in sorted(root.rglob('*')):
        s=p.lstat()
        key=str(p.relative_to(root))
        if stat.S_ISLNK(s.st_mode):
            rows[key]=dict(type='link',target=os.readlink(p))
        elif stat.S_ISREG(s.st_mode):
            with p.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            rows[key]=dict(type='file',size=s.st_size,sha256=digest)
        elif not stat.S_ISDIR(s.st_mode):
            raise ValueError('unexpected cache entry: '+key)
    return rows


def parse_probe(text):
    return dict(line.split('=',1) for line in text.strip().splitlines())


def prettier_capacity(args, export, role, schedule, checkpoint, seed):
    if getattr(args,'prettier6604_readonly_replay',False):
        if not (getattr(args,'prettier6604_task',None) and seed and not (export or role or checkpoint)):
            raise ValueError('8 GiB Prettier replay requires one complete backbone and verified seed without checkpoint')
        return 8*GIB
    reduced=getattr(args,'prettier6604_grpo_capacity_gib',32)
    if reduced!=32:
        if not (reduced==16 and export and not role and schedule=='grpo' and not checkpoint and seed):
            raise ValueError('16 GiB requires seven GRPO roots, verified converted seed and no snapshots')
        return reduced*GIB
    return 32*GIB if export and not role else 24*GIB if checkpoint or export and schedule in ('bpo','tvcache') else CAPACITY


def cancelled(signum, frame):
    raise RuntimeError('validation interrupted: '+str(signum))


def main(args):
    os.umask(0o077)
    if os.geteuid()!=0: raise RuntimeError('root required')
    platform_cfg=None
    if getattr(args,'platform_config',None):
        from use_platform import configuration
        platform_cfg=configuration(args.platform_config)
        if platform_cfg['storage']!='isolated' or platform_cfg['backends']!=['agentenv']:
            raise ValueError('AgentENV validator requires isolated single-backend config')
    prettier6604=getattr(args,'prettier6604_task',None)
    prettier_checkpoint=getattr(args,'prettier6604_checkpoint',False)
    expected_audit=getattr(args,'expected_official_audit',None)
    prettier_export=getattr(args,'prettier6604_export',None)
    prettier_role=getattr(args,'prettier6604_role',None)
    prettier_schedule=getattr(args,'prettier6604_schedule','grpo')
    prettier_readonly=getattr(args,'prettier6604_readonly_replay',False)
    prettier_monitor_mode=getattr(args,'prettier6604_monitor_mode','continuous')
    prettier_physical=getattr(args,'prettier6604_physical_cache_after','')
    prettier_shared=getattr(args,'prettier6604_shared_cache',False)
    prettier_root_count=getattr(args,'prettier6604_root_count',None)
    if prettier_shared and not (prettier_export and prettier_schedule=='grpo' and not prettier_role and not prettier_checkpoint):
        raise ValueError('shared-cache measurement requires independent Prettier GRPO roots')
    if prettier_root_count is not None and not prettier_shared:
        raise ValueError('root-count requires shared-cache analysis')
    if prettier_physical and not (prettier6604 and prettier_readonly and not prettier_checkpoint and not prettier_export and prettier_monitor_mode=='continuous'):
        raise ValueError('physical boundaries require one continuous readonly Prettier replay')
    if prettier_monitor_mode=='off' and not prettier_readonly:
        raise ValueError('Prettier monitor-off requires complete readonly backbone')
    cache_seed=getattr(args,'immutable_cache_seed',None)
    python_inputs=getattr(args,'python_backbone_inputs',None)
    python_profile=getattr(args,'python_task_profile','xarray6992')
    sphinx=python_profile=='sphinx7590'
    if sphinx and not python_inputs:raise ValueError('Sphinx profile requires prepared Python task inputs')
    python_oci=getattr(args,'python_backbone_oci',None)
    python_capacity=getattr(args,'python_backbone_capacity_gib',32)
    python_diagnostic=getattr(args,'python_diagnostic_only',False)
    python_readonly_replay=getattr(args,'python_readonly_replay',False)
    python_persistent=getattr(args,'python_persistent_sampling',False)
    python_monitor_mode=getattr(args,'python_monitor_mode','continuous')
    python_physical=getattr(args,'python_physical_cache_after','')
    python_continuation=getattr(args,'python_diagnostic_continuation',False)
    sphinx_pipeline=getattr(args,'python_sphinx_pipeline_diagnostic',False)
    pipeline_version=getattr(args,'python_sphinx_pipeline_policy_version',1)
    if pipeline_version!=1 and not sphinx_pipeline:raise ValueError('pipeline policy version requires explicit diagnostic mode')
    python_evaluation=getattr(args,'python_evaluation_patch',None)
    python_checkpoint=getattr(args,'python_checkpoint',False)
    python_expected=getattr(args,'python_expected_evaluation_raw',None)
    python_cache_smoke=getattr(args,'python_cache_smoke',False)
    python_runtime_smoke=getattr(args,'python_runtime_smoke',False)
    python_find_diagnostic=getattr(args,'python_find_diagnostic',False)
    python_command_diagnostic=getattr(args,'python_command_diagnostic',False)
    python_tool_capture=getattr(args,'python_tool_capture_demo',False)
    python_durable=getattr(args,'python_durable_checkpoint_demo',False)
    durable_bundle=getattr(args,'python_durable_checkpoint_bundle',None)
    if durable_bundle and not python_durable:raise ValueError('checkpoint bundle requires durable checkpoint demo')
    if python_find_diagnostic and not (python_runtime_smoke or python_command_diagnostic):
        raise ValueError('find diagnostic requires runtime smoke or command diagnostic')
    short_args=['--find-diagnostic'] if python_find_diagnostic else []
    if python_durable:short_args=['--phase','restore','--bundle',str(durable_bundle.resolve())] if durable_bundle else ['--phase','capture']
    if sum(map(bool,(python_runtime_smoke,python_cache_smoke,python_command_diagnostic,python_tool_capture,python_durable)))>1:
        raise ValueError('choose one short Python validation mode')
    python_short=python_cache_smoke or python_runtime_smoke or python_command_diagnostic or python_tool_capture or python_durable
    if python_physical and not (python_inputs and python_readonly_replay and python_persistent and python_monitor_mode=='continuous' and not (python_checkpoint or python_short or python_diagnostic or python_evaluation)):
        raise ValueError('physical boundaries require complete readonly continuous Python replay without restore')
    short_runner='run_native_checkpoint.py' if python_durable else 'run_tool_capture.py' if python_tool_capture else 'run_sphinx_command_diagnostic.py' if python_command_diagnostic else 'run_runtime_monitor_smoke.py' if python_runtime_smoke else 'run_task_metrics_smoke.py'
    python_cache_reference=getattr(args,'python_cache_reference',None)
    sphinx_short=sphinx and python_short and python_cache_reference and python_inputs and python_readonly_replay and python_persistent and (cache_seed or durable_bundle) and not (python_diagnostic or python_continuation or python_evaluation or python_oci or python_checkpoint or python_expected)
    if (python_short or python_cache_reference) and not sphinx_short:
        raise ValueError('cache smoke requires complete Sphinx readonly inputs, reference and persistent sampler options')
    sphinx_restore=sphinx and python_checkpoint and python_expected and python_inputs and python_readonly_replay and python_persistent and cache_seed and not (python_diagnostic or python_continuation or python_evaluation or python_oci)
    sphinx_full=sphinx and python_inputs and python_readonly_replay and python_persistent and cache_seed and not (python_short or python_cache_reference or python_checkpoint or python_expected or python_diagnostic or python_continuation or python_evaluation or python_oci)
    if sphinx_pipeline and not sphinx_full:
        raise ValueError('Sphinx pipeline diagnostic requires complete readonly Sphinx replay')
    if python_monitor_mode=='off' and not sphinx_full:
        raise ValueError('monitor-off control requires a complete readonly Sphinx replay without checkpoint')
    if sphinx and (python_diagnostic or python_continuation or python_evaluation or ((python_checkpoint or python_expected or python_readonly_replay or cache_seed) and not (sphinx_restore or sphinx_short or sphinx_full))):
        raise ValueError('Sphinx requires strict native frozen replay or complete readonly full/checkpoint/smoke options')
    if python_capacity<24 and not sphinx:raise ValueError('smaller native conversion volumes are limited to verified Sphinx OCI inputs')
    if (python_checkpoint or python_expected) and not (sphinx_restore or python_checkpoint and python_expected and python_inputs and
            python_readonly_replay and python_persistent and python_continuation and not python_evaluation and not python_diagnostic):
        raise ValueError('Python checkpoint requires readonly replay, persistent sampling and complete evaluation reference')
    if python_evaluation and not (python_inputs and cache_seed and not python_diagnostic and not python_readonly_replay and not python_continuation):
        raise ValueError('independent evaluation requires task inputs and verified readonly cache only')
    if python_continuation and not (python_inputs and python_persistent and not python_diagnostic):
        raise ValueError('complete diagnosis requires frozen Python replay with persistent sampling')
    readonly_fixture=getattr(args,'python_readonly_fixture',None)
    if (python_diagnostic or readonly_fixture) and not (python_inputs and python_diagnostic and bool(readonly_fixture or cache_seed)):
        raise ValueError('short Python diagnosis requires an owned read-only fixture and inputs')
    if readonly_fixture and cache_seed:raise ValueError('choose one readonly source')
    if python_readonly_replay and not (python_inputs and (cache_seed or durable_bundle) and not python_diagnostic and not python_oci):
        raise ValueError('readonly replay requires a verified task cache seed and no diagnostic or OCI')
    if python_persistent and not (python_inputs and not python_diagnostic):raise ValueError('persistent sampler requires frozen replay')
    if (python_oci or python_capacity!=32) and not python_inputs:
        raise ValueError('offline OCI or adjusted volume requires Python backbone')
    if python_capacity!=32 and not python_oci:
        raise ValueError('adjusted volume requires verified OCI tar-size budget')
    if python_inputs and (prettier6604 or prettier_export or prettier_checkpoint or (cache_seed and not (python_diagnostic or python_readonly_replay or python_evaluation)) or args.exp1_checkpoint or platform_cfg is not None):
        raise ValueError('Python backbone cannot be combined with another workload')
    if prettier_role and not prettier_export:
        raise ValueError('Prettier branch requires corrected frozen export')
    if prettier_schedule in ('bpo','tvcache') and not prettier_export:
        raise ValueError('BPO/TVCache requires corrected frozen export')
    if prettier_export and (not prettier6604 or not expected_audit or prettier_checkpoint):
        raise ValueError('Prettier paths require task, official reference, and no checkpoint option')
    if cache_seed and not (prettier_export or prettier_readonly or python_diagnostic or python_readonly_replay or python_evaluation):
        raise ValueError('extra cache seed is limited to corrected Prettier paths')
    seed_value=None
    if cache_seed:
        cache_seed=cache_seed.resolve()
        assert cache_seed.is_relative_to(ROOT/'.artifacts'/('sphinx-backbone' if sphinx else 'python-backbone' if python_inputs else 'prettier6604'))
        seed_value=json.loads((cache_seed/'seed.json').read_text())
        assert seed_value['status']=='PASS' and seed_value['server_image']==IMAGE
        assert seed_value['task_id']==('sphinx-doc__sphinx-7590' if sphinx else 'pydata__xarray-6992' if python_inputs else 'prettier__prettier-6604')
        if sphinx:
            from sphinx7590_inputs import IMAGE as task_image
            assert seed_value['task_image']==task_image
        seed_root=Path(seed_value['source_env']).resolve()
        assert seed_root==cache_seed/'readonly/env'
        mount_identity(cache_seed/'readonly',seed_value['loop_device'])
        assert (Path('/sys/class/block')/Path(seed_value['loop_device']).name/'ro').read_text().strip()=='1'
    if prettier_checkpoint and not (prettier6604 and expected_audit):
        raise ValueError('Prettier checkpoint requires pinned task and verified official reference')
    capacity=prettier_capacity(args,prettier_export,prettier_role,prettier_schedule,prettier_checkpoint,cache_seed)
    if python_inputs:
        capacity=python_capacity*GIB
        if python_diagnostic or python_evaluation:capacity=2*GIB
        if python_readonly_replay:capacity=8*GIB
        # AgentENV snapshots copy immutable managed layers as well as VM memory.
        # Use the already verified Sphinx restore volume, even for small smoke VMs.
        if python_runtime_smoke:capacity=16*GIB
        if python_checkpoint:capacity=16*GIB
        if python_oci:
            from prepare_xarray6992_image import validate as validate_oci
            if sphinx:
                from sphinx7590_inputs import IMAGE as task_image
                validate_oci(python_oci,task_image)
            else:validate_oci(python_oci)
        checkpoint_args=['--checkpoint','--persistent-sampling','--expected-evaluation-raw',str(python_expected),*(['--diagnostic-continuation'] if python_continuation else [])] if python_checkpoint else []
        check_command=[sys.executable,'-B',str(ROOT/'scripts'/short_runner),'--inputs',str(python_inputs),'--reference',str(python_cache_reference),'--check-only',*short_args] if python_short else [sys.executable,'-B',str(ROOT/'scripts/run_xarray6992.py'),'--inputs',str(python_inputs),'--task-profile',python_profile,'--check-only',*checkpoint_args,*(['--sphinx-pipeline-diagnostic','--sphinx-pipeline-policy-version',str(pipeline_version),'--persistent-sampling'] if sphinx_pipeline else [])]
        if python_physical:
            check_command+=['--physical-cache-after',python_physical,*(['--persistent-sampling'] if '--persistent-sampling' not in check_command else []),*(['--diagnostic-continuation'] if python_continuation and '--diagnostic-continuation' not in check_command else [])]
        subprocess.run(check_command,check=True,capture_output=True,timeout=30)
    if prettier6604 and (args.exp1_checkpoint or platform_cfg is not None):
        raise ValueError('Prettier6604 entry cannot be combined with Exp1 config')
    if prettier6604:
        check_args=['--checkpoint','--expected-official-audit',str(expected_audit)] if prettier_checkpoint else []
        if prettier_readonly:
            if not expected_audit:raise ValueError('Prettier readonly replay requires original task outcome reference')
            check_args=['--monitor-mode',prettier_monitor_mode,'--expected-official-audit',str(expected_audit)]
        runner='run_prettier6604.py'
        if prettier_export:
            runner='run_prettier6604_grpo.py'
            check_args=['--frozen-export',str(prettier_export),'--expected-official-audit',str(expected_audit),'--schedule',prettier_schedule]
            if prettier_role:check_args+=['--role',prettier_role]
            if prettier_shared:check_args+=['--shared-cache','--root-count',str(prettier_root_count or 7)]
        subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/runner),'--official-task',str(prettier6604),'--check-only',*check_args],check=True,capture_output=True,timeout=15)
    exp1=args.exp1_checkpoint or platform_cfg is not None
    window=execution_window(args.window_start_utc,args.window_end_utc,automatic=getattr(args,'automatic_window',False) or not getattr(args,'confirmed_window',False))
    out=ROOT/'.artifacts/storage-isolated-service'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False)
    (out/'code').mkdir()
    if prettier6604 or python_inputs:
        for name in ('sandbox_runtime_monitor.py','report_sandbox_runtime.py','guest_memory_collector.py',
                     'guest_performance.py','guest_diskstats.py','host_sandbox_monitor.py','report_sandbox_performance.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    for name in ('validate_isolated_agentenv.py','isolated_storage.py','probe_storage_boundary.py','experiment_disk_guard.py','exp1-process-probe.sh',
                 'run_exp1_checkpoint.py','report_exp1_checkpoint.py','exp1_inputs.py','exp1_official_task.py','exp1_independent_check.cjs'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if prettier6604:
        for name in ('run_prettier6604.py','agentenv_cold_start.py','prettier6604_boundary.py','audit_prettier6604_official.py','prettier6604_inputs.py','prettier6604_check.cjs'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if prettier_export:
            for name in ('run_prettier6604_grpo.py','prepare_prettier6604_rollouts.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    if python_inputs:
        for name in ('run_xarray6992.py','xarray6992_inputs.py','xarray6992_check.py','xarray6992_test_ids.py','agentenv_cold_start.py',
                     'run_prettier6604_grpo.py','prepare_prettier6604_rollouts.py','prettier6604_inputs.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if sphinx:
            for name in ('sphinx7590_inputs.py','sphinx7590_check.py','guest_find_diagnostic.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if python_monitor_mode=='off':shutil.copy2(ROOT/'scripts/task_metrics_prefix.py',out/'code/task_metrics_prefix.py')
            if python_command_diagnostic:
                for name in ('run_sphinx_command_diagnostic.py','guest_command_diagnostic.py','task_metrics_prefix.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_oci:
            for name in ('prepare_xarray6992_image.py','xarray6992_offline_image.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_diagnostic:shutil.copy2(ROOT/'scripts/xarray6992_find_probe.py',out/'code/xarray6992_find_probe.py')
        if python_persistent:
            for name in ('xarray6992_persistent_sampling.py','guest_memory_collector.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_checkpoint:
            for name in ('xarray6992_boundary.py','report_xarray6992.py',*(['sphinx7590_boundary.py'] if sphinx else [])):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_durable:
            for name in ('run_native_checkpoint.py','native_checkpoint_bundle.py','guest_checkpoint_probe.py','tool_trajectory.py','guest_tool_capture.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_tool_capture:
            for name in ('run_tool_capture.py','tool_trajectory.py','guest_tool_capture.py','platform_management.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_runtime_smoke:
            for name in ('run_runtime_monitor_smoke.py','guest_find_diagnostic.py','sandbox_runtime_monitor.py','report_sandbox_runtime.py','guest_memory_collector.py','task_metrics_prefix.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_cache_smoke:
            for name in ('run_task_metrics_smoke.py','task_metrics_prefix.py','smoke_agentenv_whole_cache.py','agentenv_whole_cache.py',
                         'guest_whole_cache_probe.py','guest_cache_marker.py','trenvx_whole_cache.py','measure_file_physical.py',
                         'paused_agentenv_boundary.py','cached_file_inventory.py','guest_cached_file_inventory.py',
                         'guest_named_file_probe.py','guest_file_type_check.py','kernel_file_cache.py','report_file_inventory.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        shutil.copy2(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/controller.py',out/'code/vendor-controller.py')
        (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    receipt=dict(status='FAIL',scope='temporary actual AgentENV service and snapshot/restore; not rollout or performance acceptance',
                 checks={},capacity_bytes=capacity,result_dir=str(out),failure_fixture_retained=True)
    image=out/'volume.ext4'; mount=out/'mount'; mount.mkdir()
    container=None; device=None; mounted=False; success=False; sid=None; stage='preflight'
    readonly_device=None;readonly_mounted=False;readonly_mount=out/'readonly-source'
    before=identities()
    (out/'firecracker-before.json').write_text(json.dumps(before,indent=2)+'\n')
    commands=[]
    cleaning=False
    sampler_stop=threading.Event(); sampler=None; sample_errors=[]

    def sample_disk():
        try:
            with (mount/'controller/disk-samples.jsonl').open('x') as stream:
                while not sampler_stop.is_set():
                    usage=shutil.disk_usage(mount)
                    stream.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(),timestamp_ns=time.time_ns(),
                        volume_used_bytes=usage.used,volume_available_bytes=available(mount),
                        host_available_bytes=available(ROOT)))+'\n');stream.flush()
                    sampler_stop.wait(.25)
        except Exception as exc:sample_errors.append(repr(exc))
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env['NO_PROXY']=env.get('NO_PROXY','')+',localhost,127.0.0.1,::1'
    env['no_proxy']=env['NO_PROXY']

    def call(label,argv,timeout=90,*,sensitive=False,environment=None):
        start=time.monotonic_ns()
        if not cleaning:
            remaining=(datetime.fromisoformat(window['end_utc'])-datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:raise RuntimeError('confirmed window expired; cleanup only')
            timeout=min(timeout,remaining)
        worker=subprocess.Popen(list(map(str,argv)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                env=environment or env,cwd=ROOT,start_new_session=True)
        try:
            stdout,stderr=worker.communicate(timeout=timeout)
        except BaseException:
            if worker.poll() is None:
                try:os.killpg(worker.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:worker.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    try:os.killpg(worker.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    worker.communicate(timeout=10)
            raise
        r=subprocess.CompletedProcess(argv,worker.returncode,stdout,stderr)
        if not sensitive:
            # Host receipts are bounded; bulk server logs stay inside the volume.
            (out/(label+'.stdout')).write_bytes(r.stdout[:1024**2])
            (out/(label+'.stderr')).write_bytes(r.stderr[:1024**2])
        commands.append(dict(stage=label,exit_code=r.returncode,duration_ns=time.monotonic_ns()-start))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode: raise RuntimeError(label+': exit '+str(r.returncode)+'; inspect private logs')
        return r.stdout.decode().strip()

    def inspect(cid):
        return json.loads(call('inspect-'+str(len(commands)),['docker','inspect',cid],sensitive=True))[0]

    def cli(label,*argv,timeout=120):
        return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout,environment=env)

    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        original=inspect(SERVICE)
        assert original['State']['Running'] and original['Image']==IMAGE
        receipt['original_service']={k:original['State'][k] for k in ['Pid','StartedAt','Running']}
        source=Path('/proc')/str(original['State']['Pid'])/'root/workspace/env'
        if readonly_fixture:
            readonly_fixture=readonly_fixture.resolve()
            assert readonly_fixture.parent==ROOT/'.artifacts/storage-isolated-service'
            old=json.loads((readonly_fixture/'result.json').read_text());old_image=readonly_fixture/'volume.ext4'
            assert old['status']=='FAIL' and old['cleanup']['owned_container_removed'] and old['cleanup']['mount_and_loop_removed']
            assert old_image.is_file() and not old_image.is_symlink() and old_image.stat().st_size==old['capacity_bytes']
            assert not call('readonly-source-existing-loops',['losetup','-j',old_image])
            readonly_mount.mkdir()
            readonly_device=call('attach-readonly-source',['losetup','--read-only','--find','--show',old_image])
            assert (Path('/sys/class/block')/Path(readonly_device).name/'ro').read_text().strip()=='1'
            call('mount-readonly-source',['mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec',readonly_device,readonly_mount])
            readonly_mounted=True;source=readonly_mount/'env'
            receipt['readonly_source']=dict(fixture=str(readonly_fixture),image=str(old_image),device=readonly_device,
                image_mtime_ns=old_image.stat().st_mtime_ns,scope='original failed volume remains unchanged; readonly source lower commits only')
        if seed_value:
            source=seed_root
            receipt['immutable_cache_seed']={k:seed_value[k] for k in ('source_backup','backup_sha256','uncompressed_sha256','task_id','ram_allocated_bytes','elapsed_ns')}
            receipt['cache_state']='reused task-converted immutable layers; no cold-download performance claim'
        archive_reserve=0
        if python_durable and not durable_bundle:
            from native_checkpoint_bundle import ARCHIVE_LIMIT
            archive_reserve=ARCHIVE_LIMIT
        budget=capacity_budget(available(ROOT)-archive_reserve,capacity)
        budget['checkpoint_archive_reserved_bytes']=archive_reserve
        budget['actual_host_available_bytes']=available(ROOT)
        receipt['budget']=budget
        assert budget['ready'], 'insufficient host space for fixture plus reserve and margin'
        if python_oci and not python_diagnostic:
            from xarray6992_offline_image import budget as image_budget
            receipt['offline_image_budget']=image_budget(source,python_oci,capacity,task_image) if sphinx else image_budget(source,python_oci,capacity)
        receipt['coordination']=window
        print('window_start_utc='+receipt['coordination']['start_utc'],flush=True)
        stage='allocate-fixed-volume'
        with image.open('xb') as stream:
            os.posix_fallocate(stream.fileno(),0,capacity);os.fsync(stream.fileno())
        assert image.stat().st_blocks*512>=capacity
        call('mkfs',['mkfs.ext4','-q','-F','-m','0','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',image],120)
        device=call('attach',['losetup','--find','--show','--',image])
        discard=Path('/sys/class/block')/Path(device).name/'queue/discard_max_bytes'
        discard.write_text('0\n');assert discard.read_text().strip()=='0'
        call('mount',['mount','-t','ext4','-o','nodiscard',device,mount])
        mounted=True
        receipt['mount_identity']=mount_identity(mount,device)
        for name in ['env','run','tmp','controller']:(mount/name).mkdir()
        with (mount/'cleanup-reserve').open('xb') as stream:os.posix_fallocate(stream.fileno(),0,512*2**20)
        (mount/'controller/config/aenv').mkdir(parents=True)
        env['XDG_CONFIG_HOME']=str(mount/'controller/config')
        sampler=threading.Thread(target=sample_disk,daemon=True);sampler.start()
        stage='copy-immutable-cache'
        # Live RocksDB, secrets, old snapshots and running work directories are NOT copied.
        parts=['deps'] if durable_bundle else ['deps']+['image-cache/'+n for n in ['configs','indexes','commits','remote-blocks','convert-blocks']]
        copied={}
        for part in parts:
            source_part=source/part;dest=mount/'env'/part
            dest.parent.mkdir(parents=True,exist_ok=True)
            before_copy=immutable_manifest(source_part)
            if seed_value:assert before_copy==seed_value['parts'][part], 'verified seed changed: '+part
            if (readonly_fixture or python_inputs and seed_value or prettier_readonly) and part=='image-cache/commits':
                dest.mkdir();copied[part]=before_copy
                receipt['readonly_commits_bytes']=sum(row.get('size',0) for row in before_copy.values())
                continue
            call('copy-'+part.replace('/','-'),['cp','-a','--reflink=never','--sparse=always','--',source_part,dest],120)
            assert immutable_manifest(dest)==before_copy==immutable_manifest(source_part), 'cache changed during copy: '+part
            copied[part]=before_copy
        (out/'copied-input-hashes.json').write_text(json.dumps(copied,indent=2)+'\n')
        receipt['checks']['immutable_cache_copy']='PASS'
        receipt['volume_available_after_copy_bytes']=available(mount)
        assert receipt['volume_available_after_copy_bytes']>(14*GIB if python_runtime_smoke else GIB if python_diagnostic or python_evaluation else 6*GIB if python_readonly_replay or prettier_readonly else 8*GIB)
        if prettier_export and capacity==16*GIB:
            receipt['grpo_storage_budget']=dict(capacity_bytes=capacity,reference_run='20261008T132220Z-3722480',
                reference_observed_volume_used_peak_bytes=6049792000,writable_workload_budget_bytes=4*GIB,
                margin_bytes=4*GIB,cleanup_reserve_bytes=GIB//2,
                scope='seven GRPO roots only; >8 GiB remains after copying the same converted immutable seed; no snapshot budget')
        if python_checkpoint:
            assert receipt['volume_available_after_copy_bytes']>14*GIB, 'snapshot and restore headroom insufficient'
            receipt['checkpoint_storage_budget']=dict(capacity_bytes=capacity,vm_memory_snapshot_bytes=4*GIB,
                writable_layers_budget_bytes=4*GIB,restore_scratch_budget_bytes=4*GIB,margin_bytes=2*GIB,
                cleanup_reserve_bytes=GIB//2,immutable_commits='external verified read-only RAM cache',
                scope='conservative capacity budget; not a measured peak or a guarantee against other host writers')
        if python_runtime_smoke:
            receipt['runtime_monitor_storage_budget']=dict(capacity_bytes=capacity,vm_memory_mib=512,
                vm_memory_snapshot_bytes=GIB//2,managed_layer_copy_budget_bytes=10*GIB,
                writable_workload_budget_bytes=GIB,restore_scratch_budget_bytes=2*GIB,
                margin_bytes=GIB//2,cleanup_reserve_bytes=GIB//2,
                scope='two initial 512 MiB VMs and two children of one snapshot; parent sampling resumes before deletion; no 4 GiB baseline or fine physical probe')
        elif python_readonly_replay or prettier_readonly:
            # >=6 GiB remains after dependencies/metadata and a separate .5 GiB
            # cleanup reserve: 4 GiB writable workload plus 2 GiB margin.
            receipt['readonly_replay_budget']=dict(capacity_bytes=capacity,writable_workload_budget_bytes=4*GIB,
                margin_bytes=2*GIB,cleanup_reserve_bytes=GIB//2,immutable_commits='external verified read-only RAM cache')
        if durable_bundle:
            receipt['readonly_replay_budget']=dict(capacity_bytes=capacity,cleanup_reserve_bytes=GIB//2,immutable_commits='NOT_MOUNTED; native repository plus fixed deps only')
        if python_oci and not python_diagnostic:
            from xarray6992_offline_image import install
            receipt['offline_image_transport']=install(mount,task_image) if sphinx else install(mount)
        if durable_bundle:
            from native_checkpoint_bundle import restore as restore_bundle
            receipt['checkpoint_import']=restore_bundle(durable_bundle.resolve(),mount/'env/snapshot-store')
        config=call('original-config',['docker','exec',SERVICE,'cat','/workspace/config/default.toml'])
        overrides=[('image.cache.gc','enabled','false'),('pool.network','maintenance_enabled','false'),
                   ('pool.block','enabled','false'),('pool.firecracker','enabled','false'),
                   ('pool.firecracker','startup_prewarm','false'),('ublk','daemon_metrics_listen_addr','""'),
                   ('network.internal','host_interaction_cidr','"10.211.0.0/16"'),
                   ('network.internal','veth_cidr','"10.212.0.0/16"')]
        # Separate network pools avoid overlap with the existing host-network service.
        routes=call('routes',['ip','-j','route','show','table','all'],10)
        assert '10.211.' not in routes and '10.212.' not in routes, 'candidate network pools already in use'
        for section,key,value in overrides:config=config_value(config,section,key,value)
        if python_durable:
            assert 'posix_fs' not in tomllib.loads(config).get('backend',{})
            config+='\n[backend.posix_fs]\nsnapshot_store = \"/workspace/env/snapshot-store\"\n'
        tomllib.loads(config)
        (out/'isolated-config.toml').write_text(config)
        receipt['config_overrides']=[section+'.'+key+'='+value for section,key,value in overrides]
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        receipt['address']='http://127.0.0.1:'+str(port)
        (mount/'env/logs').mkdir()
        name='asb-storage-check-'+out.name
        argv=['docker','create','--name',name,'--label','agentenv.experiment='+('initial-smoke' if exp1 else 'isolated-storage-check'),
              '--label','agentsandboxbench.run_id='+out.name,'--privileged','--read-only','--log-driver','none',
              '--network','host','--ulimit','memlock=-1:-1',
              '--mount','type=bind,src=/dev,dst=/dev',
              '--mount','type=bind,src='+str(out/'isolated-config.toml')+',dst=/workspace/config/default.toml,readonly']
        for src,dst in write_mounts(mount).items():argv+=['--mount','type=bind,src='+src+',dst='+dst]
        if not durable_bundle and (readonly_fixture or python_inputs and seed_value or prettier_readonly):
            argv+=['--mount','type=bind,src='+str(source/'image-cache/commits')+',dst=/workspace/env/image-cache/commits,readonly']
        if python_oci:
            argv+=['--mount','type=bind,src='+str(python_oci.resolve()/'ram/oci')+',dst=/asb-frozen-oci,readonly']
        # Only these path variables are copied; no credentials or source-service state.
        original_env=dict(e.split('=',1) for e in original['Config']['Env'] if '=' in e)
        for key in ['AENV_CONFIG_PATH','AENV_HOME_PATH','AENV_UBLK_DAEMON_BINARY_PATH']:
            if key in original_env:argv+=['-e',key+'='+original_env[key]]
        if prettier6604 or python_inputs:
            for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
                argv+=['-e',key+'=http://127.0.0.1:17891']
            for key in ('NO_PROXY','no_proxy'):
                argv+=['-e',key+'=localhost,127.0.0.1,::1,10.211.0.0/16,10.212.0.0/16']
        argv+=['-e','API_ADDR=127.0.0.1:'+str(port),'-e','AENV_FIRECRACKER_SERIAL_DIR=/workspace/env/logs/serial',
               '--entrypoint','/bin/sh',IMAGE,'-c','exec /server >>/workspace/env/logs/server.log 2>&1']
        stage='isolated-server-start'
        container=call('create-server',argv,30)
        receipt['container_id']=container
        receipt['boundary']=verify_container_boundary(inspect(container),mount,device)
        call('start-server',['docker','start',container],30)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            info=inspect(container)
            assert info['State']['Running'],'new isolated server exited'
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(receipt['address']+'/health',timeout=3) as response:
                    if response.status in (200,204):break
            except Exception:time.sleep(1)
        else:raise TimeoutError('isolated server did not become healthy')
        key=call('private-key',['docker','exec',container,'cat','/workspace/env/secrets/api-key'],sensitive=True)
        credentials=mount/'controller/config/aenv/credentials'
        credentials.write_text('url = '+json.dumps(receipt['address'])+'\napi_key = '+json.dumps(key)+'\n')
        credentials.chmod(0o600)
        assert json.loads(cli('list-before','list','--output','json'))==[]
        receipt['checks']['isolated_service_ready']='PASS'
        frozen=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())
        if python_inputs:
            stage='sphinx7590-backbone' if sphinx else 'xarray6992-backbone'
            replay=mount/'controller/python-backbone'
            task_command=[sys.executable,'-B',ROOT/'scripts'/short_runner,'--inputs',python_inputs,'--reference',python_cache_reference,
                 '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address'],'--output',replay,*short_args] if python_short else [sys.executable,'-B',ROOT/'scripts/run_xarray6992.py','--inputs',python_inputs,
                 '--task-profile',python_profile,
                 '--container',container,'--config-home',mount/'controller/config',
                 '--service-url',receipt['address'],'--output',replay,*(['--diagnostic-only'] if python_diagnostic else []),
                 *(['--diagnostic-probe',getattr(args,'python_diagnostic_probe','find')] if python_diagnostic else []),
                 *(['--diagnostic-continuation'] if python_continuation else []),
                 *(['--sphinx-pipeline-diagnostic','--sphinx-pipeline-policy-version',str(pipeline_version)] if sphinx_pipeline else []),
                 *(['--evaluation-patch',python_evaluation] if python_evaluation else []),
                 *(['--checkpoint','--expected-evaluation-raw',python_expected] if python_checkpoint else []),
                 *(['--persistent-sampling'] if python_persistent else []),'--monitor-mode',python_monitor_mode]
            if python_physical:task_command+=['--physical-cache-after',python_physical]
            call(stage,task_command,900,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS'
            if python_command_diagnostic:
                assert task_result['command_diagnostic'] and task_result['checks']['find_captures_and_source_unchanged' if python_find_diagnostic else 'repeats_outputs_exits_and_source_unchanged']=='PASS'
            elif python_durable:assert task_result['native_checkpoint_demo'] and task_result['checks']['process_memory_open_fd_files_and_boundary' if durable_bundle else 'snapshot_after_two_actual_calls']=='PASS'
            elif python_tool_capture:assert task_result['tool_capture_demo'] and task_result['checks']['frozen_replay_matches_calls_and_final_file']=='PASS' and task_result['replay']['action_count']==4
            elif python_runtime_smoke:assert task_result['runtime_monitor_smoke'] and task_result['checks']['continuous_idle_and_per_instance_curves']=='PASS'
            elif python_cache_smoke:assert task_result['cache_metrics_smoke'] and task_result['checks']['exact_kernel_physical_cache']=='PASS'
            elif python_evaluation:assert task_result['evaluation_only'] and task_result['checks']['complete_benchmark_results_and_source_unchanged']=='PASS'
            elif python_diagnostic:assert task_result['checks']['find_diagnostic_capture']=='PASS'
            else:assert task_result['replay']['action_count']==35
            assert task_result['cleanup']['service_sandbox_list_empty']
            if python_physical:
                assert task_result['checks']['physical_cache_probe_and_task_state']=='PASS'
                assert task_result['cleanup']['physical_tracer_stopped']
            receipt['python_backbone_result']=task_result
            if python_continuation or sphinx_pipeline:
                assert task_result['diagnostic_status']=='PASS' and task_result['strict_replay_status']=='FAIL'
                receipt['strict_replay_status']='FAIL';receipt['diagnostic_status']='PASS'
            if sphinx_pipeline and pipeline_version==2:
                assert task_result['sphinx_pipeline_policy_version']==2
                receipt['sphinx_pipeline_policy_version']=2
            if python_checkpoint:
                assert task_result['checkpoint_restore']=='PASS'
                for key in ('checkpoint_interface','restored_progress_and_files','process_continuity',
                            'continuation_from_step_30_and_final_state' if sphinx else 'continuation_from_step_23_and_final_state','official_outcome_matches_full_replay'):
                    assert task_result['checks'][key]=='PASS'
                assert task_result['cleanup']['snapshot_removed']
                receipt['checkpoint_restore']='PASS'
            receipt['checks']['xarray6992_find_diagnostic' if python_diagnostic else 'xarray6992_backbone_and_separate_tests']='PASS'
            receipt['scope']='short '+getattr(args,'python_diagnostic_probe','find')+' diagnostic only; no trajectory acceptance' if python_diagnostic else 'one frozen Xarray backbone; task evaluation separate; not physical cache or performance comparison'
            if python_continuation:receipt['scope']='complete Xarray diagnostic and separate task tests; historical strict replay remains FAIL'
            if python_evaluation:receipt['scope']='independent task tests from captured frozen patch; no trajectory replay or VM restore'
            if python_checkpoint:receipt['scope']='Xarray step-22 full VM snapshot/restore; steps 23-35 and original task failures preserved; strict replay FAIL'
            if sphinx:receipt['scope']='Sphinx step-29 full VM restore and steps 30-35; strict replay and original task failure preserved' if python_checkpoint else 'one strict frozen Sphinx backbone; task evaluation separate; not physical cache or performance comparison'
            if sphinx_pipeline:receipt['scope']='complete Sphinx diagnostic variant; only exact read_repo/001 output permits 141 or 0; historical strict FAIL retained; task tests separate'
            if sphinx_pipeline and pipeline_version==2:receipt['scope']='complete Sphinx diagnostic policy v2; only exact read_repo/001 and core_patch/001 output/patch permits 141 or 0; historical strict FAIL retained; task tests separate'
            if python_physical:receipt['scope']+='; paused physical cache points and continuous curves, analysis cost included'
            if python_durable:
                receipt['scope']=task_result['scope']
                receipt['checks']['durable_native_checkpoint_restore' if durable_bundle else 'durable_native_checkpoint_capture']='PASS'
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
            if python_tool_capture:
                receipt['scope']=task_result['scope']
                receipt['checks']['tool_capture_freeze_replay']='PASS'
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
            if python_runtime_smoke:
                receipt['scope']=task_result['scope']
                receipt['checks']['sandbox_continuous_monitoring']='PASS'
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
            if python_cache_smoke:
                receipt['scope']='Sphinx six-step prefix in three short cases; paused exact-kernel cache calibration; not complete trajectory or formal monitoring impact'
                receipt['checks']['sphinx_short_cache_metrics']='PASS'
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
            if python_command_diagnostic:
                receipt['scope']=task_result['scope']
                if python_find_diagnostic:receipt['strict_prefix_status']=task_result['strict_prefix_status']
                receipt['checks']['sphinx_command_diagnostic']='PASS'
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
        elif prettier6604:
            stage='prettier6604-paths' if prettier_export else 'prettier6604-backbone'
            replay=mount/'controller/prettier6604'
            task_args=['--checkpoint','--expected-official-audit',expected_audit] if prettier_checkpoint else []
            if prettier_readonly:task_args=['--monitor-mode',prettier_monitor_mode,'--expected-official-audit',expected_audit]
            if prettier_physical:task_args+=['--physical-cache-after',prettier_physical]
            runner='run_prettier6604.py'
            if prettier_export:
                runner='run_prettier6604_grpo.py'
                task_args=['--frozen-export',prettier_export,'--expected-official-audit',expected_audit,'--schedule',prettier_schedule]
                if prettier_role:task_args+=['--role',prettier_role]
                if prettier_shared:task_args+=['--shared-cache','--root-count',str(prettier_root_count or 7)]
            call(stage,[sys.executable,'-B',ROOT/'scripts'/runner,
                 '--official-task',prettier6604,'--container',container,
                 '--config-home',mount/'controller/config','--service-url',receipt['address'],
                 '--output',replay,*task_args],900,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS'
            receipt['prettier6604_result']=task_result
            if prettier_export:
                replay_check='sampled_actions_order_exit_codes_and_distinct_instances' if prettier_schedule!='grpo' else 'sampled_actions_order_exit_codes_and_distinct_roots'
                assert task_result['checks'][replay_check]=='PASS'
                assert task_result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
                expected_count=(39 if prettier_role else 84) if prettier_schedule=='tvcache' else 84 if prettier_schedule=='bpo' and not prettier_role else 189 if not prettier_role else 21 if prettier_role.startswith('early') else 33 if prettier_role=='late_3' else 32
                if prettier_shared:expected_count={1:29,2:50,7:189}[prettier_root_count or 7]
                assert task_result['action_count']==expected_count
                if prettier_schedule in ('bpo','tvcache'):
                    assert task_result['checks']['restored_files_progress_and_process']=='PASS'
                    assert len(task_result['cleanup']['snapshots_removed'])==(1 if prettier_role else 2)
                if prettier_schedule=='tvcache':assert task_result['checks']['deterministic_prefix_tree_order']=='PASS'
                assert task_result['cleanup']['service_sandbox_list_empty']
                label='prettier6604_bpo_restore_smoke' if prettier_role else 'prettier6604_corrected_bpo'
                if prettier_schedule=='grpo':label='prettier6604_corrected_branch' if prettier_role else 'prettier6604_corrected_grpo'
                if prettier_schedule=='tvcache':label='prettier6604_tvcache_restore_smoke' if prettier_role else 'prettier6604_corrected_tvcache'
                receipt['checks'][label]='PASS'
                receipt['scope']=task_result['scope']+'; platform consistency PASS; official task results reported separately; not performance comparison'
            elif prettier_checkpoint:
                assert task_result['action_count']==29
                receipt['checks']['prettier6604_replay_and_independent_tests']='PASS'
                for key in ('checkpoint_interface','restored_progress_and_files','process_continuity','official_outcome_matches_full_replay'):
                    assert task_result['checks'].get(key)=='PASS'
                assert task_result['official_task_status']=='FAIL' and task_result['cleanup']['snapshot_removed']
                receipt['checks']['prettier6604_restore_and_original_outcome']='PASS'
                receipt['scope']='platform restore PASS, original task FAIL; no performance comparison'
            else:
                assert task_result['action_count']==29
                receipt['checks']['prettier6604_backbone_and_tests']='PASS'
                receipt['scope']='temporary isolated AgentENV Prettier6604 frozen backbone; no restore or performance comparison'
        elif exp1:
            stage='exp1-full-checkpoint-workflow'
            from exp1_inputs import validate_trajectory
            frozen_cfg=platform_cfg or json.loads((ROOT/'configs/platform-exp1.json').read_text())
            boundary=frozen_cfg['checkpoint_after_step'] if platform_cfg else '022'
            task=validate_trajectory(ROOT/frozen_cfg['trajectory_dir'])
            server_result=mount/'controller/server';server_result.mkdir()
            (server_result/'container-id.txt').write_text(container+'\n')
            (server_result/'address.txt').write_text('127.0.0.1:'+str(port)+'\n')
            runtime=mount/'controller/runtime';runtime.mkdir();(runtime/'bin').symlink_to(ROOT/'runtime/bin',target_is_directory=True)
            (runtime/'config').symlink_to(mount/'controller/config',target_is_directory=True)
            replay=mount/'controller/exp1'
            cfg=dict(server_result=str(server_result),repository=frozen_cfg['repository'],runtime_dir=str(runtime),
                     output_root=str(replay),checkpoint_output_root=str(replay),checkpoint_after_step=boundary,
                     trajectory_dir=frozen_cfg['trajectory_dir'],sandbox_cpu=frozen_cfg['sandbox_cpu'],sandbox_memory_mib=frozen_cfg['sandbox_memory_mib'],
                     drop_guest_caches=0,sample_interval_seconds=frozen_cfg['sample_interval_seconds'] if platform_cfg else 1.0,
                     action_timeout_seconds=frozen_cfg['action_timeout_seconds'],
                     host_monitor_light=True,expected_patch_sha256=task['expected_patch_sha256'],
                     official_task=frozen_cfg['official_task'],generate_analysis=False)
            cfg_path=mount/'controller/exp1-config.json';cfg_path.write_text(json.dumps(cfg,indent=2)+'\n')
            call('exp1-checkpoint-workflow',[sys.executable,'-B',ROOT/'scripts/run_exp1_checkpoint.py',cfg_path],300,environment=env)
            audits=list((replay/'audit').iterdir());assert len(audits)==1
            audit=audits[0]
            checks=json.loads((audit/'checks.json').read_text())
            assert json.loads((audit/'result.json').read_text())['status']=='PASS'
            required=['input_hashes','replay_correctness','patch_matches_full_replay',
                      'independent_svg_properties','official_task_tests','cleanup','host_sampling']
            if boundary:required+=['process_continuity','restored_progress_and_files']
            for key in required:
                assert checks.get(key)=='PASS', 'missing Exp1 check: '+key
            if boundary:
                call('exp1-phase-report',[sys.executable,'-B',ROOT/'scripts/report_exp1_checkpoint.py',audit],30,environment=env)
                receipt['exp1_report']=json.loads((audit/'phase-breakdown.json').read_text())
            receipt['exp1_checks']=checks
            receipt['checks']['full_exp1_step_022_restore_and_official_tests' if boundary else 'full_exp1_and_official_tests']='PASS'
            receipt['scope']='temporary actual AgentENV storage boundary; frozen Exp1; complete VM restore when requested; not performance comparison or formal backend migration'
        else:
            stage='cold-start'
            sid=cli('cold-start','start','--cold',frozen['workload_image'],'--detach','--cpu','2','--memory','4096','--timeout','900',timeout=180)
            assert re.fullmatch('[a-fA-F0-9-]{36}',sid)
            receipt['checks']['cold_start']='PASS'
            cli('upload-probe','upload',sid,ROOT/'scripts/exp1-process-probe.sh','/workspace/storage-process-probe.sh')
            probe_before=parse_probe(cli('probe-before','exec',sid,'bash','-lc',
                'printf "001\\n" >/workspace/storage-step; printf "owned-state\\n" >/workspace/storage-state; bash /workspace/storage-process-probe.sh start'))
            snapshot='storage-check-'+out.name
            stage='checkpoint'
            cli('checkpoint','snapshot','create',sid,'--name',snapshot,timeout=180)
            receipt['checks']['checkpoint']='PASS'
            cli('delete-original','delete',sid,timeout=90);sid=None
            stage='restore'
            sid=cli('restore','start',snapshot,'--detach','--timeout','900',timeout=180)
            assert re.fullmatch('[a-fA-F0-9-]{36}',sid)
            probe_after=parse_probe(cli('probe-after','exec',sid,'bash','-lc',
                'test "$(cat /workspace/storage-step)" = 001; test "$(cat /workspace/storage-state)" = owned-state; sleep 0.3; bash /workspace/storage-process-probe.sh'))
            assert all(probe_before[k]==probe_after[k] for k in ['pid','start_ticks','boot_id'])
            assert int(probe_after['count'])>int(probe_before['count'])
            cli('continue-next-step','exec',sid,'bash','-lc',
                'test "$(cat /workspace/storage-step)" = 001; printf "002\\n" >/workspace/storage-step; test "$(cat /workspace/storage-step)" = 002; echo continuation-PASS')
            receipt['checks']['restore_process_files_and_progress']='PASS'
            receipt['process_continuity']={'before':probe_before,'after':probe_after}
        if prettier6604 and prettier_monitor_mode=='continuous' or python_inputs and not (python_cache_smoke or python_command_diagnostic) and python_monitor_mode=='continuous':
            stage='default-runtime-report'
            call(stage,[sys.executable,'-B',ROOT/'scripts/report_sandbox_runtime.py',replay,mount/'controller/report'],60,environment=env)
            receipt['default_runtime_report']=dict(status='PASS',directory=str(out/'report'),scope='guest logical memory curves; task correctness remains separate')
        elif python_inputs and python_monitor_mode=='off' or prettier6604 and prettier_monitor_mode=='off':
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='monitor-off control has no memory samples; no curve or peak fabricated')
        elif python_command_diagnostic:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='process diagnostic is separate from guest memory curve sampling')
        stage='write-boundary-audit'
        info=inspect(container)
        receipt['boundary']=verify_container_boundary(info,mount,device)
        cg=(Path('/proc')/str(info['State']['Pid'])/'cgroup').read_text()
        volume_dev=mount.stat().st_dev
        writable=[];unexpected=[]
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():continue
            try:
                if (proc/'cgroup').read_text()!=cg:continue
                for fd in (proc/'fd').iterdir():
                    try:
                        flags=next(l.split()[1] for l in (proc/'fdinfo'/fd.name).read_text().splitlines() if l.startswith('flags:'))
                        if int(flags,8)&3==0:continue
                        s=fd.stat()
                        if not stat.S_ISREG(s.st_mode):continue
                        row=dict(pid=int(proc.name),fd=fd.name,path=os.readlink(fd),device=s.st_dev,size=s.st_size)
                        writable.append(row)
                        if s.st_dev!=volume_dev:unexpected.append(row)
                    except FileNotFoundError:continue
            except FileNotFoundError:continue
        (out/'writable-files.json').write_text(json.dumps(writable,indent=2)+'\n')
        assert writable and not unexpected, 'writable regular file outside the volume'
        receipt['checks']['actual_writable_files_inside_volume']='PASS'
        # Real root process must be unable to place bulk data on the container root.
        if sid:cli('guest-channel-still-ready','exec',sid,'true')
        call('readonly-root-check',['docker','exec',container,'sh','-c',
             'if touch /workspace/unbounded-write-test 2>/dev/null; then exit 1; else test ! -e /workspace/unbounded-write-test; fi'])
        receipt['checks']['readonly_root_rejects_write']='PASS'
        receipt['volume_used_bytes']=shutil.disk_usage(mount).used
        receipt['volume_available_at_finish_bytes']=available(mount)
        receipt['host_available_at_finish_bytes']=available(ROOT)
        assert image.stat().st_size==capacity and image.stat().st_blocks*512>=capacity
        assert available(ROOT)>HOST_RESERVE
        receipt['checks']['fixed_backing_allocation']='PASS'
        success=True
    except BaseException as exc:
        receipt['error']=repr(exc);receipt['failed_stage']=stage
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        # Only this labelled container, mount and loop. Never terminate the current service.
        for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,signal.SIG_IGN)
        cleaning=True
        cleanup={}
        if sampler:
            sampler_stop.set();sampler.join(timeout=5)
            if sampler.is_alive() or sample_errors:
                cleanup['disk_sampler_error']=sample_errors or ['sampler did not exit'];success=False
        try:
            if sid:
                try:
                    cli('delete-restored','delete',sid,timeout=90);sid=None
                except Exception as exc:
                    cleanup['sandbox_delete_error']=repr(exc);success=False
                    # The owned container still must be stopped after a failed API cleanup.
            if container:
                info=inspect(container)
                assert info['Config']['Labels'].get('agentsandboxbench.run_id')==out.name
                if info['State']['Running']:call('stop-own-server',['docker','stop','-t','10',container],30)
                call('remove-own-server',['docker','rm',container],30)
            cleanup['owned_container_removed']=True
            if mounted and success and python_durable and not durable_bundle:
                from native_checkpoint_bundle import export as export_bundle
                saved=json.loads((mount/'controller/python-backbone/result.json').read_text())
                try:receipt['checkpoint_export']=export_bundle(mount/'env/snapshot-store',out/'checkpoint-bundle',saved)
                except BaseException as exc:
                    cleanup['checkpoint_export_error']=repr(exc);receipt['error']=repr(exc);receipt['failed_stage']='checkpoint-export';success=False
                    (out/'checkpoint-export-failure.txt').write_text(traceback.format_exc())
            if mounted:
                reserve=mount/'cleanup-reserve'
                if reserve.exists():reserve.unlink()
                sample_path=mount/'controller/disk-samples.jsonl'
                if sample_path.exists():
                    rows=[json.loads(line) for line in sample_path.read_text().splitlines()]
                    assert rows, 'no disk samples'
                    shutil.copy2(sample_path,out/'disk-samples.jsonl')
                    receipt['disk_sample_count']=len(rows)
                    receipt['observed_volume_used_peak_bytes']=max(row['volume_used_bytes'] for row in rows)
                    receipt['observed_host_available_min_bytes']=min(row['host_available_bytes'] for row in rows)
                replay=mount/'controller/exp1'
                if replay.exists():
                    shutil.copytree(replay,out/'exp1')
                    receipt['preserved_exp1_dir']=str(out/'exp1')
                prettier_output=mount/'controller/prettier6604'
                if prettier_output.exists():
                    shutil.copytree(prettier_output,out/'prettier6604')
                    receipt['preserved_prettier6604_dir']=str(out/'prettier6604')
                python_output=mount/'controller/python-backbone'
                if python_output.exists():
                    shutil.copytree(python_output,out/'python-backbone')
                    receipt['preserved_python_backbone_dir']=str(out/'python-backbone')
                runtime_report=mount/'controller/runtime-report'
                if runtime_report.exists():shutil.copytree(runtime_report,out/'runtime-report')
                default_report=mount/'controller/report'
                if default_report.exists():shutil.copytree(default_report,out/'report')
                for name in ['logs']:
                    src=mount/'env'/name
                    if src.exists():shutil.copytree(src,out/('service-'+name),symlinks=True)
                credentials=mount/'controller/config/aenv/credentials'
                if credentials.exists():credentials.unlink()
                call('unmount-own-volume',['umount',mount],30);mounted=False
            if device:
                assert call('verify-own-loop',['losetup','-j',image]).startswith(device+':')
                call('detach-own-loop',['losetup','-d',device],20);device=None
            assert not call('remaining-own-loops',['losetup','-j',image])
            cleanup['mount_and_loop_removed']=True
            if readonly_mounted:call('unmount-readonly-source',['umount',readonly_mount],30);readonly_mounted=False
            if readonly_device:
                call('detach-readonly-source',['losetup','-d',readonly_device],20);readonly_device=None
            if readonly_fixture:
                assert (readonly_fixture/'volume.ext4').stat().st_mtime_ns==receipt['readonly_source']['image_mtime_ns']
                cleanup['readonly_source_unmounted_and_unchanged']=True
            if success:
                receipt['fixture_allocated_before_remove_bytes']=image.stat().st_blocks*512
                image.unlink();cleanup['successful_fixture_removed']=True
                receipt['failure_fixture_retained']=False
            else:cleanup['failed_fixture_retained']=image.exists()
        except BaseException as exc:
            cleanup['error']=repr(exc);success=False
        after=identities()
        (out/'firecracker-after.json').write_text(json.dumps(after,indent=2)+'\n')
        cleanup['original_firecracker_identities_unchanged']=before==after
        if before!=after:success=False
        try:
            original_after=inspect(SERVICE)
            cleanup['original_service_unchanged']=receipt.get('original_service')=={k:original_after['State'][k] for k in ['Pid','StartedAt','Running']}
            if not cleanup['original_service_unchanged']:success=False
        except BaseException as exc:cleanup['original_service_error']=repr(exc);success=False
        receipt['cleanup']=cleanup
        receipt['status']='PASS' if success else 'FAIL'
        receipt['host_available_after_cleanup_bytes']=available(ROOT)
        receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
        receipt['formal_backend_migration']='NOT_CONFIGURED'
        receipt['trenvx_boundary']='NOT_CONFIGURED'
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        (out/'result.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    print('status='+receipt['status']+' result_dir='+str(out),flush=True)
    return 0 if receipt['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-start-utc',help='Optional runtime start with timezone; provide together with end')
    parser.add_argument('--window-end-utc',help='Optional runtime end; by default use a new bounded 20-minute runtime')
    parser.add_argument('--platform-config',type=Path,help='Validated unified isolated Exp1 config')
    parser.add_argument('--python-backbone-inputs',type=Path,help='Prepared pinned Xarray backbone; volume budget depends on selected mode')
    parser.add_argument('--prettier6604-readonly-replay',action='store_true',help='8 GiB single backbone; converted immutable commits remain read-only in verified RAM cache')
    parser.add_argument('--prettier6604-monitor-mode',choices=('continuous','off'),default='continuous')
    parser.add_argument('--prettier6604-physical-cache-after',default='',help='Completed segments for paused physical file-cache measurement')
    parser.add_argument('--prettier6604-shared-cache',action='store_true')
    parser.add_argument('--prettier6604-root-count',type=int,choices=[1,2,7])
    parser.add_argument('--python-backbone-oci',type=Path,help='Verified read-only local OCI cache; native conversion unchanged')
    parser.add_argument('--python-backbone-capacity-gib',type=int,choices=[18,20,22,24,28,32],default=32,help='Adjusted native volume requires verified OCI tar-size budget; 18/20/22 only for Sphinx')
    parser.add_argument('--python-task-profile',choices=['xarray6992','sphinx7590'],default='xarray6992')
    parser.add_argument('--python-diagnostic-only',action='store_true',help='Owned 2 GiB find diagnostic, not replay acceptance')
    parser.add_argument('--python-diagnostic-probe',choices=['find','pipeline'],default='find')
    parser.add_argument('--python-readonly-replay',action='store_true',help='8 GiB write volume; verified task-converted commits bound read-only from RAM seed')
    parser.add_argument('--python-persistent-sampling',action='store_true',help='Explicit single-process Exp3 sampler variant')
    parser.add_argument('--python-physical-cache-after',default='',help='Completed Python-task segments for paused physical file-cache measurement')
    parser.add_argument('--python-sphinx-pipeline-policy-version',type=int,choices=(1,2),default=1)
    parser.add_argument('--python-sphinx-pipeline-diagnostic',action='store_true',help='Explicit approved Sphinx first-step diagnostic; strict FAIL retained')
    parser.add_argument('--python-monitor-mode',choices=['continuous','off'],default='continuous',help='Off control requires complete readonly Sphinx replay; tool timing and correctness checks retained')
    parser.add_argument('--python-diagnostic-continuation',action='store_true',help='User-approved exact-output exception for one pinned SIGPIPE; not strict replay PASS')
    parser.add_argument('--python-evaluation-patch',type=Path,help='Independent evaluation from archived actual 35-step patch; 2 GiB write volume')
    parser.add_argument('--python-checkpoint',action='store_true',help='Snapshot after Xarray step 22 or Sphinx step 29; 16 GiB isolated write volume')
    parser.add_argument('--python-expected-evaluation-raw',type=Path,help='Complete archived evaluation raw for restored outcome comparison')
    parser.add_argument('--python-runtime-smoke',action='store_true',help='Two VMs, continuous guest sampling, six frozen Sphinx actions and one snapshot/restore')
    parser.add_argument('--python-find-diagnostic',action='store_true',help='Runtime smoke captures original find exits and suppressed errors before/after restore; no continuation replay acceptance')
    parser.add_argument('--python-command-diagnostic',action='store_true',help='13 original Sphinx actions and repeated slow-command CPU/I/O observations; no full replay')
    parser.add_argument('--python-durable-checkpoint-demo',action='store_true',help='Native POSIX repository export/import and full state continuity; 8 GiB functional fixture')
    parser.add_argument('--python-durable-checkpoint-bundle',type=Path,help='Verified offline bundle to import into a fresh private service')
    parser.add_argument('--python-tool-capture-demo',action='store_true',help='Four actual tool calls captured and replayed in a fresh sandbox; no model inference')
    parser.add_argument('--python-cache-smoke',action='store_true',help='Three short Sphinx six-step cases; sampling and paused physical-cache calibration')
    parser.add_argument('--python-cache-reference',type=Path,help='Successful complete Sphinx raw; exact prefix outputs remain frozen')
    parser.add_argument('--python-readonly-fixture',type=Path,help='Stopped failed fixture; mount source read-only and share immutable commits')
    parser.add_argument('--prettier6604-task',type=Path,help='Prepared pinned official task; run one frozen backbone after smoke')
    parser.add_argument('--prettier6604-checkpoint',action='store_true',help='24 GiB fixture, checkpoint after 24/29, preserve original task failure')
    parser.add_argument('--prettier6604-export',type=Path,help='Role-specific frozen export; run GRPO, BPO or TVCache paths')
    parser.add_argument('--prettier6604-role',choices=['early_1','early_2','early_3','late_1','late_2','late_3'],help='GRPO branch 20 GiB, BPO/TVCache early_1 restore smoke 24 GiB; omit for full 32 GiB schedule')
    parser.add_argument('--prettier6604-schedule',choices=['grpo','bpo','tvcache'],default='grpo',help='BPO/TVCache uses 24 GiB restore smoke or 32 GiB full schedule')
    parser.add_argument('--prettier6604-grpo-capacity-gib',type=int,choices=[16,32],default=32,help='16 GiB only for seven GRPO roots using verified converted seed; retains host reserve and >8 GiB post-copy headroom')
    parser.add_argument('--immutable-cache-seed',type=Path,help='Verified read-only local backup cache for Prettier, Xarray or Sphinx paths')
    parser.add_argument('--expected-official-audit',type=Path,help='Verified official result of original complete replay')
    parser.add_argument('--exp1-checkpoint',action='store_true',help='Also verify frozen Exp1 and step-022 restore on this owned service')
    parser.add_argument('--confirmed-window',action='store_true',help='Legacy explicit user-confirmed window; no longer required')
    parser.add_argument('--automatic-window',action='store_true',help='Given timestamps bound runtime under standing authorization, not exclusive disk access')
    args=parser.parse_args()
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,cancelled)
    with writer_lock():raise SystemExit(main(args))
