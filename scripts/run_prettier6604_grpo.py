"""Run sampled GRPO, BPO or TVCache paths on one owned isolated service."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from prettier6604_inputs import ROOT, validate, sha
from prepare_prettier6604_rollouts import audit, sampled_segment
from exp1_official_task import load, guest_command
from audit_prettier6604_official import inspect as inspect_official, reference, signature
from prettier6604_boundary import verify as verify_boundary


def parallel_guest_cleanup(sandboxes, stop, delete):
    """Only after all task/check/query work; preserve stop-before-delete per guest."""
    def close(sid):
        row=dict(sandbox_id=sid,stopped=None if stop is None else False,deleted=False,errors=[],start_monotonic_ns=time.monotonic_ns())
        if stop is not None:
            try:stop(sid);row['stopped']=True
            except Exception as error:row['errors'].append(repr(error))
        try:delete(sid);row['deleted']=True
        except Exception as error:row['errors'].append(repr(error))
        row['end_monotonic_ns']=time.monotonic_ns()
        return row
    if not sandboxes:return []
    with ThreadPoolExecutor(max_workers=min(7,len(sandboxes))) as pool:
        return list(pool.map(close,sandboxes))


def monitor_options(args):
    mode=getattr(args,'monitor_mode','continuous')
    if mode not in ('continuous','off'):raise ValueError('invalid monitor mode')
    if mode=='off':
        variants=('role','light_monitor','shared_cache','root_count','snapshot_cache_pair','host_restore_short',
            'host_matched_task','host_trace_dual','host_independent_phase','host_rootfs_short','host_scaling_mode',
            'host_prefix_count','host_shared_files','host_full_rootfs_mode','host_file_roots','host_prefix_roots')
        if getattr(args,'schedule','grpo')!='grpo' or any(getattr(args,k,None) for k in variants):
            raise ValueError('monitor-off requires ordinary complete seven-guest GRPO without another measurement mode')
    return mode


def create_before_setup_options(args):
    enabled=getattr(args,'grpo_create_before_setup',getattr(args,'prettier6604_grpo_create_before_setup',False))
    if not enabled:return False
    schedule=getattr(args,'prettier6604_schedule',getattr(args,'schedule','grpo'))
    mode=getattr(args,'prettier6604_monitor_mode',getattr(args,'monitor_mode','continuous'))
    variants=('role','prettier6604_role','shared_cache','prettier6604_shared_cache','root_count','prettier6604_root_count',
        'snapshot_cache_pair','prettier6604_snapshot_cache_pair','host_restore_short','host_matched_task',
        'host_trace_dual','host_independent_phase','host_rootfs_short','host_scaling_mode','host_prefix_count',
        'host_shared_files','host_full_rootfs_mode','host_file_roots','host_prefix_roots','host_file_monitor',
        'prettier6604_checkpoint','prettier6604_readonly_replay','prettier6604_physical_cache_after',
        'diagnostic_guest_kernel','prettier6604_live_cache','guest_cache_metadata_probe','damon_observe','damon_disable',
        'startup_sampling','startup_server_metrics','online_task_config')
    light=getattr(args,'light_monitor',False)
    if (schedule!='grpo' or not ((mode=='continuous' and light) or (mode=='off' and not light)) or
            any(getattr(args,key,None) for key in variants) or
            os.environ.get('ASB_HOST_FILE_SLICE') or os.environ.get('ASB_HOST_PRIVATE_MEMBER')):
        raise ValueError('create-before-setup requires ordinary seven-guest light GRPO or monitor-off, without other measurements')
    return True


def skip_boundary_vmstat_options(args):
    enabled=getattr(args,'light_skip_boundary_vmstat',False)
    if not enabled:return False
    mode=getattr(args,'prettier6604_monitor_mode',getattr(args,'monitor_mode','continuous'))
    schedule=getattr(args,'prettier6604_schedule',getattr(args,'schedule','grpo'))
    excluded=('role','prettier6604_role','shared_cache','prettier6604_shared_cache','root_count','prettier6604_root_count',
        'snapshot_cache_pair','prettier6604_snapshot_cache_pair','host_restore_short','host_matched_task',
        'host_trace_dual','host_independent_phase','host_rootfs_short','host_scaling_mode','host_prefix_count',
        'host_shared_files','host_full_rootfs_mode','host_file_roots','host_prefix_roots',
        'prettier6604_checkpoint','prettier6604_readonly_replay','prettier6604_physical_cache_after',
        'diagnostic_guest_kernel','prettier6604_live_cache','guest_cache_metadata_probe','damon_observe','damon_disable',
        'grpo_create_before_setup','prettier6604_grpo_create_before_setup','online_task_config','startup_sampling')
    native_scope=(not hasattr(args,'prettier6604_schedule') or
        getattr(args,'host_current_only',False) and getattr(args,'host_file_monitor',False) and
        getattr(args,'persistent_cache_seed',None) and getattr(args,'prettier6604_export',None) and
        not getattr(args,'immutable_cache_seed',None) and not getattr(args,'python_backbone_inputs',None))
    if (os.environ.get('ASB_LOCAL_NO_PROXY')=='on' and schedule=='grpo' and mode=='continuous' and
            getattr(args,'light_monitor',False) and native_scope and not any(getattr(args,k,None) for k in excluded)):
        return True
    if not create_before_setup_options(args) or mode!='continuous' or not getattr(args,'light_monitor',False):
        raise ValueError('skip boundary vmstat requires sampled create-all-before-setup or local current GRPO')
    return True


def merge_boundary_meminfo_options(args):
    enabled=getattr(args,'light_merge_boundary_meminfo',False)
    if not enabled:return False
    if (type(enabled) is not bool or os.environ.get('ASB_LOCAL_NO_PROXY')!='on' or
            not getattr(args,'light_skip_boundary_vmstat',False) or
            getattr(args,'grpo_create_before_setup',False) or getattr(args,'prettier6604_grpo_create_before_setup',False) or
            not skip_boundary_vmstat_options(args)):
        raise ValueError('merged boundary meminfo requires local current GRPO with boundary vmstat skipped')
    return True


def local_current_grpo_record(result):
    """Saved proof for the local seven-guest variant, independent of report environment."""
    roles=result.get('roles',[]);guests=result.get('local_no_proxy_guests',{})
    return (result.get('schedule')=='grpo' and result.get('grpo_setup_schedule')=='per-guest-setup' and
        result.get('local_no_proxy')=='PASS' and len(roles)==7 and set(guests)==set(roles) and
        len(set(guests.values()))==7 and 'current' in result.get('host_before_first_tool',{}).get('counters',{}))


def execution_paths(rollouts,schedule,role=None,root_count=None,cache_pair=False):
    if cache_pair:
        assert schedule=='bpo' and role is None and root_count is None
        return {'backbone':rollouts['backbone'][:2],**{r:rollouts[r][2:] for r in ('early_1','early_2')}}
    if root_count is not None:
        assert schedule=='grpo' and role is None and root_count in (1,2,7)
        selected=['backbone'] if root_count==1 else ['backbone','early_1'] if root_count==2 else list(rollouts)
        return {r:rollouts[r] for r in selected}
    if schedule=='grpo':return {r:path for r,path in rollouts.items() if not role or r==role}
    assert schedule in ('bpo','tvcache') and role in (None,'early_1'), 'restore smoke supports early_1 only'
    if schedule=='tvcache':
        selected={r:path for r,path in rollouts.items() if not role or r in ('backbone',role)}
        plan=prefix_tree_plan(selected)
        return {r:[s for label,s in plan['segments'] if label==r] for r in selected}
    if role:return {'backbone':rollouts['backbone'][:2],role:rollouts[role][2:]}
    return {r:path if r=='backbone' else path[2:] if r.startswith('early') else path[4:]
            for r,path in rollouts.items()}


def prefix_tree_plan(paths):
    """Mirror the pinned controller's deterministic trie traversal, without I/O."""
    forks={};segments=[]
    def visit(roles,offset):
        label='backbone' if 'backbone' in roles else roles[0]
        while all(offset<len(paths[r]) for r in roles):
            candidate=paths[roles[0]][offset]
            if any(paths[r][offset]!=candidate for r in roles[1:]):break
            segments.append([label,candidate]);offset+=1
        if len(roles)==1:
            segments.extend([label,s] for s in paths[label][offset:]);return
        assert all(offset<len(paths[r]) for r in roles), 'strict prefix is unsupported'
        groups={}
        for r in roles:groups.setdefault(paths[r][offset],[]).append(r)
        assert len(groups)>1
        ordered=list(groups.values())
        primary=ordered.pop(next((i for i,g in enumerate(ordered) if 'backbone' in g),0))
        assert all(len(g)==1 for g in ordered), 'this adapter requires singleton secondary groups'
        point=f'longest-prefix-{label}-{offset}'
        forks[point]=dict(parent_role=label,prefix=paths[label][:offset],offset=offset,
                          child_roles=[g[0] for g in ordered])
        for group in [primary,*ordered]:visit(group,offset)
    assert paths and all(paths.values())
    visit(list(paths),0)
    return dict(forks=forks,segments=segments)


def verify_process(before,after):
    assert all(before[k]==after[k] for k in ('pid','start_ticks','boot_id'))
    assert int(after['count'])>int(before['count'])
    return dict(status='PASS',before=before,after=after)


def validated_export(directory):
    cfg, original, patch = validate()
    directory = directory.resolve()
    provenance = json.loads((directory/'provenance.json').read_text())
    workload = directory/'workload'
    sample = json.loads((workload/'agent-sampling.json').read_text())['segments']
    assert sha(workload/'agent-sampling.json') == cfg['sampling_sha256']
    assert sha(workload/'guest-runner.sh') == cfg['runner_sha256']
    expected = json.loads((original/'rollouts.json').read_text())
    expected = {role: [role+'_inspect' if s in ('early_inspect','late_inspect') else s for s in path]
                for role, path in expected.items()}
    rollouts = json.loads((workload/'rollouts.json').read_text())
    assert rollouts == expected
    names = {s: 'early_inspect' if s.startswith('early_') and s.endswith('_inspect') else
             'late_inspect' if s.startswith('late_') and s.endswith('_inspect') else s
             for path in expected.values() for s in path}
    assert provenance['original_segment_names'] == names
    assert provenance['sampling_sha256'] == cfg['sampling_sha256']
    for name, digest in provenance['exported_sha256'].items():
        assert sha(workload/name) == digest, name
    assert sha(workload/'task.json') == sha(original/'task.json')
    check = audit(workload, sample, rollouts, names)
    assert check['status'] == 'PASS' and check['logical_action_count'] == 189 and check['logical_segment_count'] == 35
    return cfg, workload, patch, sample, rollouts, names


async def joined(coroutines):
    # Wait for every owned request before cleanup, including after one fails.
    values = await asyncio.gather(*coroutines, return_exceptions=True)
    errors = [v for v in values if isinstance(v, BaseException)]
    if errors:
        raise RuntimeError('owned concurrent operation failed: '+repr(errors[0]))
    return values


def verify_path(raw, role, path, workload, samples, names, sampling=True, *, sampled_role=None, skip_boundary_vmstat=False, merge_boundary_meminfo=False):
    if skip_boundary_vmstat and not sampling:raise ValueError('cannot skip boundary vmstat in monitor-off')
    if merge_boundary_meminfo and not (sampling and skip_boundary_vmstat):raise ValueError('merged meminfo requires sampled skipped vmstat')
    count = 0
    duration = 0
    peaks = []
    for segment in path:
        output = raw/'rollouts'/role/segment
        manifest = workload/'segments'/segment/'actions.tsv'
        assert sha(output/'actions.tsv') == sha(manifest)
        with (output/'steps.tsv').open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        actions = sampled_segment(samples, sampled_role or role, names[segment])['actions']
        assert [r['step'] for r in rows] == [f'{i:03}' for i in range(1,len(actions)+1)]
        assert [int(r['exit_code']) for r in rows] == [a['exit_code'] for a in actions]
        assert all(r['branch']==role and r['round']==segment for r in rows)
        count += len(rows)
        duration += sum(int(r['duration_ns']) for r in rows)
        with (output/'guest-memory.tsv').open() as stream:
            reader=csv.DictReader(stream, delimiter='\t');memory=list(reader)
        if merge_boundary_meminfo:
            from prototype_boundary_memory import FIELDS
            expected=['timestamp_ns','phase','sandbox','branch','round','step']+[k.replace('(', '_').replace(')', '')+'_kib' for k in FIELDS]
            assert reader.fieldnames==expected, 'merged boundary memory header changed'
            phases=[('baseline','000')]+[(phase,r['step']) for r in rows for phase in ('before','after')]+[('complete','999')]
            assert [(r['phase'],r['step']) for r in memory]==phases
            assert all(r['sandbox']==r['branch']==role and r['round']==segment for r in memory)
            assert all(int(r[k])>=0 for r in memory for k in expected[6:])
        assert bool(memory)==sampling, 'memory samples do not match requested mode'
        if skip_boundary_vmstat:
            assert len(memory)==2+2*len(rows), 'boundary memory rows changed'
            with (output/'guest-vmstat.tsv').open() as stream:
                vmstat=csv.DictReader(stream,delimiter='\t')
                assert vmstat.fieldnames==['timestamp_ns','phase','sandbox','branch','round','step','pgfault','pgmajfault','pgpgin','pgpgout']
                assert not list(vmstat), 'skipped boundary vmstat has samples'
        peaks.extend((int(r['MemTotal_kib'])-int(r['MemFree_kib']))*1024 for r in memory)
    return dict(action_count=count,guest_action_sum_ns=duration,
                guest_observed_peak_bytes=max(peaks) if peaks else None,final_patch_sha256=sha(raw/'rollouts'/role/path[-1]/'patch.diff'))


def storage_allocation(mount):
    totals={};seen=set();begin=time.monotonic_ns()
    for top in ('env','run','tmp','controller'):
        for parent,dirs,files in os.walk(mount/top,followlinks=False):
            for name in files:
                p=Path(parent)/name
                try:info=p.lstat()
                except FileNotFoundError:continue
                if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino) in seen:continue
                seen.add((info.st_dev,info.st_ino))
                rel=p.relative_to(mount)
                category='/'.join(rel.parts[:2]) if len(rel.parts)>2 else top
                totals[category]=totals.get(category,0)+info.st_blocks*512
    return dict(allocated_regular_file_bytes=totals,scan_elapsed_ns=time.monotonic_ns()-begin,
                scope='terminal file allocations, hard links counted once; not atomic; excludes directory/filesystem metadata')


def reuse_capacity_checkpoint(source,out,container,config_home,service_url,recovery=None,damon_before_checkpoint=False):
    """Import evidence, not actions, from the preceding 64 MiB run on this service."""
    source=source.resolve()
    assert source.parent==out.parent and source!=out
    state=json.loads((source/'result.json').read_text())
    binding=json.loads((source/'capacity-checkpoint.json').read_text())
    saved=json.loads((source/'checkpoint-11.json').read_text())
    assert state['status']=='PASS' and state['cache_target_mib']==64
    assert state['actual_action_count']==31 and state['reused_action_count']==0
    assert state['cleanup']['snapshots_retained_for_reuse']==[saved['template']]
    assert state['cleanup']['service_sandbox_list_empty']
    assert bool(state.get('damon_before_checkpoint'))==damon_before_checkpoint
    expected=dict(container=container,config_home=str(config_home.resolve()),service_url=service_url)
    if recovery:
        recovered=json.loads(recovery.read_text())
        assert recovered['status']=='PASS' and recovered['previous_binding']==binding
        assert recovered['new_binding']==expected and recovered['template']==saved['template']
        shutil.copy2(recovery,out/'capacity-recovery.json')
    else:assert binding==expected
    assert saved['status']=='PASS' and saved['step']==11
    shutil.copytree(source/'rollouts/backbone',out/'rollouts/backbone')
    shutil.copy2(source/'checkpoint-11.json',out/'checkpoint-11.json')
    if damon_before_checkpoint:
        parent=json.loads((source/'damon-before-checkpoint.json').read_text())
        assert parent['status']=='PASS' and parent['disabled']==dict(enabled='N',kdamond_pid=-1)
        assert parent['original_enabled'] in ('Y','N')
        shutil.copy2(source/'damon-before-checkpoint.json',out/'damon-before-checkpoint.json')
    with (source/'segments.tsv').open() as stream:
        reader=csv.DictReader(stream,delimiter='\t');fields=reader.fieldnames
        rows=[r for r in reader if r['rollout']=='backbone']
    assert [r['segment'] for r in rows]==['read_repo','read_typescript']
    assert all(r['sandbox_id']==saved['parent'] for r in rows)
    with (out/'segments.tsv').open('a') as stream:
        csv.DictWriter(stream,fieldnames=fields,delimiter='\t').writerows(rows)
    return saved


def run(args):
    monitor_mode=monitor_options(args);monitoring=monitor_mode!='off'
    schedule_smoke=False
    if getattr(args,'schedule_smoke_metrics',False) or getattr(args,'schedule_expanded_files',False) or getattr(args,'schedule_cache_layers',False) or getattr(args,'schedule_block_cache_method',None) is not None or getattr(args,'schedule_split_ram_cache',False) or getattr(args,'schedule_memory_backing_links',False):
        from schedule_smoke_metrics import options as smoke_options
        schedule_smoke=smoke_options(args)
    from light_prettier_rollout import options as light_rollout_options
    light_rollout=light_rollout_options(args);rollout_sampler=None;checkpoint=None
    per_action=os.environ.get('ASB_GRPO_COLD_ACTIONS')=='on'
    if per_action and not (light_rollout and args.schedule=='grpo'):raise ValueError('cold actions require light GRPO')
    cold_points=None
    local_no_proxy=os.environ.get('ASB_LOCAL_NO_PROXY')=='on'
    if local_no_proxy and (getattr(args,'schedule','grpo')!='grpo' or getattr(args,'role',None)):
        raise ValueError('local-no-proxy GRPO requires all seven cold guests')
    create_before_setup=create_before_setup_options(args)
    skip_boundary_vmstat=skip_boundary_vmstat_options(args)
    merge_boundary_meminfo=merge_boundary_meminfo_options(args)
    cfg, workload, patch, samples, rollouts, names = validated_export(args.frozen_export)
    task = load(args.official_task)
    expected_official = reference(args.expected_official_audit)
    assert task['instance_id']==cfg['task_id'] and task['base_commit']==cfg['base_commit']
    assert expected_official['F2P']==task['F2P'] and expected_official['P2P']==task['P2P']
    schedule=getattr(args,'schedule','grpo')
    root_count=getattr(args,'root_count',None)
    shared_cache=getattr(args,'shared_cache',False)
    assert not root_count or shared_cache, 'root-count is an explicit shared-cache variant'
    assert not shared_cache or schedule=='grpo' and not args.role
    host_matched=getattr(args,'host_matched_task',False)
    from host_scaling import options as scaling_options, paths as scaling_paths
    scaling=scaling_options(args)
    from host_independent_task import phase_options, execution_paths as independent_paths
    independent_phase=phase_options(args)
    rootfs_short=getattr(args,'host_rootfs_short',False)
    if rootfs_short and not (independent_phase=='dual' and host_matched and not getattr(args,'host_trace_dual',False)):
        raise ValueError('rootfs short requires one imported dual matched restore')
    assert not getattr(args,'host_file_roots',None) or host_matched, 'live file inventory requires host-matched-task'
    host_trace_dual=getattr(args,'host_trace_dual',False)
    assert not host_trace_dual or host_matched
    assert not (host_matched and getattr(args,'host_restore_short',False))
    host_restore=getattr(args,'host_restore_short',False) or host_matched
    from host_file_matched_task import matched_paths, source_role
    assert not host_restore or schedule=='bpo' and not (args.role or shared_cache or root_count or getattr(args,'snapshot_cache_pair',False))
    cache_pair=getattr(args,'snapshot_cache_pair',False)
    assert not cache_pair or schedule=='bpo' and not (args.role or shared_cache or root_count)
    cache_sampling=getattr(args,'snapshot_cache_sampling','on')
    assert cache_pair or cache_sampling=='on', 'sampling control requires the real pair'
    guest_cache_policy=getattr(args,'snapshot_guest_cache_policy',None)
    assert guest_cache_policy is None or cache_pair and cache_sampling=='on', 'guest cache policy requires the sampled real pair'
    cache_target=getattr(args,'snapshot_cache_target_mib',None)
    assert cache_target is None or cache_pair and cache_sampling=='on' and guest_cache_policy is None, 'cache target requires the sampled real pair without initial drop policy'
    retain_checkpoint=getattr(args,'retain_capacity_checkpoint',False)
    reuse_checkpoint=getattr(args,'reuse_capacity_checkpoint',None)
    recovery_checkpoint=getattr(args,'capacity_checkpoint_recovery',None)
    assert not retain_checkpoint or cache_target==64 and not reuse_checkpoint
    assert not reuse_checkpoint or cache_target==128 and not retain_checkpoint
    assert not recovery_checkpoint or reuse_checkpoint
    damon_before_checkpoint=getattr(args,'snapshot_damon_before_checkpoint',False)
    assert not damon_before_checkpoint or cache_target is not None and (retain_checkpoint or reuse_checkpoint), 'early DAMON stop requires the explicit capacity checkpoint comparison'
    provider_cache=getattr(args,'snapshot_provider_cache',False)
    batch_lookup=getattr(args,'snapshot_bpf_batch',False)
    assert not provider_cache or cache_pair, 'provider collection requires the explicit real pair'
    assert not batch_lookup or provider_cache, 'batch lookup requires provider collection'
    prefix_count=getattr(args,'host_prefix_count',None)
    current_pair=getattr(args,'host_current_pair',False)
    if current_pair or getattr(args,'host_persistent_seed',None):
        from host_current_pair import enabled
        enabled(args)
    from host_private_files import member as private_member
    private_control=private_member()
    from host_full_rootfs_control import validate_mode as validate_full_rootfs_mode
    full_rootfs_mode=validate_full_rootfs_mode(args)
    full_rootfs_residency=getattr(args,'host_full_rootfs_residency','on')
    full_rootfs_cold_read=getattr(args,'host_full_rootfs_cold_read',False)
    if full_rootfs_mode and not (getattr(args,'light_monitor',False) and not (private_control or shared_cache or root_count or cache_pair or host_restore)):
        raise ValueError('full rootfs copies require the unchanged complete light GRPO')
    if private_control and not (prefix_count==1 and schedule=='grpo' and getattr(args,'light_monitor',False)):
        raise ValueError('private file control requires a single light prefix')
    if scaling:
        assert schedule=='grpo' and getattr(args,'light_monitor',False) and not (args.role or root_count or shared_cache or cache_pair or host_restore)
    if prefix_count:
        assert schedule=='grpo' and getattr(args,'light_monitor',False) and not (args.role or root_count or shared_cache or cache_pair or host_restore)
    executed=execution_paths(rollouts,schedule,args.role,root_count,cache_pair)
    if prefix_count:
        from host_prefix_compare import prefix_paths
        executed=prefix_paths(rollouts,prefix_count)
    if host_restore:executed={'backbone':rollouts['backbone'][:2]}
    if host_matched:executed=matched_paths(rollouts,dual_only=host_trace_dual)
    if independent_phase:executed=independent_paths(rollouts,independent_phase)
    if scaling:executed=scaling_paths(rollouts,scaling)
    if current_pair:
        from host_current_pair import paths as current_pair_paths
        executed=current_pair_paths(rollouts)
    roles=list(executed)
    final_roles=[args.role] if args.role and schedule!='tvcache' else roles
    if cache_pair:final_roles=['early_1','early_2']
    if host_restore or prefix_count or scaling=='cold':final_roles=[]
    tree_plan=prefix_tree_plan({r:rollouts[r] for r in roles}) if schedule=='tvcache' else None
    assert all((source_role(role) if host_matched else role) in rollouts for role in roles)
    if getattr(args,'light_monitor',False):
        assert schedule in ('grpo','bpo') and not (args.role or shared_cache or root_count or cache_pair or host_restore), 'light profile requires an ordinary full GRPO/BPO'
    full_shared=getattr(args,'host_shared_files',False)
    if full_shared and not (getattr(args,'light_monitor',False) and schedule in ('grpo','bpo') and not (scaling or prefix_count or independent_phase or args.role or shared_cache or root_count or cache_pair or host_restore)):
        raise ValueError('shared-file mode requires complete light GRPO/BPO')
    if args.check_only:
        print('status=VALIDATED schedule='+schedule+' roles='+','.join(roles));return 0
    out = args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    assert args.config_home.resolve()==out.parent/'config'
    out.mkdir(parents=True,exist_ok=False)
    os.umask(0o077)
    env = dict(os.environ, PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],
               XDG_CONFIG_HOME=str(args.config_home),HOST_MONITOR_LIGHT='1')
    from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
    lock = threading.Lock();counter=0;events=[];controller=None;monitor=None;physical=None;success=False
    experiment_sampler=None
    shared_files=None;full_rootfs=None
    scope='one corrected sampled branch' if args.role else 'GRPO: seven independent roots; concurrent full paths; no snapshots'
    if schedule=='bpo':scope='BPO early restore smoke: one parent prefix and one resumed branch' if args.role else 'BPO: one backbone, two full-VM snapshots, six resumed suffixes'
    if schedule=='tvcache':scope='TVCache smoke: full backbone and one restored suffix; deterministic trie' if args.role else 'TVCache: deterministic corrected trie, two full-VM snapshots and six restored suffixes'
    if shared_cache:scope=f'GRPO {len(roles)} roots: identical step-11 measurement barrier, then concurrent original suffixes; paused analysis variant'
    result = dict(status='FAIL',backend='agentenv',task_id=cfg['task_id'],roles=roles,paths={},checks={},schedule=schedule,
                  final_roles=final_roles,scope=scope,
                  physical_page_cache='NOT_MEASURED',VMM_PSS='NOT_MEASURED',monitoring_impact='NOT_VALIDATED')
    if schedule=='grpo':result['grpo_setup_schedule']='create-all-before-setup' if create_before_setup else 'per-guest-setup'
    if skip_boundary_vmstat:result['light_skip_boundary_vmstat']=True
    if merge_boundary_meminfo:result['light_merge_boundary_meminfo']=True
    if not monitoring:result['monitor_mode']='off'
    if cache_pair:
        result.update(scope='one step-11 snapshot; early_1 and early_2 frozen suffixes; no-pause raw memory-layer collection',
                      physical_page_cache='CURRENT_VMM_DEVICE_RAW_LAYER_PFN_UNION',phases=[],cache_pair=True,
                      cache_sampling=cache_sampling)
        if provider_cache:
            result.update(scope='one step-11 snapshot; early_1 and early_2 suffixes; current VMM/device/raw/provider PFN union',
                          physical_page_cache='CURRENT_VMM_DEVICE_RAW_PROVIDER_PFN_UNION',provider_cache=True,
                          batch_lookup=batch_lookup)
        if cache_sampling=='off':
            result.update(scope='same real shared-prefix/two-suffix workflow; cache sampler explicitly disabled; regular monitoring unchanged',
                          physical_page_cache='NOT_MEASURED_SAMPLING_OFF')
    if host_restore:
        result.update(host_restore_short=not host_matched,host_matched_task=host_matched,scope='one ordinary-kernel step-11 snapshot; sequential N=1/N=2 idle restore observations; no suffix or savings ratio')
    if host_matched:result.update(scope='one ordinary-kernel step-11 snapshot; identical early_1 suffix N=1/N=2; 41 actual actions')
    if host_trace_dual:result.update(host_trace_dual=True,scope='one ordinary-kernel step-11 snapshot; N=2 identical early_1 suffixes only; 31 actual actions')
    if independent_phase:
        result.update(host_independent_phase=independent_phase,scope='Independent writable service: '+independent_phase+'; one original step-11 checkpoint; imported prefix is not executed')
    if prefix_count:result.update(host_prefix_count=prefix_count,official_task_status='NOT_RUN_PREFIX_ONLY',scope='Cold guests execute the same original 11-action prefix; no suffix or snapshot')
    if private_control:result['host_private_file_control']={key:private_control[key] for key in ('mode','slot')}
    if scaling:result.update(host_scaling_mode=scaling,scope='One service/cgroup; cumulative 1/2/4/7 '+scaling+' guests; identical original prefix or imported suffix, not the full GRPO schedule')
    if current_pair:result.update(host_current_pair=True,scope='One group; two cold guests run the same original 11 actions once, five-second observations; not a full task or sharing control')
    if getattr(args,'host_persistent_seed',None):
        result.update(host_persistent_input=True,scope=result['scope']+'; fresh plain-file inputs and one post-window residency check; archive restoration outside group')
    workers={}
    if guest_cache_policy is not None:
        result['guest_cache_policy']=guest_cache_policy
        (out/'cache-control').mkdir()
    capacity_active={};parent_damon={}
    if cache_target is not None:
        result['cache_target_mib']=cache_target
        (out/'capacity-control').mkdir()
    if damon_before_checkpoint:result['damon_before_checkpoint']=True
    stage='preflight';started=time.monotonic_ns()

    def call(label, argv, timeout=180):
        nonlocal counter
        begin=time.monotonic_ns();wall=time.time_ns()
        with lock:
            index=counter;counter+=1
        label=f'{index:04}-{label}'
        code=None
        try:
            with (out/(label+'.stderr')).open('x') as log:
                proc=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,
                                    stderr=log,text=True,timeout=timeout)
            (out/(label+'.stdout')).write_text(proc.stdout)
            code=proc.returncode
            if code:raise RuntimeError(label+': exit '+str(code))
            return proc.stdout.strip()
        finally:
            with lock:
                events.append(dict(index=index,stage=label,start_monotonic_ns=begin,timestamp_ns=wall,
                                   duration_ns=time.monotonic_ns()-begin,exit_code=code))
                (out/'events.json').write_text(json.dumps(sorted(events,key=lambda r:r['index']),indent=2)+'\n')

    def cli(label,*argv,timeout=180):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def guest(label,sid,command,timeout=180):return cli(label,'exec',sid,'bash','-lc',command,timeout=timeout)
    def capacity_start(role,sid):
        capacity_active[role]=sid
        restore=' --restore-damon '+parent_damon['original_enabled'] if damon_before_checkpoint else ''
        guest('capacity-start-'+role,sid,'nohup timeout 180 python3 -B /workspace/asb-cache-control/guest_cache_capacity.py /workspace/asb-task-capacity --serve-target-mib '+str(cache_target)+restore+' </dev/null >/tmp/asb-task-capacity.log 2>&1 &',timeout=10)
        guest('capacity-ready-'+role,sid,'for i in $(seq 1 100); do test -f /workspace/asb-task-capacity/ready && exit 0; test ! -f /workspace/asb-task-capacity/result.json || break; sleep .1; done; cat /tmp/asb-task-capacity.log; exit 1',timeout=15)
    def capacity_stop(role,sid):
        try:
            guest('capacity-stop-'+role,sid,'touch /workspace/asb-task-capacity/stop; for i in $(seq 1 100); do test -f /workspace/asb-task-capacity/result.json && exit 0; sleep .1; done; cat /tmp/asb-task-capacity.log; exit 1',timeout=15)
            for name in ('result.json','samples.jsonl'):
                value=cli('capacity-collect-'+role+'-'+name,'exec',sid,'cat','/workspace/asb-task-capacity/'+name)
                (out/'capacity-control'/(role+'-'+name)).write_text(value+'\n')
            state=json.loads((out/'capacity-control'/(role+'-result.json')).read_text())
            assert state['status']=='PASS' and state['target_bytes']==cache_target*2**20
            assert state['damon_disabled']['kdamond_pid']==-1
            expected=parent_damon['original_enabled'] if damon_before_checkpoint else state['capabilities_before']['damon']['enabled']
            assert state['damon_restored']['enabled']==expected
            if damon_before_checkpoint:assert state['parent_original_damon_enabled']==expected
        finally:capacity_active.pop(role,None)
    def checkpoint_damon(action,role,sid):
        value=json.loads(cli('checkpoint-damon-'+action+'-'+role,'exec',sid,'python3','-B',
            '/workspace/asb-cache-control/guest_cache_capacity.py','/workspace/asb-damon-before-checkpoint.json',
            '--checkpoint-damon',action,timeout=15))
        assert value['status']=='PASS'
        if action=='prepare':
            assert value['disabled']==dict(enabled='N',kdamond_pid=-1)
            parent_damon.update(value)
            name='damon-before-checkpoint.json'
        else:
            assert value['damon']['enabled']=='N' and int(value['damon']['kdamond_pid'])==-1
            assert value['original_enabled']==parent_damon['original_enabled']
            name='restored-damon-'+role+'.json'
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    def cache_control(role,sid,phase,policy):
        value=json.loads(cli('cache-control-'+role+'-'+phase,'exec',sid,'python3',
                             '/workspace/asb-cache-control/guest_page_cache_control.py',policy,timeout=60))
        assert value['status']=='PASS' and value['policy']==policy
        value.update(sandbox_id=sid,role=role,phase=phase)
        (out/'cache-control'/f'{role}-{phase}.json').write_text(json.dumps(value,indent=2)+'\n')
        if policy=='drop-clean':assert value['guest_cache_reduction_bytes']>0, 'guest cache control did not reduce cache'
        return value
    def wait_sampler(side,name):
        deadline=time.monotonic()+45
        while not (out/'live-cache'/side/name).exists():
            assert workers[side].poll() is None,side+' sampler exited before '+name
            assert time.monotonic()<deadline,side+' sampler timeout: '+name
            time.sleep(.05)

    def start_samplers():
        for side in ('child-a','child-b'):
            with (out/(side+'-sampler.log')).open('x') as log:
                workers[side]=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/collect_snapshot_child.py'),
                    '--output',str(out),'--container',args.container,'--config-home',str(args.config_home),'--side',side,
                    *(['--provider-cache'] if provider_cache else []),*(['--batch-lookup'] if batch_lookup else []),
                    *(['--expected-guest-kernel','6.1.175-asb-cache-v3-reclaim'] if cache_target is not None else [])],
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
            wait_sampler(side,'capture-ready')

    def stop_samplers():
        for side in workers:(out/'live-cache'/side/'stop').touch()
        errors=[]
        for side,worker in workers.items():
            try:code=worker.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid,signal.SIGTERM)
                try:worker.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(worker.pid,signal.SIGKILL);worker.wait(timeout=5)
                code=-1
            if code:errors.append(side+': '+str(code))
        assert not errors,errors

    async def final_check(role,sid=None):
        sid=by_role[role] if sid is None else sid
        original='early_1' if scaling=='restore' else source_role(role) if host_matched else role
        if runtime:await asyncio.to_thread(runtime.mark,sid,'official-task-tests')
        diff=await asyncio.to_thread(guest,'source-diff-'+role,sid,'git -C /testbed diff -- src/language-js/needs-parens.js')
        assert diff==('' if original=='early_2' else patch.strip()),'unexpected source patch: '+role
        mode=' --baseline' if original=='early_2' else ''
        oracle=json.loads(await asyncio.to_thread(guest,'oracle-'+role,sid,'node /workspace/prettier6604-check.cjs'+mode))
        assert oracle['status']=='PASS'
        (out/('independent-'+role+'.json')).write_text(json.dumps(oracle,indent=2)+'\n')
        await asyncio.to_thread(cli,'upload-official-'+role,'upload',sid,args.official_task/'test.patch','/workspace/prettier6604-official.patch')
        content=await asyncio.to_thread(guest,'official-'+role,sid,guest_command(args.official_task,'/workspace/prettier6604-official.patch'),timeout=160)
        official=inspect_official(args.official_task,content)
        summary=official['summary']
        assert summary['numTotalTests']==2 and summary['numPendingTests']==0 and not summary['wasInterrupted']
        observed=official['parser_results']['passed_tests']+official['parser_results']['failed_tests']
        assert sorted(observed)==sorted(expected_official['parser_results']['failed_tests'])
        assert all(summary['snapshot'][k]==0 for k in ('added','updated','filesRemoved'))
        if original!='early_2':assert signature(official)==signature(expected_official)
        (out/('official-result-'+role+'.json')).write_text(json.dumps(official,indent=2)+'\n')
        result['paths'][role].update(source_patch_check='PASS',independent_check='PASS',
                official_task_status=official['status'],official_passed=official['parser_results']['num_tests_passed'],
                official_failed=official['parser_results']['num_tests_failed'])

    light_monitor=getattr(args,'light_monitor',False)
    timed_rollout=light_monitor or not monitoring or schedule_smoke
    if timed_rollout:result['official_task_directory']=str(args.official_task.resolve())
    runtime=RuntimeMonitor(out,cli,container=args.container,light=host_restore or light_monitor) if monitoring else None
    def interrupted(signum,frame):raise RuntimeError('owned rollout interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    try:
        info=json.loads(call('service-state',['docker','inspect','--format','{{json .State}}',args.container],10))
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container],10))
        assert info['Running'] and labels.get('agentsandboxbench.run_id')==out.parents[2].name
        assert labels.get('agentenv.experiment')=='isolated-storage-check'
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        os.environ['WORKLOAD_NAME']='prettier-6604-rl-fork'
        sys.path.insert(0,str(vendor))
        spec=importlib.util.spec_from_file_location('prettier6604_grpo_controller',vendor/'controller.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.WORKLOAD=workload
        if schedule=='tvcache':module.ROLES=tuple(roles)
        def shell(*argv,capture=True):
            argv=list(argv)
            if argv[0]=='aenv':argv[0]=ROOT/'runtime/bin/aenv'
            label='snapshot-call' if argv[1:3]==['snapshot','create'] else 'controller-call'
            return call(label,argv,600)
        module.shell=shell
        class SampledPaths(module.Controller):
            async def physical_memory(self,label):return None

            async def run_segment(self,sandbox,role,segment):
                if timed_rollout:
                    if 'first_tool_start_monotonic_ns' not in result:
                        if monitoring and os.environ.get('ASB_HOST_FILE_SLICE'):
                            from host_file_monitor import measurement_group, counters
                            group,_=measurement_group()
                            begin=time.monotonic_ns(); values=counters(group)
                            result['host_before_first_tool']=dict(start_monotonic_ns=begin,
                                end_monotonic_ns=time.monotonic_ns(),counters=values)
                        result['first_tool_start_monotonic_ns']=time.monotonic_ns()
                    result.setdefault('role_first_segment_monotonic_ns',{}).setdefault(role,time.monotonic_ns())
                if full_rootfs and full_rootfs.cold:full_rootfs.cold.mark('segment-begin',role=role,segment=segment)
                if cold_points:
                    if cold_points.abort.is_set():raise RuntimeError('another role failed')
                    async def measure():
                        for i in range(len(sampled_segment(samples,role,names[segment])['actions'])):
                            await asyncio.to_thread(cold_points.point,role,sandbox,segment,f'{i+1:03}')
                    try:
                        return await asyncio.gather(super().run_segment(sandbox,role,segment),measure())
                    except BaseException:
                        cold_points.abort.set();raise
                try:return await super().run_segment(sandbox,role,segment)
                finally:
                    if full_rootfs and full_rootfs.cold:full_rootfs.cold.mark('segment-end',role=role,segment=segment)

            async def allocate(self,role,*,template=None,parent=''):
                if rollout_sampler:await asyncio.to_thread(rollout_sampler.before_create,role,restored=bool(template))
                begin=time.monotonic_ns()
                if template:
                    sid=await asyncio.to_thread(cli,'restore-'+role,'start',template,'--detach','--timeout','1800',timeout=150)
                else:
                    sid=await asyncio.to_thread(call,'cold-'+role,[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
                           '--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image'],
                           *(['--local-no-proxy'] if local_no_proxy or schedule_smoke else [])],610)
                assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid) and sid not in self.live
                self.live[sid]=sid  # Track immediately, before tool checks/uploads can fail.
                if schedule=='tvcache':self.by_role[role]=sid
                self.record('sandbox-ids.tsv',f'{role}\t{sid}\t{parent}\t{template or "root"}')
                self.record('events.tsv',f'{time.time_ns()}\tstart\troot\t{parent}\t{sid}\t{template or ""}\t{time.monotonic_ns()-begin}')
                if rollout_sampler:await asyncio.to_thread(rollout_sampler.register,role,sid)
                return sid

            async def start(self,role,*,template=None,parent=''):
                sid=await self.allocate(role,template=template,parent=parent)
                return await self.prepare(sid,role,template=template,parent=parent)

            async def prepare(self,sid,role,*,template=None,parent=''):
                if local_no_proxy or schedule_smoke:
                    from prettier_no_proxy import PROXY_KEYS
                    probe='import os; assert not any(os.environ.get(k) for k in '+repr(PROXY_KEYS)+'); print("guest-no-proxy-ready")'
                    assert await asyncio.to_thread(guest,'guest-no-proxy-check-'+role,sid,
                        'python3 -c '+shlex.quote(probe))=='guest-no-proxy-ready'
                    result.setdefault('local_no_proxy_guests',{})[role]=sid
                if template and damon_before_checkpoint:
                    await asyncio.to_thread(checkpoint_damon,'observe',role,sid)
                if runtime:await asyncio.to_thread(runtime.start,sid,role,'restored-checks' if template else 'setup')
                if template:
                    saved=self.checkpoints[template];step=saved['step']
                    assert saved['parent']==parent and sid!=parent
                    restored=json.loads(await asyncio.to_thread(guest,'boundary-'+role,sid,
                        'python3 /workspace/prettier6604-boundary.py --after-global-step '+str(step)))
                    boundary=verify_boundary(saved['boundary'],restored,step)
                    proc=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-'+role,sid,
                         'sleep 0.3; bash /workspace/exp1-process-probe.sh')).splitlines())
                    process=verify_process(saved['process'],proc)
                    (out/('restored-'+role+'.json')).write_text(json.dumps(dict(status='PASS',template=template,
                        parent=parent,sandbox_id=sid,before=saved['boundary'],after=restored,boundary=boundary,
                        process=process,branch_next_segment='NOT_RUN_SHORT_RESTORE' if host_restore else executed[role][0]),indent=2)+'\n')
                    return sid
                source=await asyncio.to_thread(guest,'source-'+role,sid,
                    'cd /testbed && python3 - <<\'EOF\'\nimport subprocess,json\nfrom pathlib import Path\nhead=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()\nassert head=="'+cfg['base_commit']+'"\np=Path("src/language-js/needs-parens.js");assert p.read_bytes()==subprocess.check_output(["git","show","HEAD:"+str(p)])\nprint(json.dumps(dict(base_commit=head,cwd=str(Path.cwd()),initial_git_status=subprocess.check_output(["git","status","--porcelain"],text=True))))\nEOF')
                (out/('source-'+role+'.json')).write_text(json.dumps(json.loads(source),indent=2)+'\n')
                await asyncio.to_thread(cli,'upload-check-'+role,'upload',sid,ROOT/'scripts/prettier6604_check.cjs','/workspace/prettier6604-check.cjs')
                baseline=json.loads(await asyncio.to_thread(guest,'baseline-'+role,sid,'node /workspace/prettier6604-check.cjs --baseline'))
                assert baseline['status']=='PASS'
                await asyncio.to_thread(cli,'upload-workload-'+role,'upload',sid,out/'frozen-input','/workspace/mixfs-workload')
                if guest_cache_policy is not None or cache_target is not None:
                    await asyncio.to_thread(cli,'upload-cache-control','upload',sid,out/'cache-control-tools','/workspace/asb-cache-control')
                if schedule in ('bpo','tvcache'):
                    await asyncio.to_thread(cli,'upload-boundary','upload',sid,ROOT/'scripts/prettier6604_boundary.py','/workspace/prettier6604-boundary.py')
                    await asyncio.to_thread(cli,'upload-process','upload',sid,ROOT/'scripts/exp1-process-probe.sh','/workspace/exp1-process-probe.sh')
                    self.root_process=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-start',sid,
                         'bash /workspace/exp1-process-probe.sh start')).splitlines())
                return sid

            async def save_prefix(self,sid,point,step):
                boundary=json.loads(await asyncio.to_thread(guest,'boundary-before-'+str(step),sid,
                         'python3 /workspace/prettier6604-boundary.py --after-global-step '+str(step)))
                verify_boundary(boundary,boundary,step)
                proc=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-before-'+str(step),sid,
                     'bash /workspace/exp1-process-probe.sh')).splitlines())
                assert all(proc[k]==self.root_process[k] for k in ('pid','start_ticks','boot_id'))
                if shared_files:await asyncio.to_thread(shared_files.phase,'before-checkpoint-'+str(step),{})
                if full_rootfs:await asyncio.to_thread(full_rootfs.phase,'before-checkpoint-'+str(step),{'backbone':sid})
                await asyncio.to_thread(runtime.stop,sid,'checkpoint')
                if rollout_sampler:await asyncio.to_thread(rollout_sampler.saving,'backbone')
                try:
                    template=await super().capture_agentenv_template(sid,point)
                    if rollout_sampler:await asyncio.to_thread(rollout_sampler.saved,'backbone')
                finally:await asyncio.to_thread(runtime.start,sid,'backbone','after-checkpoint')
                assert re.fullmatch(r'[a-fA-F0-9-]{36}',template)
                self.checkpoints[template]=dict(parent=sid,step=step,boundary=boundary,process=proc)
                (out/('checkpoint-'+str(step)+'.json')).write_text(json.dumps(dict(status='PASS',template=template,
                    **self.checkpoints[template]),indent=2)+'\n')
                if shared_files:
                    shared_files.templates[template]=step
                    await asyncio.to_thread(shared_files.phase,'after-checkpoint-'+str(step),{})
                if full_rootfs:await asyncio.to_thread(full_rootfs.phase,'after-checkpoint-'+str(step),{'backbone':sid},template=template,step=step)
                return template

            async def do_bpo(self):
                self.checkpoints={}
                backbone=self.rollouts['backbone']
                if independent_phase in ('single','dual'):
                    from host_independent_task import import_prefix
                    saved=import_prefix(args.host_checkpoint_bundle,args.host_checkpoint_source,out)
                    early=saved['template'];root=saved['parent']
                    rows=json.loads(await asyncio.to_thread(cli,'imported-snapshot-list','snapshot','list','--output','json'))
                    assert any(row['snapshotID']==early for row in rows),'imported snapshot ID missing'
                    self.checkpoints[early]={k:saved[k] for k in ('parent','step','boundary','process')}
                    self.agentenv_templates.append(early)
                    result['imported_snapshot']=early
                    result['reused_prefix_source']=str(args.host_checkpoint_source)
                elif reuse_checkpoint:
                    saved=reuse_capacity_checkpoint(reuse_checkpoint,out,args.container,args.config_home,args.service_url,recovery_checkpoint,damon_before_checkpoint)
                    early=saved['template'];root=saved['parent']
                    self.checkpoints[early]={k:saved[k] for k in ('parent','step','boundary','process')}
                    self.agentenv_templates.append(early)
                    result['reused_prefix_source']=str(reuse_checkpoint)
                    result['scope']='Reuse the preceding actual step-11 checkpoint; two new suffixes; prefix evidence imported, not executed'
                    if damon_before_checkpoint:parent_damon.update(json.loads((out/'damon-before-checkpoint.json').read_text()))
                else:
                    root=await self.start('backbone')
                    if shared_files:await asyncio.to_thread(shared_files.phase,'root-ready',{'backbone':root})
                    if full_rootfs:await asyncio.to_thread(full_rootfs.phase,'root-ready',{'backbone':root})
                    await self.run_path(root,'backbone',backbone[:2])
                    if damon_before_checkpoint:await asyncio.to_thread(checkpoint_damon,'prepare','backbone',root)
                    early=await self.save_prefix(root,'save-after-backbone-2',11)
                if host_restore:
                    if independent_phase not in ('single','dual'):
                        await asyncio.to_thread(runtime.stop,root,'parent-removal')
                        await asyncio.to_thread(cli,'delete-parent','delete',root,timeout=60)
                        self.live.pop(root)
                    if independent_phase=='capture':
                        result['checks']['checkpoint_saved_at_step_11']='PASS'
                        return {'backbone':root}
                    if host_matched:
                        from host_file_matched_task import observe_matched
                        def verify_instance(role):
                            value=verify_path(out,role,executed[role],workload,samples,names,sampled_role=source_role(role))
                            result['paths'][role]=value
                            return value
                        result['host_matched']=await observe_matched(self,early,root,runtime,args,out,cli,executed,verify_instance,final_check)
                        result['checks']['final_source_properties_and_complete_official_outcomes']='PASS'
                        return {'backbone':root,**{g['role']:g['sandbox_id'] for phase in result['host_matched']['groups'] for g in phase['guests']}}
                    from run_host_file_restore import observe
                    result['host_restore']=await observe(self,early,root,runtime,args,out,cli)
                    return {'backbone':root}
                if cache_pair:
                    if not reuse_checkpoint:
                        await asyncio.to_thread(runtime.stop,root,'parent-removal')
                        await asyncio.to_thread(cli,'delete-parent','delete',root,timeout=60)
                        self.live.pop(root)
                    restore_begin=time.monotonic_ns()
                    if cache_sampling=='on':await asyncio.to_thread(start_samplers)
                    by_role={'backbone':root}
                    for role,side in [('early_1','child-a'),('early_2','child-b')]:
                        child=await self.start(role,template=early,parent=root)
                        by_role[role]=child
                        if cache_sampling=='on':(out/'live-cache'/side/'sandbox-id.txt').write_text(child+'\n')
                    for side in workers:await asyncio.to_thread(wait_sampler,side,'sampling-ready')
                    result['restore_and_cache_ready_ns']=time.monotonic_ns()-restore_begin
                    if cache_target is not None:
                        capacity_begin=time.monotonic_ns()
                        await joined(asyncio.to_thread(capacity_start,r,by_role[r]) for r in final_roles)
                        result['guest_capacity_preparation_ns']=time.monotonic_ns()-capacity_begin
                    if guest_cache_policy is not None:
                        control_begin=time.monotonic_ns()
                        await joined(asyncio.to_thread(cache_control,r,by_role[r],'intervention',guest_cache_policy) for r in final_roles)
                        await joined(asyncio.to_thread(cache_control,r,by_role[r],'task-start','observe') for r in final_roles)
                        result['guest_cache_preparation_ns']=time.monotonic_ns()-control_begin
                    result['phases'].append(dict(name='real-task-branches',start_ns=time.time_ns()))
                    result['branch_sides']={'child-a':'early_1','child-b':'early_2'}
                    replay_begin=time.monotonic_ns()
                    await joined(self.run_path(by_role[r],r,executed[r]) for r in final_roles)
                    result['branch_replay_elapsed_ns']=time.monotonic_ns()-replay_begin
                    if cache_target is not None:
                        capacity_end=time.monotonic_ns()
                        await joined(asyncio.to_thread(capacity_stop,r,by_role[r]) for r in final_roles)
                        result['guest_capacity_stop_ns']=time.monotonic_ns()-capacity_end
                        result['checks']['continuous_guest_cache_controller_and_damon_restore']='PASS'
                        if damon_before_checkpoint:result['checks']['checkpoint_damon_inherited_and_parent_original_restored']='PASS'
                    if guest_cache_policy is not None:
                        await joined(asyncio.to_thread(cache_control,r,by_role[r],'task-end','observe') for r in final_roles)
                        result['checks']['guest_cache_intervention_and_endpoints']='PASS'
                    return by_role
                if args.role:
                    child=await self.start(args.role,template=early,parent=root)
                    await self.run_path(child,args.role,executed[args.role])
                    return {'backbone':root,args.role:child}
                await self.run_path(root,'backbone',backbone[2:4])
                late=await self.save_prefix(root,'save-after-backbone-4',24)
                await self.run_path(root,'backbone',backbone[4:])
                early_roles=[r for r in roles if r.startswith('early')]
                late_roles=[r for r in roles if r.startswith('late')]
                early_ids=await joined(self.start(r,template=early,parent=root) for r in early_roles)
                if shared_files:
                    await asyncio.to_thread(shared_files.phase,'early-restores-ready',dict(backbone=root,**dict(zip(early_roles,early_ids,strict=True))),restored={r:early for r in early_roles})
                if full_rootfs:
                    await asyncio.to_thread(full_rootfs.phase,'early-restores-ready',dict(backbone=root,**dict(zip(early_roles,early_ids,strict=True))),restored={r:early for r in early_roles})
                late_ids=await joined(self.start(r,template=late,parent=root) for r in late_roles)
                if shared_files:
                    await asyncio.to_thread(shared_files.phase,'all-restores-ready',dict(backbone=root,**dict(zip(early_roles+late_roles,early_ids+late_ids,strict=True))),restored={r:late for r in late_roles})
                if full_rootfs:
                    await asyncio.to_thread(full_rootfs.phase,'all-restores-ready',dict(backbone=root,**dict(zip(early_roles+late_roles,early_ids+late_ids,strict=True))),restored={r:late for r in late_roles})
                for point,ids,template in [('expand-after-backbone-2',early_ids,early),('expand-after-backbone-4',late_ids,late)]:
                    self.record('events.tsv',f'{time.time_ns()}\tfork\t{point}\t{root}\t{",".join(ids)}\t{template}\t0')
                by_role=dict(backbone=root,**dict(zip(early_roles+late_roles,early_ids+late_ids,strict=True)))
                if checkpoint:
                    result['checkpoint_window']=await asyncio.to_thread(checkpoint.measure,by_role,rollout_sampler.by_role)
                await joined(self.run_path(by_role[r],r,executed[r]) for r in early_roles+late_roles)
                return by_role

            async def fork(self,sandbox,count,point,role,*,event='fork'):
                assert schedule=='tvcache'
                planned=tree_plan['forks'][point]
                assert planned['parent_role']==role=='backbone' and len(planned['child_roles'])==count
                assert planned['prefix']==rollouts[role][:planned['offset']]
                begin=time.monotonic_ns();self.phase('fork:'+point)
                step={2:11,4:24}[planned['offset']]
                template=await self.save_prefix(sandbox,point,step)
                children=await joined(self.start(r,template=template,parent=sandbox) for r in planned['child_roles'])
                self.record('events.tsv',f'{time.time_ns()}\t{event}\t{point}\t{sandbox}\t{",".join(children)}\t{template}\t{time.monotonic_ns()-begin}')
                return sandbox,children

            async def do_tvcache(self):
                self.checkpoints={};self.by_role={}
                await super().do_tvcache()
                return self.by_role

            async def do_grpo(self):
                # Same successful scheduling shape as upstream; join partial failures before deletion.
                if scaling:
                    saved=None
                    if scaling=='restore':
                        from host_independent_task import checked_prefix
                        saved=checked_prefix(args.host_checkpoint_bundle,args.host_checkpoint_source)
                        rows=json.loads(await asyncio.to_thread(cli,'scaling-imported-snapshot-list','snapshot','list','--output','json'))
                        assert any(row['snapshotID']==saved['template'] for row in rows)
                        self.checkpoints={saved['template']:{k:saved[k] for k in ('parent','step','boundary','process')}}
                        self.agentenv_templates.append(saved['template'])
                        result['imported_snapshot']=saved['template']
                        result['reused_prefix_source']=str(args.host_checkpoint_source)
                    from host_scaling import observe
                    if current_pair:
                        from host_current_pair import observe
                    def verify_scaling_instance(role):
                        value=verify_path(out,role,executed[role],workload,samples,names,sampled_role='early_1' if scaling=='restore' else None)
                        result['paths'][role]=value
                        return value
                    if current_pair:
                        by_role,result['host_scaling']=await observe(self,args,out,cli,executed,verify_scaling_instance)
                    else:by_role,result['host_scaling']=await observe(self,args,out,cli,executed,verify_scaling_instance,saved=saved)
                    if scaling=='restore':result['checks']['restored_files_progress_and_process']='PASS'
                    return by_role
                if prefix_count and not private_control:
                    from host_prefix_compare import observe_prefix
                    result['prefix_before']=await asyncio.to_thread(observe_prefix,args,out,'before',{})
                if create_before_setup:
                    sandboxes=await joined(self.allocate(role) for role in roles)
                    result['all_created_monotonic_ns']=time.monotonic_ns()
                    await joined(self.prepare(sid,role) for sid,role in zip(sandboxes,roles,strict=True))
                    result['all_prepared_monotonic_ns']=time.monotonic_ns()
                else:
                    sandboxes=await joined(self.start(role) for role in roles)
                if cold_points:
                    cold_points.capture.ready()
                    await joined(asyncio.to_thread(cold_points.start_role,r) for r in roles)
                if local_no_proxy:
                    assert result['local_no_proxy_guests']==dict(zip(roles,sandboxes,strict=True))
                    result['local_no_proxy']='PASS'
                if private_control:
                    from host_private_files import gate
                    await asyncio.to_thread(gate,'guest-ready',dict(sandbox_id=sandboxes[0],container=args.container))
                self.phase('root-ready')
                if full_rootfs and full_rootfs.cold:full_rootfs.cold.mark('guests-and-setup-ready')
                if shared_files:await asyncio.to_thread(shared_files.phase,'roots-ready',dict(zip(roles,sandboxes,strict=True)))
                if physical:
                    physical.ready()
                    assert all(self.rollouts[role][:2]==['read_repo','read_typescript'] for role in roles)
                    await joined(self.run_path(sid,role,self.rollouts[role][:2]) for sid,role in zip(sandboxes,roles,strict=True))
                    self.phase('shared-cache-prefix11')
                    for sid in sandboxes:await asyncio.to_thread(runtime.mark,sid,'shared-cache-analysis')
                    from shared_task_cache import measure
                    measurement=await asyncio.to_thread(measure,physical,dict(zip(roles,sandboxes,strict=True)),cfg['source_image'])
                    result['shared_cache_status']=measurement['status']
                    result['physical_page_cache']='MEASURED_AT_COMMON_PREFIX'
                    result['checks']['shared_cache_probe_and_task_state']='PASS'
                    await joined(self.run_path(sid,role,self.rollouts[role][2:]) for sid,role in zip(sandboxes,roles,strict=True))
                else:
                    await joined(self.run_path(sid,role,executed[role]) for sid,role in zip(sandboxes,roles,strict=True))
                if private_control:
                    from host_private_files import gate
                    # The original verify_path below still checks all actions and exit codes.
                    await asyncio.to_thread(gate,'paths-finished',dict(sandbox_id=sandboxes[0],task_end_monotonic_ns=time.monotonic_ns()))
                elif prefix_count:
                    result['prefix_after']=await asyncio.to_thread(observe_prefix,args,out,'after',dict(zip(roles,sandboxes,strict=True)))
                return dict(zip(roles,sandboxes,strict=True))

        controller=SampledPaths(SimpleNamespace(system='agentenv',raw_dir=out,timeout=600))
        if full_rootfs_mode:
            from host_file_monitor import measurement_group
            from host_sandbox_monitor import Binding
            from host_full_rootfs_control import FullRootfsControl
            group,_=measurement_group()
            full_rootfs=FullRootfsControl(full_rootfs_mode,out,group,Binding(args.container,out),schedule=schedule,residency=full_rootfs_residency,cold_read=full_rootfs_cold_read)
            if full_rootfs_residency=='off':result['host_full_rootfs_residency']='off'
        if full_shared:
            from host_file_monitor import measurement_group
            from host_sandbox_monitor import Binding
            from host_rollout_shared import SharedRollout
            group,_=measurement_group()
            shared_files=SharedRollout(group,Binding(args.container,out),out,schedule,
                expanded=os.environ.get('ASB_HOST_EXPANDED_FILES')=='on')
        controller.phase('prepare')
        shutil.copytree(workload,out/'frozen-input')
        if guest_cache_policy is not None or cache_target is not None:
            tools=out/'cache-control-tools';tools.mkdir()
            for name in ('guest_page_cache_control.py','kernel_cache_metadata.py','guest_diskstats.py'):
                shutil.copy2(ROOT/'scripts'/name,tools/name)
            if cache_target is not None:shutil.copy2(ROOT/'scripts/guest_cache_capacity.py',tools/'guest_cache_capacity.py')
        if retain_checkpoint:
            (out/'capacity-checkpoint.json').write_text(json.dumps(dict(container=args.container,
                config_home=str(args.config_home.resolve()),service_url=args.service_url),indent=2)+'\n')
        runner=out/'frozen-input/guest-runner.sh'
        shutil.copy2(runner,runner.with_name('guest-runner.original.sh'))
        if merge_boundary_meminfo:shutil.copy2(ROOT/'scripts/prototype_boundary_memory.py',out/'frozen-input/prototype_boundary_memory.py')
        if monitoring:runner.write_text(boundary_runner(runner.read_text(),guest_phases=True,skip_vmstat=skip_boundary_vmstat,**({'merge_meminfo':True} if merge_boundary_meminfo else {})))
        else:
            from task_metrics_prefix import without_sampling
            runner.write_text(without_sampling(runner.read_text()))
        if per_action:
            from grpo_action_gate import gated_runner
            runner.write_text(gated_runner(runner.read_text(),tool_time=True))
        (out/'sampling-variant.json').write_text(json.dumps(dict(cold_action_gate=per_action,mode='continuous per sandbox; boundary samples per segment' if monitoring else 'off',phase_updates='guest runner' if monitoring else 'none',original_runner_sha256=sha(workload/'guest-runner.sh'),executed_runner_sha256=sha(runner),**({'light_merge_boundary_meminfo':True} if merge_boundary_meminfo else {})),indent=2)+'\n')
        shutil.copy2(args.frozen_export/'provenance.json',out/'input-provenance.json')
        (out/'reference-source.patch').write_text(patch)
        (out/'expected-official-audit.json').write_text(json.dumps(expected_official,indent=2)+'\n')
        code=out/'code';code.mkdir()
        if schedule_smoke:
            for name in ('schedule_smoke_metrics.py','host_file_monitor.py','experiment_memory.py','host_rootfs_layers.py','host_prefix_compare.py','prettier_no_proxy.py'):
                shutil.copy2(ROOT/'scripts'/name,code/name)
            if getattr(args,'schedule_cache_layers',False):
                for name in ('host_schedule_cache_layers.py','host_deleted_backing.py','isolated_storage.py','host_memory_backing_links.py'):
                    shutil.copy2(ROOT/'scripts'/name,code/name)
            if getattr(args,'schedule_expanded_files',False):
                for name in ('host_schedule_files.py','host_special_metadata.py'):
                    shutil.copy2(ROOT/'scripts'/name,code/name)
        for p in [Path(__file__),ROOT/'scripts/prepare_prettier6604_rollouts.py',ROOT/'scripts/prettier6604_inputs.py',
                  ROOT/'scripts/agentenv_cold_start.py',ROOT/'scripts/prettier6604_check.cjs',
                  ROOT/'scripts/exp1_official_task.py',ROOT/'scripts/audit_prettier6604_official.py',
                  ROOT/'scripts/prettier6604_boundary.py',ROOT/'scripts/exp1-process-probe.sh',vendor/'controller.py']:
            shutil.copy2(p,code/p.name)
        if full_shared or rootfs_short:
            for name in ('host_rollout_shared.py','host_rootfs_layers.py','host_prefix_compare.py','host_scaling.py','run_host_file_restore.py'):
                shutil.copy2(ROOT/'scripts'/name,code/name)
            if os.environ.get('ASB_HOST_EXPANDED_FILES')=='on':shutil.copy2(ROOT/'scripts/host_expanded_files.py',code/'host_expanded_files.py')
        if current_pair:shutil.copy2(ROOT/'scripts/host_current_pair.py',code/'host_current_pair.py')
        if merge_boundary_meminfo:shutil.copy2(ROOT/'scripts/prototype_boundary_memory.py',code/'prototype_boundary_memory.py')
        (out/'source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in code.iterdir()},indent=2)+'\n')
        if full_rootfs_mode:
            for name in ('host_full_rootfs_control.py','host_private_files.py','host_rootfs_layers.py','host_file_monitor.py','experiment_memory.py','host_sandbox_monitor.py','host_rollout_shared.py','host_prefix_compare.py'):
                shutil.copy2(ROOT/'scripts'/name,code/name)
            if full_rootfs_cold_read:
                for name in ('host_grpo_cold_read.py','host_cold_file_read.py'):shutil.copy2(ROOT/'scripts'/name,code/name)
        legacy_host_scope=('Explicit VMM child-cgroup check: legacy host/memory-samples.tsv covers service only; authoritative parent/child counters are in outer vmm-cgroup-samples/memory.jsonl'
                           if os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK') else 'one temporary service cgroup; includes all VMMs and image cache; host phases may overlap')
        (out/'effective-config.json').write_text(json.dumps(dict(cfg,paths=rollouts,selected_roles=roles,scope=result['scope'],
               schedule=schedule,executed_paths=executed,final_roles=final_roles,
               **({'light_skip_boundary_vmstat':True} if skip_boundary_vmstat else {}),
               **({'light_merge_boundary_meminfo':True} if merge_boundary_meminfo else {}),
               grpo_setup_schedule=result.get('grpo_setup_schedule'),
               prefix_tree_plan=tree_plan,
               shared_cache_measurement=shared_cache,root_count=root_count,
               guest_cache_policy=guest_cache_policy,
               cache_target_mib=cache_target,
               damon_before_checkpoint=damon_before_checkpoint,
               variant='role-specific inspect manifests from original sampling',sandbox_ttl_seconds=1800,
               host_scope='experiment_slice_cgroup' if schedule_smoke else legacy_host_scope,
               legacy_host_scope=legacy_host_scope,
               guest_scope='continuous per actual VM ID after monitor setup; explicit snapshot gaps; logical memory, not physical deduplication'),indent=2)+'\n')
        if monitoring and not (host_restore or light_monitor):
            with (out/'host-monitor.log').open('x') as log:
                monitor=subprocess.Popen(['bash',ROOT/'IncrementalDAX_moti/motivation/experiments/lib/host-memory-monitor.sh',
                    out/'host/memory-samples.tsv',controller.phase_file,'0.1',args.container],env=env,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        if schedule_smoke and monitoring:
            from experiment_memory import HOST_SCOPE, GroupSampler, resolve_scope
            from host_sandbox_monitor import Binding
            binding=Binding(args.container,out)
            context=resolve_scope(binding.private_service_cgroup)
            result.update(host_scope=HOST_SCOPE, legacy_host_scope='private_service_cgroup',
                          legacy_cgroup=str(binding.private_service_cgroup), experiment_memory_context=context)
            if context['status']=='AVAILABLE':
                if os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK'):
                    # The outer sampler already starts before guests and now
                    # writes the same canonical fields; do not sample it twice.
                    result['experiment_memory_external_series']=os.environ.get('ASB_EXPERIMENT_MEMORY_SERIES',
                        str(out.parents[2]/'vmm-cgroup-samples/memory.jsonl'))
                else:
                    experiment_sampler=GroupSampler(Path(context['parent']),out/'experiment-memory')
        if shared_cache:
            sources=['shared_task_cache.py','task_physical_cache.py','agentenv_whole_cache.py','measure_file_physical.py',
                     'guest_whole_cache_probe.py','guest_cache_marker.py','trenvx_whole_cache.py','paused_agentenv_boundary.py',
                     'cached_file_inventory.py','guest_cached_file_inventory.py','guest_named_file_probe.py',
                     'guest_file_type_check.py','kernel_file_cache.py','report_file_inventory.py']
            physical_code=out/'physical-code';physical_code.mkdir()
            for name in sources:shutil.copy2(ROOT/'scripts'/name,physical_code/name)
            (out/'physical-source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in physical_code.iterdir()},indent=2)+'\n')
            from task_physical_cache import TaskPhysicalCache
            physical=TaskPhysicalCache(out,args.container,cli)
        if light_rollout:
            from light_prettier_rollout import LightRollout
            rollout_sampler=LightRollout(args.container,out,roles)
            if per_action:
                from grpo_cold_points import ColdPoints
                cold_points=ColdPoints(out,args.container,cli,rollout_sampler.binding)
                rollout_sampler.page_cache=cold_points
            if getattr(args,'bpo_checkpoint_window',False):
                from bpo_checkpoint_window import Window
                checkpoint=Window(out,args.container,cli,rollout_sampler.binding)
                shutil.copy2(ROOT/'scripts/bpo_checkpoint_window.py',code/'bpo_checkpoint_window.py')
            for name in ('light_prettier_rollout.py','light_memory_round.py','run_guest_light_memory.py','host_sandbox_monitor.py'):
                shutil.copy2(ROOT/'scripts'/name,code/name)
        stage='frozen-paths';controller.phase(stage)
        if shared_files:shared_files.phase('before-guests',{})
        if full_rootfs:full_rootfs.before()
        if timed_rollout:result['rollout_start_monotonic_ns']=time.monotonic_ns()
        if full_rootfs and full_rootfs.cold:
            full_rootfs.cold.mark('guest-creation-begin',monotonic_ns=result['rollout_start_monotonic_ns'])
            full_rootfs.cold.start()
        by_role=asyncio.run(getattr(controller,'do_'+schedule)())
        if schedule_smoke:
            result['original_paths_end_monotonic_ns']=time.monotonic_ns()
            if monitoring:
                from schedule_smoke_metrics import observe
                result['schedule_smoke_metrics']=observe(args.container,out,by_role,result['original_paths_end_monotonic_ns'],
                    **({'expanded':True} if getattr(args,'schedule_expanded_files',False) else {}),
                    **({'cache_layers':True} if getattr(args,'schedule_cache_layers',False) else {}),
                    **({'block_cache_method':args.schedule_block_cache_method} if getattr(args,'schedule_block_cache_method',None) is not None else {}),
                    **({'split_ram':True} if getattr(args,'schedule_split_ram_cache',False) else {}),
                    **({'memory_backing_links':True} if getattr(args,'schedule_memory_backing_links',False) else {}))
            else:result['schedule_smoke_metrics']=dict(status='NOT_REQUESTED',reason='Explicit sampling-off control')
        if full_rootfs:
            try:result['host_full_rootfs_control']=full_rootfs.after(by_role,time.monotonic_ns())
            except Exception:
                # As with the existing shared query, complete all original checks.
                result['host_full_rootfs_control']=full_rootfs.state
        if shared_files:
            paths_end=time.monotonic_ns()
            try:result['host_shared_files']=shared_files.query(by_role,paths_end)
            except Exception as error:
                # Finish the original official checks even if the added query fails.
                shared_files.state.update(status='FAIL',error=repr(error));shared_files.save()
                result['host_shared_files']=shared_files.state
        with (out/'segments.tsv').open() as stream:trace=list(csv.DictReader(stream,delimiter='\t'))
        assert len(set(by_role.values()))==len(roles)
        for role,path in executed.items():
            assert [r['segment'] for r in trace if r['rollout']==role]==path
            assert all(r['sandbox_id']==by_role[role] for r in trace if r['rollout']==role)
            if role not in result['paths']:result['paths'][role]=verify_path(out,role,path,workload,samples,names,sampling=monitoring,sampled_role='early_1' if scaling=='restore' else None,skip_boundary_vmstat=skip_boundary_vmstat,**({'merge_boundary_meminfo':True} if merge_boundary_meminfo else {}))
            if not monitoring:
                for segment in path:
                    with (out/'rollouts'/role/segment/'guest-vmstat.tsv').open() as stream:
                        assert not list(csv.DictReader(stream,delimiter='\t')), 'monitor-off has boundary vmstat samples'
        result['checks']['sampled_actions_order_exit_codes_and_distinct_instances' if schedule!='grpo' or scaling=='restore' else 'sampled_actions_order_exit_codes_and_distinct_roots']='PASS'
        if schedule=='tvcache':
            assert [[r['rollout'],r['segment']] for r in trace]==tree_plan['segments']
            result['checks']['deterministic_prefix_tree_order']='PASS'
        if schedule in ('bpo','tvcache'):
            if independent_phase!='capture':result['checks']['restored_files_progress_and_process']='PASS'
            result['logical_action_count']=({'capture':11,'single':21,'dual':42}[independent_phase] if independent_phase else 42 if host_trace_dual else 63 if host_matched else 11 if host_restore else sum(len(sampled_segment(samples,r,names[s])['actions']) for r in final_roles for s in rollouts[r]))
        result['action_count']=sum(p['action_count'] for p in result['paths'].values())
        result['reused_action_count']=result['paths']['backbone']['action_count'] if reuse_checkpoint or independent_phase in ('single','dual') else 0
        result['verified_action_count']=result['action_count']
        result['action_count']-=result['reused_action_count']
        result['actual_action_count']=result['action_count']
        result['segment_count']=len(trace)
        if skip_boundary_vmstat:
            result['boundary_vmstat']=dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,
                boundary_memory_rows=2*result['segment_count']+2*result['action_count'])
            result['checks']['boundary_vmstat_skipped_memory_preserved']='PASS'
        if merge_boundary_meminfo:
            from sandbox_runtime_monitor import boundary_meminfo_metadata
            result['boundary_meminfo']=boundary_meminfo_metadata(result['action_count'],result['segment_count'])
            result['checks']['boundary_meminfo_timer_merge_preserved']='PASS'
        stage='final-state-and-official-tests';controller.phase(stage)
        asyncio.run(joined(final_check(role) for role in final_roles))
        for role in set(roles)-set(final_roles):
            if not host_matched or role=='backbone':result['paths'][role]['official_task_status']='NOT_RUN_PREFIX_ONLY'
        if host_restore or prefix_count or scaling=='cold':result['official_evaluation']='NOT_RUN_PREFIX_ONLY'
        else:result['checks']['final_source_properties_and_complete_official_outcomes']='PASS'
        if timed_rollout:result['rollout_end_monotonic_ns']=time.monotonic_ns()
        if host_matched and independent_phase!='capture':result['official_evaluation']='COMPLETED_PER_INSTANCE_BEFORE_REMOVAL'
        (out/'terminal-storage-allocation.json').write_text(json.dumps(storage_allocation(out.parent.parent),indent=2)+'\n')
        if cache_pair:result['phases'][0]['end_ns']=time.time_ns()
        if full_rootfs:
            from host_full_rootfs_control import validate_observation
            validate_observation(full_rootfs.state,full_rootfs_residency)
        success=True
        if schedule_smoke and monitoring and result['schedule_smoke_metrics']['status']!='OBSERVED':
            success=False;result.update(error=result['schedule_smoke_metrics']['error'],failed_stage='schedule-smoke-metrics')
        if shared_files and shared_files.state['status']!='OBSERVED':
            success=False;result.update(error=shared_files.state['error'],failed_stage='host-shared-files')
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage)
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={}
        if checkpoint:checkpoint.close()
        if rollout_sampler:
            try:result['light_rollout_sampling']=rollout_sampler.stop()
            except Exception as error:cleanup['light_rollout_error']=repr(error);success=False
        cold_reaped=True
        if full_rootfs and full_rootfs.cold:
            try:full_rootfs.cold.stop()
            except Exception as error:cleanup['cold_reader_error']=repr(error);success=False
            cold_reaped=not full_rootfs.cold.is_alive();cleanup['cold_reader_reaped']=cold_reaped
            if not cold_reaped:
                cleanup['cold_reader_cleanup_deferred']='Controller exits before outer isolated-service cleanup; do not delete guests with an active query'
                success=False
            if not success:full_rootfs.state.update(status='FAIL',error=result.get('error',cleanup.get('cold_reader_error','cold read incomplete')))
            full_rootfs.save();result['host_full_rootfs_control']=full_rootfs.state
        if light_monitor or not monitoring:cleanup['legacy_host_monitor_not_requested']=True
        if not monitoring:cleanup['monitoring_disabled']=True
        for role,sid in list(capacity_active.items()):
            try:capacity_stop(role,sid)
            except Exception as error:cleanup.setdefault('capacity_errors',[]).append(repr(error));success=False
        if workers:
            try:
                stop_begin=time.monotonic_ns()
                stop_samplers();cleanup['samplers_stopped']=True
                result['cache_sampler_stop_ns']=time.monotonic_ns()-stop_begin
            except Exception as error:cleanup['samplers_error']=repr(error);success=False
        if physical:
            physical.close();cleanup['physical_tracer_stopped']=physical.tracer.poll() is not None
        if controller and cold_reaped:
            removed=[]
            if light_monitor or not monitoring:
                cleanup_begin=time.monotonic_ns()
                closed=parallel_guest_cleanup(list(controller.live),runtime.stop if runtime else None,lambda sid:cli('delete-owned','delete',sid,timeout=60))
                result['light_cleanup_timing']=dict(start_monotonic_ns=cleanup_begin,end_monotonic_ns=time.monotonic_ns(),guests=closed)
                for row in closed:
                    if row['deleted']:removed.append(row['sandbox_id']);controller.live.pop(row['sandbox_id'])
                    if row['errors']:cleanup.setdefault('errors',[]).extend(row['errors']);success=False
            else:
                for sid in list(controller.live):
                    try:runtime.stop(sid)
                    except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
                    try:cli('delete-owned','delete',sid,timeout=60);removed.append(sid);controller.live.pop(sid)
                    except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
            cleanup['sandboxes_removed']=removed
            if controller.agentenv_templates:
                if success and independent_phase=='capture':
                    cleanup['snapshots_retained_for_export']=controller.agentenv_templates
                elif success and retain_checkpoint:
                    cleanup['snapshots_retained_for_reuse']=controller.agentenv_templates
                elif success:
                    removed_templates=[]
                    for template in controller.agentenv_templates:
                        try:cli('delete-snapshot','template','delete',template,timeout=60);removed_templates.append(template)
                        except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
                    cleanup['snapshots_removed']=removed_templates
                else:cleanup['snapshots_retained_for_failure']=controller.agentenv_templates
            try:
                remaining=json.loads(cli('list-after','list','--output','json',timeout=30))
                assert remaining==[], 'owned service has remaining sandboxes'
                cleanup['service_sandbox_list_empty']=True
            except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
        if monitor:
            if monitor.poll() is not None:cleanup['monitor_unexpected_exit']=monitor.returncode;success=False
            else:
                os.killpg(monitor.pid,signal.SIGTERM)
                try:monitor.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(monitor.pid,signal.SIGKILL);monitor.wait(timeout=5)
                cleanup['monitor_stopped']=True
        if experiment_sampler is not None:
            try:result['experiment_memory_collector']=experiment_sampler.stop()
            except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
        controller_end=time.monotonic_ns()
        if create_before_setup:
            result['controller_end_monotonic_ns']=controller_end
            if 'all_created_monotonic_ns' in result:
                result['created_to_controller_end_ns']=controller_end-result['all_created_monotonic_ns']
            if 'all_prepared_monotonic_ns' in result:
                result['create_to_setup_ns']=result['all_prepared_monotonic_ns']-result['all_created_monotonic_ns']
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup,controller_elapsed_ns=controller_end-started)
        if success and not light_monitor:result['VMM_PSS']='NOT_REQUESTED' if rootfs_short or not monitoring else 'MEASURED_PER_OWNED_VMM'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-export',type=Path,required=True)
    parser.add_argument('--official-task',type=Path,required=True)
    parser.add_argument('--expected-official-audit',type=Path,required=True)
    parser.add_argument('--role',choices=['early_1','early_2','early_3','late_1','late_2','late_3'])
    parser.add_argument('--schedule',choices=['grpo','bpo','tvcache'],default='grpo')
    parser.add_argument('--schedule-sampling-control',action='store_true',help='Explicit GRPO on/off pair with the same local-only backend')
    parser.add_argument('--schedule-block-cache-method',choices=('mincore','cachestat','compare'),help='Explicit block cache method; default mincore; compare queries both per pinned object; requires cache layers')
    parser.add_argument('--schedule-memory-backing-links',action='store_true',help='Opt-in saved memory config and lower identity/query links; requires cache layers')
    parser.add_argument('--schedule-split-ram-cache',action='store_true',help='Extra preplanned RAM cachestat; requires schedule cache layers and explicit cachestat')
    parser.add_argument('--schedule-cache-layers',action='store_true',help='Opt-in block/RAM, deleted TMPFS, private run/tmp and outer loop caches; requires expanded schedule files')
    parser.add_argument('--schedule-expanded-files',action='store_true',help='Opt-in endpoint regular-file inventory with explicit unknowns; requires schedule smoke metrics')
    parser.add_argument('--light-rollout',action='store_true',help='Explicit BPO child-cgroup timestamped PSS rounds; original checks retained')
    parser.add_argument('--schedule-smoke-metrics',action='store_true',help='Explicit full-profile seven-role smoke: local-only creation, VMM PSS and 19 initial lower P1')
    parser.add_argument('--host-trace-dual',action='store_true',help='Only two matched restores; external targeted cache tracer')
    parser.add_argument('--host-independent-phase',choices=('capture','single','dual'))
    parser.add_argument('--host-checkpoint-bundle',type=Path)
    parser.add_argument('--host-checkpoint-source',type=Path)
    parser.add_argument('--host-matched-task',action='store_true',help='Identical early_1 suffix for one then two restored guests')
    parser.add_argument('--host-rootfs-short',action='store_true',help='Imported dual suffix only; shared-file query, no PSS or broad live inventory')
    parser.add_argument('--host-prefix-count',type=int,choices=(1,7))
    parser.add_argument('--host-shared-files',action='store_true',help='Full paths: selected shared backings after original actions, before official checks')
    parser.add_argument('--host-full-rootfs-mode',choices=('shared','private'),help='Complete GRPO/BPO: initial-layer copies, generated layers separate')
    parser.add_argument('--host-full-rootfs-residency',choices=('on','off'),default='on')
    parser.add_argument('--host-full-rootfs-cold-read',action='store_true',help='Full GRPO only: cold initial lower files and serial background residency')
    parser.add_argument('--host-scaling-mode',choices=('cold','restore'))
    parser.add_argument('--host-current-pair',action='store_true')
    parser.add_argument('--host-persistent-seed',type=Path)
    parser.add_argument('--host-prefix-roots',type=Path)
    parser.add_argument('--host-file-roots',type=Path,help='Explicit regular-file roots for live matched-task residency')
    parser.add_argument('--host-restore-short',action='store_true',help='Ordinary-kernel step-11 snapshot; N=1 then N=2, five seconds each, no suffix')
    parser.add_argument('--snapshot-cache-pair',action='store_true',help='One step-11 snapshot and two actual early suffixes with no-pause layer PFNs')
    parser.add_argument('--snapshot-cache-sampling',choices=('on','off'),default='on',help='Real pair only; matched cache-collection timing control')
    parser.add_argument('--snapshot-guest-cache-policy',choices=('preserve','drop-clean'),help='Sampled real pair only; sync both guests, optionally drop clean guest file cache before suffixes')
    parser.add_argument('--snapshot-cache-target-mib',type=int,choices=(64,128),help='Sampled real pair only; continuous clean-file reclaim target using the private reclaim kernel')
    parser.add_argument('--retain-capacity-checkpoint',action='store_true',help='64 MiB pair only; keep the owned checkpoint for the following 128 MiB run')
    parser.add_argument('--reuse-capacity-checkpoint',type=Path,help='128 MiB pair only; reuse the preceding sibling 64 MiB run checkpoint and prefix evidence')
    parser.add_argument('--capacity-checkpoint-recovery',type=Path,help='Explicit private-service rebinding receipt after verified full-volume recovery')
    parser.add_argument('--bpo-checkpoint-window',action='store_true')
    parser.add_argument('--snapshot-damon-before-checkpoint',action='store_true',help='Capacity checkpoint comparison only; stop parent DAMON before save and restore the parent original state after each suffix')
    parser.add_argument('--snapshot-provider-cache',action='store_true',help='Explicit provider collection in the real pair only')
    parser.add_argument('--snapshot-bpf-batch',action='store_true',help='Batch lookup in the explicit provider pair')
    parser.add_argument('--shared-cache',action='store_true',help='Pause all roots after the common 11-step prefix and count shared PFNs')
    parser.add_argument('--root-count',type=int,choices=[1,2,7])
    parser.add_argument('--container');parser.add_argument('--config-home',type=Path)
    parser.add_argument('--service-url');parser.add_argument('--output',type=Path)
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--light-monitor',action='store_true',help='Guest meminfo only at 0.1 s; no per-VMM or legacy host collector')
    parser.add_argument('--monitor-mode',choices=('continuous','off'),default='continuous',help='Off: ordinary seven-guest GRPO, retain timings and original task checks')
    parser.add_argument('--grpo-create-before-setup',action='store_true',help='Explicit guest-only GRPO: finish all seven creates before concurrent sampling/check/upload setup')
    parser.add_argument('--light-skip-boundary-vmstat',action='store_true',help='Sampled create-before-setup or local current GRPO: retain memory, skip boundary vmstat')
    parser.add_argument('--light-merge-boundary-meminfo',action='store_true',help='Local current light GRPO with skipped vmstat only: reuse action timer Python for boundary meminfo')
    args=parser.parse_args()
    if not args.check_only and not all((args.container,args.config_home,args.service_url,args.output)):
        parser.error('live run requires owned container, config-home, service-url, output')
    raise SystemExit(run(args))
