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
from host_sandbox_monitor import validate_pss_interval

ROOT = Path(__file__).resolve().parents[1]
SERVICE = os.environ.get('ASB_AGENTENV_SOURCE_SERVICE', 'agentenv-smoke-20260922T114006Z-1387374')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', SERVICE):
    raise ValueError('invalid explicit AgentENV source service name')
IMAGE = 'sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681'
CAPACITY = 20*GIB


def finish_fixture(image,success,receipt,cleanup,*,retain_for_final_acceptance=False):
    """Opt-in private calibration keeps the unmounted volume even on inner PASS."""
    if retain_for_final_acceptance:
        if not image.is_file():raise FileNotFoundError('private fixture missing before final acceptance')
        receipt['fixture_retention']=dict(policy='retain_until_explicit_cleanup_after_final_acceptance',
            path=str(image),status='PENDING_FINAL_ACCEPTANCE' if success else 'RETAINED_AFTER_INNER_FAIL')
        cleanup['fixture_retained_for_final_acceptance']=True
        if not success:cleanup['failed_fixture_retained']=True
    elif success:
        receipt['fixture_allocated_before_remove_bytes']=image.stat().st_blocks*512
        image.unlink();cleanup['successful_fixture_removed']=True
        receipt['failure_fixture_retained']=False
    else:cleanup['failed_fixture_retained']=image.exists()


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
    if getattr(args,'host_full_rootfs_mode',None):
        if not (export and seed and schedule in ('grpo','bpo') and not (role or checkpoint or getattr(args,'prettier6604_readonly_replay',False))):
            raise ValueError('full rootfs copies require complete GRPO/BPO with verified seed')
        return (48 if schedule=='grpo' else 64)*GIB
    if getattr(args,'online_task_config',None):return 16*GIB
    scaling=getattr(args,'host_scaling_mode',None)
    if scaling:
        if scaling not in ('cold','restore') or not (export and seed and schedule=='grpo' and not (role or checkpoint or getattr(args,'prettier6604_readonly_replay',False))):
            raise ValueError('host scaling requires the explicit GRPO adapter and verified seed')
        if getattr(args,'host_current_pair',False):
            if scaling!='cold':raise ValueError('current pair only supports two cold prefixes')
            return 8*GIB
        return (16 if scaling=='cold' else 24)*GIB
    if getattr(args,'prettier6604_readonly_replay',False):
        if not (getattr(args,'prettier6604_task',None) and seed and not (export or role or checkpoint)):
            raise ValueError('8 GiB Prettier replay requires one complete backbone and verified seed without checkpoint')
        return 8*GIB
    if getattr(args,'host_restore_short',False) or getattr(args,'host_matched_task',False):
        if not (export and seed and schedule=='bpo' and not (role or checkpoint)):
            raise ValueError('host restore short requires one BPO snapshot and verified seed')
        return 24*GIB
    if getattr(args,'prettier6604_snapshot_cache_pair',False):
        if not (export and not role and schedule=='bpo' and not checkpoint and seed):
            raise ValueError('24 GiB actual-task pair requires one BPO snapshot and verified seed')
        return 24*GIB
    reduced=getattr(args,'prettier6604_grpo_capacity_gib',32)
    if reduced!=32:
        if not (reduced==16 and export and not role and schedule=='grpo' and not checkpoint and seed):
            raise ValueError('16 GiB requires seven GRPO roots, verified converted seed and no snapshots')
        return reduced*GIB
    return 32*GIB if export and not role else 24*GIB if checkpoint or export and schedule in ('bpo','tvcache') else CAPACITY


def cancelled(signum, frame):
    raise RuntimeError('validation interrupted: '+str(signum))


def source_vm_identities_unchanged(before, after, cgroup, *, allow_empty=False):
    def owned(rows):
        return [v for v in rows if v['cgroup'].strip()==cgroup.strip() or v['cgroup'].strip().startswith(cgroup.strip()+'/')]
    return (allow_empty or bool(owned(before))) and owned(before)==owned(after)


def late_owned_container(run_id, call):
    rows=call('late-owned-container',['docker','ps','-aq','--filter','label=agentsandboxbench.run_id='+run_id],30).split()
    if len(rows)>1:raise RuntimeError('multiple containers for this run')
    return rows[0] if rows else None


def ordinary_current_only(args, *, monitor_mode='continuous'):
    """Full cold single tasks or explicit local current GRPO, without extra analysis."""
    if not (getattr(args,'immutable_cache_seed',None) or getattr(args,'persistent_cache_seed',None)):return False
    local_grpo=(os.environ.get('ASB_LOCAL_NO_PROXY')=='on' and
        getattr(args,'persistent_cache_seed',None) and getattr(args,'host_current_only',False) and
        getattr(args,'host_file_monitor',False) and getattr(args,'prettier6604_export',None) and
        getattr(args,'prettier6604_schedule','grpo')=='grpo' and
        (not getattr(args,'light_skip_boundary_vmstat',False) or
            monitor_mode=='continuous' and getattr(args,'light_monitor',False)) and
        not any(getattr(args,k,None) for k in ('prettier6604_readonly_replay','prettier6604_shared_cache',
            'prettier6604_root_count','host_scaling_mode','host_prefix_count','host_shared_files',
            'host_full_rootfs_mode','host_independent_phase','host_restore_short','host_matched_task',
            'prettier6604_grpo_create_before_setup','local_no_proxy_check')))
    if getattr(args,'light_merge_boundary_meminfo',False) and not (local_grpo and getattr(args,'light_skip_boundary_vmstat',False)):
        return False
    excluded=('prettier6604_export','prettier6604_role','prettier6604_checkpoint',
        'python_checkpoint','prettier6604_physical_cache_after','python_physical_cache_after',
        'diagnostic_guest_kernel','online_task_config','python_tool_capture_demo',
        'python_durable_checkpoint_demo','python_online_agent_config','python_cache_smoke',
        'python_runtime_smoke','python_command_diagnostic','python_diagnostic_only',
        'python_evaluation_patch','python_backbone_oci','damon_observe','damon_disable',
        'host_file_pair_smoke','startup_sampling','exp2_cache_mode')
    if any(getattr(args,key,False) for key in excluded if key!='prettier6604_export' or not local_grpo):return False
    prettier=bool(getattr(args,'prettier6604_task',None))
    python=bool(getattr(args,'python_backbone_inputs',None))
    if prettier==python:return False
    if prettier:
        return ((getattr(args,'prettier6604_readonly_replay',False) or local_grpo) and
                getattr(args,'prettier6604_monitor_mode','continuous')==monitor_mode)
    return (getattr(args,'python_task_profile','xarray6992') in ('sphinx7590','xarray6992') and
            getattr(args,'python_readonly_replay',False) and getattr(args,'python_persistent_sampling',False) and
            getattr(args,'python_monitor_mode','continuous')==monitor_mode)


def main(args):
    os.umask(0o077)
    if os.geteuid()!=0: raise RuntimeError('root required')
    diagnostic_server=getattr(args,'diagnostic_server_binary',None)
    if diagnostic_server:
        if not ((getattr(args,'guest_cache_light_restore_one',False) or getattr(args,'guest_cache_rollout_sessions',False) or getattr(args,'light_rollout',False)) and os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK')):
            raise ValueError('diagnostic server requires explicit child-cgroup light restore')
        diagnostic_server=Path(diagnostic_server).resolve()
        if not diagnostic_server.is_relative_to(ROOT/'.artifacts') or not diagnostic_server.is_file() or not os.access(diagnostic_server,os.X_OK):
            raise ValueError('invalid diagnostic server binary')
    density_path=getattr(args,'density_request',None)
    density=None
    if density_path:
        from host_density import request as density_request
        density=density_request(density_path)
    platform_cfg=None
    external_config=getattr(args,'external_workload_config',None)
    external_upstream=getattr(args,'external_workload_upstream',None)
    django_schedule=getattr(args,'django_schedule',None)
    django_schedule_inputs=getattr(args,'django_schedule_inputs',None)
    c4_schedule=getattr(args,'c4_schedule',None)
    c4_schedule_inputs=getattr(args,'c4_schedule_inputs',None)
    cython_schedule=getattr(args,'cython_schedule',None)
    cython_schedule_inputs=getattr(args,'cython_schedule_inputs',None)
    if bool(cython_schedule)!=bool(cython_schedule_inputs):raise ValueError('Cython schedule requires explicit prepared inputs')
    text_editing_schedule=getattr(args,'text_editing_schedule',None)
    text_editing_schedule_inputs=getattr(args,'text_editing_schedule_inputs',None)
    if bool(text_editing_schedule)!=bool(text_editing_schedule_inputs):raise ValueError('Text editing schedule requires explicit prepared inputs')
    matplotlib_schedule=getattr(args,'matplotlib_schedule',None)
    matplotlib_schedule_inputs=getattr(args,'matplotlib_schedule_inputs',None)
    if bool(matplotlib_schedule)!=bool(matplotlib_schedule_inputs):raise ValueError('Matplotlib schedule requires explicit prepared inputs')
    if bool(c4_schedule)!=bool(c4_schedule_inputs):raise ValueError('C4 schedule requires explicit prepared inputs')
    if sum(bool(s) for s in (c4_schedule,django_schedule,matplotlib_schedule,text_editing_schedule,cython_schedule))>1:raise ValueError('Only one external schedule task at a time')
    if getattr(args,'experiment_memory_standard',False) and not any((c4_schedule,django_schedule,matplotlib_schedule,text_editing_schedule,cython_schedule)):
        raise ValueError('experiment memory acceptance requires an explicit external schedule')
    if bool(django_schedule)!=bool(django_schedule_inputs):raise ValueError('Django schedule requires explicit prepared inputs')
    if bool(external_config)!=bool(external_upstream):raise ValueError('external workload requires config and pinned upstream')
    external_sampling=getattr(args,'external_workload_sampling','on')
    if external_sampling not in ('on','off') or external_sampling=='off' and not external_config:raise ValueError('external sampling off requires an explicit supported config')
    external_off=bool(external_config) and external_sampling=='off'
    task_endpoint_metrics=getattr(args,'task_endpoint_metrics',False)
    external_oci_input=getattr(args,'external_workload_oci_input',None)
    external_oci_layout=None
    if external_oci_input and not external_config:raise ValueError('external OCI requires a fixed external workload')
    if c4_schedule and not (external_config and external_oci_input and not any((external_off,task_endpoint_metrics,density))):
        raise ValueError('C4 schedule requires its complete sampled config and local OCI, without other modes')
    if matplotlib_schedule and not (external_config and external_oci_input and not any((external_off,task_endpoint_metrics,density))):
        raise ValueError('Matplotlib schedule requires its complete sampled config and local OCI, without other modes')
    if text_editing_schedule and not (external_config and external_oci_input and not any((external_off,task_endpoint_metrics,density))):
        raise ValueError('Text editing schedule requires its complete sampled config and local OCI, without other modes')
    if cython_schedule and not (external_config and external_oci_input and not any((external_off,task_endpoint_metrics,density))):
        raise ValueError('Cython schedule requires its complete sampled config and local OCI, without other modes')
    creation_only=getattr(args,'external_creation_controls',False)
    vmm_pss_interval=validate_pss_interval(getattr(args,'vmm_pss_interval_seconds',1))
    if vmm_pss_interval!=1 and not (external_config and not external_off and not creation_only and not density):
        raise ValueError('non-default PSS interval requires a complete sampled external task')
    django_oci_input=getattr(args,'external_creation_oci_input',None)
    django_oci_state=django_oci_layout=None
    if django_oci_input and not (creation_only or django_schedule):raise ValueError('local Django OCI requires explicit creation controls or Django schedule')
    if django_schedule and not (external_config and django_oci_input and not any((external_off,creation_only,task_endpoint_metrics,external_oci_input))):
        raise ValueError('Django schedule requires its complete sampled external config and local Django OCI')
    if creation_only and not external_off:raise ValueError('creation controls require an explicit external config and periodic sampling off outside each creation')
    startup_sampling=getattr(args,'startup_sampling',None)
    if getattr(args,'startup_server_metrics',False) and not startup_sampling:
        raise ValueError('startup service metrics require the explicit startup sampling experiment')
    if startup_sampling:
        from cold_start_sampling import options as startup_options
        startup_sampling=startup_options(args)
    if getattr(args,'platform_config',None):
        from use_platform import configuration
        platform_cfg=configuration(args.platform_config)
        if platform_cfg['storage']!='isolated' or platform_cfg['backends']!=['agentenv']:
            raise ValueError('AgentENV validator requires isolated single-backend config')
    exp1_light=False
    if getattr(args,'exp1_light',False):
        from exp1_light import mode as exp1_light_mode
        exp1_light=exp1_light_mode(args,platform_cfg)
    prettier6604=getattr(args,'prettier6604_task',None)
    online_task=getattr(args,'online_task_config',None)
    online_source=getattr(args,'online_replay_source',None)
    online_points=getattr(args,'online_physical_after',None)
    if online_task:
        from online_task import load as load_online_task
        task_value=load_online_task(online_task,prettier6604)
        assert task_value['profile']=='prettier6604' and prettier6604 and getattr(args,'immutable_cache_seed',None)
    if (online_source or online_points) and not online_task:raise ValueError('frozen online replay requires task config')

    exp2_mode=getattr(args,'exp2_cache_mode',None)
    pair_cache_probe=getattr(args,'snapshot_cache_pair',False)
    capacity_probe=getattr(args,'guest_cache_capacity_probe',False)
    if capacity_probe and any(getattr(args,k,False) for k in ('snapshot_cache_pair','snapshot_cache_calibration','snapshot_lower_cache_calibration','guest_cache_metadata_probe')):
        raise ValueError('guest cache capacity probe is a separate short workflow')
    prettier_cache_pair=getattr(args,'prettier6604_snapshot_cache_pair',False)
    pair_sampling=getattr(args,'prettier6604_snapshot_cache_sampling',None)
    if pair_sampling is not None and not prettier_cache_pair:
        raise ValueError('pair cache sampling control requires the actual-task pair')
    guest_cache_policy=getattr(args,'prettier6604_guest_cache_policy',None)
    if guest_cache_policy is not None and (not prettier_cache_pair or pair_sampling=='off'):
        raise ValueError('guest cache policy requires the sampled actual-task pair')
    cache_target=getattr(args,'prettier6604_cache_target_mib',None)
    if cache_target is not None and (not prettier_cache_pair or pair_sampling=='off' or guest_cache_policy is not None):
        raise ValueError('continuous cache target requires the sampled actual-task pair without initial drop policy')
    capacity_sequence=getattr(args,'prettier6604_capacity_sequence',False)
    if capacity_sequence and (not prettier_cache_pair or cache_target!=64 or pair_sampling=='off' or guest_cache_policy is not None):
        raise ValueError('capacity sequence requires the sampled 64 MiB actual pair')
    capacity_recovery=getattr(args,'prettier6604_capacity_recovery',None)
    if capacity_recovery and (capacity_sequence or not prettier_cache_pair or cache_target!=128 or pair_sampling=='off' or guest_cache_policy is not None):
        raise ValueError('capacity recovery requires only the sampled 128 MiB actual pair')
    damon_before_checkpoint=getattr(args,'prettier6604_damon_before_checkpoint',False)
    if damon_before_checkpoint and not (capacity_sequence or capacity_recovery):
        raise ValueError('early DAMON stop requires the capacity checkpoint comparison')
    if capacity_recovery:
        capacity_recovery=capacity_recovery.resolve()
        assert capacity_recovery.parent==ROOT/'.artifacts/storage-isolated-service'
        old=json.loads((capacity_recovery/'result.json').read_text())
        assert old['status']=='FAIL' and old['failed_stage']=='prettier6604-reused-capacity-128'
        assert old['prettier6604_result']['status']=='PASS' and old['prettier6604_result']['cache_target_mib']==64
    provider_binary=getattr(args,'snapshot_provider_binary',None)
    provider_get_data=getattr(args,'snapshot_provider_get_data',False)
    batch_lookup=getattr(args,'snapshot_bpf_batch',False)
    if batch_lookup and not (provider_binary and provider_get_data and not pair_cache_probe):
        raise ValueError('BPF batch requires the private provider GET_DATA restore calibration')
    if provider_get_data and not provider_binary:
        raise ValueError('provider GET_DATA requires an owned diagnostic binary')
    if provider_binary:
        assert not pair_cache_probe, 'provider observation uses one short restore workflow'
        provider_binary=provider_binary.resolve()
        assert provider_binary.is_relative_to(ROOT/'.artifacts') and provider_binary.is_file()
        assert provider_binary.open('rb').read(4)==b'\x7fELF'
    lower_cache_probe=getattr(args,'snapshot_lower_cache_calibration',False) or pair_cache_probe or bool(provider_binary) and not prettier_cache_pair and not getattr(args,'guest_cache_rollout_sessions',False)
    cache_restore_probe=getattr(args,'snapshot_cache_calibration',False) or lower_cache_probe
    child_cache_probe=getattr(args,'guest_cache_child_calibration',False)
    child_cache_count=getattr(args,'guest_cache_child_count',1)
    dynamic_file_cache=getattr(args,'guest_cache_dynamic_file',False)
    ram_page_evidence=getattr(args,'guest_cache_ram_page_evidence',False)
    vmm_page_evidence=getattr(args,'guest_cache_vmm_page_evidence',False)
    io_uring_page_evidence=getattr(args,'guest_cache_io_uring_page_evidence',False)
    if io_uring_page_evidence and not vmm_page_evidence:
        raise ValueError('io_uring evidence requires explicit VMM and RAM evidence')
    if vmm_page_evidence and not (child_cache_probe and ram_page_evidence):
        raise ValueError('VMM mapping evidence requires private child calibration and explicit RAM evidence')
    host_backing_evidence=getattr(args,'guest_cache_host_backing_evidence',False)
    host_backing_pfn_evidence=getattr(args,'guest_cache_host_backing_pfn_evidence',False)
    host_backing_full_pfn_evidence=getattr(args,'guest_cache_host_backing_full_pfn_evidence',False)
    host_backing_charge_evidence=getattr(args,'guest_cache_host_backing_charge_evidence',False)
    if host_backing_charge_evidence and not host_backing_full_pfn_evidence:
        raise ValueError('page charge evidence requires explicit full-list PFNs')
    cache_sessions=getattr(args,'guest_cache_rollout_sessions',False)
    seven_restored=getattr(args,'seven_restored',False)
    restored_lower=getattr(args,'guest_cache_restored_lower',False)
    unified_cache_rounds=getattr(args,'guest_cache_unified_rounds',False)
    paused_native=getattr(args,'paused_native',False)
    if paused_native and not (cache_sessions and restored_lower and provider_binary and child_cache_count==2 and not unified_cache_rounds):
        raise ValueError('paused observation requires one source and one restore with the private snapshot provider')
    if cache_sessions and provider_binary and not (restored_lower and provider_get_data):raise ValueError('cache sessions provider requires restored lower and GET_DATA')
    if restored_lower and not cache_sessions:raise ValueError('restored lower requires cache sessions')
    if unified_cache_rounds and not cache_sessions:raise ValueError('unified rounds requires cache sessions')
    immediate=getattr(args,"immediate_restores",False)
    if immediate and not seven_restored:raise ValueError("immediate restores requires seven restored branches")
    if seven_restored and not (cache_sessions and restored_lower and provider_binary and provider_get_data and unified_cache_rounds and child_cache_count==7):
        raise ValueError('seven-restored requires explicit restored provider sessions')
    if cache_sessions and not (child_cache_probe and child_cache_count==(7 if seven_restored else 2) and diagnostic_server and os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK')):raise ValueError('cache sessions requires two owned guests and private server')
    light_memory=getattr(args,'guest_cache_light_memory',False)
    light_restore=getattr(args,'guest_cache_light_restore_one',False)
    if cache_sessions and any((light_memory,light_restore,dynamic_file_cache,ram_page_evidence,vmm_page_evidence,io_uring_page_evidence,host_backing_evidence,host_backing_pfn_evidence,host_backing_full_pfn_evidence,host_backing_charge_evidence)):raise ValueError('cache sessions cannot combine other probes')
    if light_restore and not (light_memory and child_cache_count==7):raise ValueError('restore-one requires seven light guests')
    if light_memory and (not child_cache_probe or child_cache_count not in (2,7) or any((dynamic_file_cache,ram_page_evidence,vmm_page_evidence,io_uring_page_evidence,host_backing_evidence))):
        raise ValueError('light memory requires two or seven private guests and no heavy evidence')
    retain_fixture=getattr(args,'retain_fixture_for_final_acceptance',False)
    if retain_fixture and not host_backing_full_pfn_evidence:
        raise ValueError('fixture retention for final acceptance requires explicit private full-list calibration')
    if host_backing_full_pfn_evidence and not (child_cache_probe and host_backing_evidence and child_cache_count==1 and not host_backing_pfn_evidence):
        raise ValueError('full-list PFNs require one private guest, backing inventory, and no single-range option')
    if host_backing_pfn_evidence and not (child_cache_probe and host_backing_evidence and child_cache_count==1):
        raise ValueError('host backing PFNs require explicit backing evidence and one private guest')
    if host_backing_evidence and not child_cache_probe:
        raise ValueError('host backing evidence requires explicit private child calibration')
    if ram_page_evidence and not child_cache_probe:
        raise ValueError('full RAM evidence requires explicit private child calibration')
    if dynamic_file_cache and not (child_cache_probe and child_cache_count in (1,2)):
        raise ValueError('dynamic file cache requires explicit private child calibration')
    if child_cache_count not in (1,2,7) or child_cache_count!=1 and not child_cache_probe or child_cache_count==7 and not (light_memory or seven_restored):
        raise ValueError('two private cache guests require the explicit child calibration')
    if child_cache_probe and any((getattr(args,'guest_cache_metadata_probe',False),cache_restore_probe,capacity_probe)):
        raise ValueError('child cache calibration is a separate private-guest workflow')
    cache_metadata_probe=getattr(args,'guest_cache_metadata_probe',False) or cache_restore_probe or capacity_probe or child_cache_probe
    diagnostic_kernel=getattr(args,'diagnostic_guest_kernel',None)
    live_task_cache=getattr(args,'prettier6604_live_cache',False)
    if cache_metadata_probe and not diagnostic_kernel:
        raise ValueError('guest cache metadata probe requires an explicit diagnostic kernel')
    if diagnostic_kernel:
        if getattr(args,'light_rollout',False):
            from light_prettier_rollout import diagnostic_kernel_args
            diagnostic_kernel_args(diagnostic_kernel)
        diagnostic_kernel=diagnostic_kernel.resolve()
        assert diagnostic_kernel.is_relative_to(ROOT/'.artifacts') and diagnostic_kernel.is_file()
        assert diagnostic_kernel.open('rb').read(4)==b'\x7fELF', 'diagnostic kernel must be a local ELF'
    prettier_checkpoint=getattr(args,'prettier6604_checkpoint',False)
    expected_audit=getattr(args,'expected_official_audit',None)
    prettier_export=getattr(args,'prettier6604_export',None)
    prettier_role=getattr(args,'prettier6604_role',None)
    prettier_schedule=getattr(args,'prettier6604_schedule','grpo')
    prettier_readonly=getattr(args,'prettier6604_readonly_replay',False)
    prettier_monitor_mode=getattr(args,'prettier6604_monitor_mode','continuous')
    create_before_setup=getattr(args,'prettier6604_grpo_create_before_setup',False)
    if create_before_setup:
        from run_prettier6604_grpo import create_before_setup_options
        create_before_setup_options(args)
    if create_before_setup and not (prettier6604 and prettier_export):
        raise ValueError('create-before-setup requires the corrected Prettier GRPO export')
    skip_boundary_vmstat=getattr(args,'light_skip_boundary_vmstat',False)
    if skip_boundary_vmstat:
        from run_prettier6604_grpo import skip_boundary_vmstat_options
        skip_boundary_vmstat_options(args)
    merge_boundary_meminfo=getattr(args,'light_merge_boundary_meminfo',False)
    if merge_boundary_meminfo:
        from run_prettier6604_grpo import merge_boundary_meminfo_options
        merge_boundary_meminfo_options(args)
    prettier_physical=getattr(args,'prettier6604_physical_cache_after','')
    host_matched=getattr(args,'host_matched_task',False)
    from host_scaling import options as scaling_options, arguments as scaling_arguments
    scaling=scaling_options(args)
    current_pair=getattr(args,'host_current_pair',False)
    if current_pair:
        from host_current_pair import enabled
        enabled(args)
    from host_independent_task import phase_options, phase_arguments, checked_prefix
    independent_phase=phase_options(args)
    rootfs_short=getattr(args,'host_rootfs_short',False)
    if rootfs_short and not (independent_phase=='dual' and host_matched and not getattr(args,'host_trace_dual',False)):
        raise ValueError('rootfs short requires one imported dual matched restore')
    host_bundle=getattr(args,'host_checkpoint_bundle',None)
    if host_bundle:checked_prefix(host_bundle,args.host_checkpoint_source)
    host_trace_dual=getattr(args,'host_trace_dual',False)
    if host_trace_dual and not host_matched:raise ValueError('trace dual requires explicit matched-task mode')
    if host_matched and getattr(args,'host_restore_short',False):raise ValueError('host restore modes are mutually exclusive')
    host_restore=getattr(args,'host_restore_short',False) or host_matched
    host_sampling=os.environ.get('ASB_HOST_FILE_SAMPLING','on')
    if external_config and (host_sampling!=external_sampling or bool(getattr(args,'light_monitor',False))!=(external_sampling=='on')):
        raise ValueError('external host and guest sampling modes differ')
    current_monitor_off=(getattr(args,'host_current_only',False) and getattr(args,'host_file_monitor',False) and
        not getattr(args,'light_monitor',False) and ordinary_current_only(args,monitor_mode='off'))
    if current_monitor_off and host_sampling!='off':
        raise ValueError('current-only monitor-off requires host sampling off from the outer entry')
    if host_sampling not in ('on','off') or host_sampling=='off' and not external_off and not current_monitor_off and not scaling and (not host_matched or host_trace_dual or independent_phase):
        raise ValueError('host sampling off requires matched-task, scaling or explicit current-only monitor-off')
    if host_restore and not (getattr(args,'host_file_monitor',False) and prettier_export and prettier_schedule=='bpo' and
                             not (diagnostic_kernel or provider_binary or prettier_checkpoint or prettier_readonly or prettier_role or live_task_cache or cache_metadata_probe or prettier_cache_pair)):
        raise ValueError('host restore short requires ordinary-kernel host monitoring and explicit BPO export only')
    prefix_count=getattr(args,'host_prefix_count',None)
    from host_private_files import member as private_member
    private_control=private_member()
    from host_full_rootfs_control import validate_mode as validate_full_rootfs_mode
    full_rootfs_mode=validate_full_rootfs_mode(args)
    full_rootfs_residency=getattr(args,'host_full_rootfs_residency','on')
    full_rootfs_cold_read=getattr(args,'host_full_rootfs_cold_read',False)
    full_rootfs_daemon=getattr(args,'host_full_rootfs_daemon',None)
    if full_rootfs_mode:
        if not (getattr(args,'host_file_monitor',False) and getattr(args,'light_monitor',False) and prettier_export and full_rootfs_daemon and not (private_control or diagnostic_kernel or provider_binary or live_task_cache or cache_metadata_probe or prettier_cache_pair)):
            raise ValueError('full rootfs copies require light host GRPO/BPO and an explicit experimental daemon')
        with full_rootfs_daemon.open('rb') as stream:
            if stream.read(4)!=b'\x7fELF':raise ValueError('rootfs copy daemon must be ELF')
    elif full_rootfs_daemon:raise ValueError('rootfs copy daemon requires the explicit full mode')
    if private_control and not (prefix_count==1 and scaling is None and not (host_restore or independent_phase or getattr(args,'damon_disable',False) or getattr(args,'damon_observe',False))):
        raise ValueError('private-file control requires one ordinary light cold prefix')
    full_shared=getattr(args,'host_shared_files',False)
    if full_shared and not (getattr(args,'host_file_monitor',False) and getattr(args,'light_monitor',False) and prettier_export and prettier_schedule in ('grpo','bpo') and not (scaling or prefix_count or host_restore or independent_phase or prettier_role or diagnostic_kernel or provider_binary or prettier_checkpoint or prettier_readonly or live_task_cache or cache_metadata_probe or prettier_cache_pair or getattr(args,'prettier6604_shared_cache',False) or getattr(args,'damon_observe',False) or getattr(args,'damon_disable',False))):
        raise ValueError('host_shared_files requires ordinary full light GRPO/BPO only')
    if scaling and not (getattr(args,'host_file_monitor',False) and getattr(args,'light_monitor',False) and prettier_export and prettier_schedule=='grpo' and not (prettier_role or diagnostic_kernel or provider_binary or prettier_checkpoint or prettier_readonly or live_task_cache or cache_metadata_probe or prettier_cache_pair or getattr(args,'prettier6604_shared_cache',False))):
        raise ValueError('host scaling requires ordinary-kernel light replay without other analyses')
    if prefix_count and not (getattr(args,'host_file_monitor',False) and getattr(args,'light_monitor',False) and prettier_export and prettier_schedule=='grpo' and not (prettier_role or diagnostic_kernel or prettier_checkpoint or prettier_readonly or live_task_cache or host_restore or getattr(args,'prettier6604_shared_cache',False))):
        raise ValueError('prefix comparison requires light host GRPO')
    host_pair_smoke=getattr(args,'host_file_pair_smoke',False)
    if host_pair_smoke and not (getattr(args,'host_file_monitor',False) and prettier6604 and prettier_readonly and
                               not (prettier_export or prettier_checkpoint or diagnostic_kernel or online_task or prettier_physical)):
        raise ValueError('host file short pair requires a fresh ordinary Prettier-image host workflow without task variants')
    if live_task_cache and not (diagnostic_kernel and prettier6604 and (prettier_readonly or prettier_checkpoint) and
                               not (prettier_export or prettier_physical or cache_metadata_probe) and prettier_monitor_mode=='continuous'):
        raise ValueError('live task cache requires diagnostic kernel and continuous Prettier replay or step-24 restore')
    live_task_cache_lower=getattr(args,'prettier6604_live_cache_lower',False)
    if live_task_cache_lower and not (live_task_cache and prettier_checkpoint):
        raise ValueError('raw memory-layer collection requires live Prettier step-24 restore')
    prettier_cache_pair=getattr(args,'prettier6604_snapshot_cache_pair',False)
    if prettier_cache_pair and not (diagnostic_kernel and prettier_export and prettier_schedule=='bpo' and
                                   not (prettier_role or prettier_checkpoint or live_task_cache or cache_metadata_probe)):
        raise ValueError('actual-task pair requires diagnostic kernel, BPO export and one snapshot')
    prettier_shared=getattr(args,'prettier6604_shared_cache',False)
    prettier_root_count=getattr(args,'prettier6604_root_count',None)
    if prettier_shared and not (prettier_export and prettier_schedule=='grpo' and not prettier_role and not prettier_checkpoint):
        raise ValueError('shared-cache measurement requires independent Prettier GRPO roots')
    if prettier_root_count is not None and not prettier_shared:
        raise ValueError('root-count requires shared-cache analysis')
    if prettier_physical and not (prettier6604 and prettier_readonly and not prettier_checkpoint and not prettier_export and prettier_monitor_mode=='continuous'):
        raise ValueError('physical boundaries require one continuous readonly Prettier replay')
    if prettier_monitor_mode=='off' and not prettier_readonly:
        if not (prettier_export and prettier_schedule=='grpo' and not (prettier_role or prettier_checkpoint or prettier_shared or prettier_root_count or
            getattr(args,'light_monitor',False) or getattr(args,'host_file_monitor',False) and not current_monitor_off or full_rootfs_mode or full_shared or
            scaling or prefix_count or host_restore or independent_phase or prettier_cache_pair or diagnostic_kernel or prettier_physical)):
            raise ValueError('Prettier monitor-off requires complete readonly backbone or ordinary seven-guest GRPO')
    cache_seed=getattr(args,'immutable_cache_seed',None)
    schedule_smoke=False
    if getattr(args,'schedule_smoke_metrics',False) or getattr(args,'schedule_expanded_files',False) or getattr(args,'schedule_cache_layers',False) or getattr(args,'schedule_block_cache_method',None) is not None or getattr(args,'schedule_split_ram_cache',False) or getattr(args,'schedule_memory_backing_links',False):
        from schedule_smoke_metrics import options as smoke_options
        schedule_smoke=smoke_options(args,native=True)
    from light_prettier_rollout import options as light_rollout_options
    light_rollout=light_rollout_options(args,native=True)
    vmm_check=os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK')
    if light_rollout and not (vmm_check and diagnostic_server):raise ValueError('light rollout requires owned VMM groups and patched private server')
    vmm_sampler=None
    if vmm_check:
        vmm_check=Path(vmm_check).resolve()
        assert (schedule_smoke and getattr(args,'prettier6604_schedule','grpo')=='grpo') or child_cache_probe or light_rollout, 'VMM cgroups require full GRPO smoke or explicit private-guest cache calibration'
        assert vmm_check.is_relative_to(ROOT/'.artifacts') and (vmm_check/'vmm-cgroup-launcher').is_file()
    if child_cache_probe:
        assert vmm_check and cache_seed and os.environ.get('ASB_VMM_REQUIRE_IO')=='on', 'child calibration requires owned launcher, local seed and I/O accounting'
        assert not os.environ.get('ASB_VMM_ACCOUNTING_ONLY'), 'seven-guest accounting acceptance is not a private-guest calibration check'
    persistent_seed=getattr(args,'persistent_cache_seed',None)
    local_no_proxy=os.environ.get('ASB_LOCAL_NO_PROXY')=='on'
    local_no_proxy_check=getattr(args,'local_no_proxy_check',False)
    if local_no_proxy_check and not local_no_proxy:raise ValueError('short no-proxy check requires local-no-proxy')
    if local_no_proxy_check and not prettier6604:raise ValueError('short no-proxy check remains Prettier-only')
    local_sphinx=(getattr(args,'python_task_profile',None)=='sphinx7590' and bool(getattr(args,'python_backbone_inputs',None)) and
        getattr(args,'python_sphinx_pipeline_diagnostic',False) and getattr(args,'python_sphinx_pipeline_policy_version',1)==2)
    local_xarray=(getattr(args,'python_task_profile',None)=='xarray6992' and bool(getattr(args,'python_backbone_inputs',None)) and
        getattr(args,'python_diagnostic_continuation',False) and not getattr(args,'python_sphinx_pipeline_diagnostic',False))
    local_task_id='pydata__xarray-6992' if local_xarray else 'sphinx-doc__sphinx-7590' if local_sphinx else 'prettier__prettier-6604'
    if local_no_proxy and not (persistent_seed and (prettier6604 or local_sphinx or local_xarray) and getattr(args,'host_current_only',False) and
            (getattr(args,'light_monitor',False) or current_monitor_off and not local_no_proxy_check)):
        raise ValueError('local-no-proxy requires persistent light current-only input or full sampling-off control')
    if persistent_seed:
        from host_persistent_input import native_option
        native_option(args)
        cache_seed=persistent_seed
    python_inputs=getattr(args,'python_backbone_inputs',None)
    python_schedule=getattr(args,'python_schedule',None)
    python_profile=getattr(args,'python_task_profile','xarray6992')
    python_branch_policy=getattr(args,'python_branch_pipeline_policy','original')
    python_schedule_find=getattr(args,'python_schedule_find_exit_diagnostic',False)
    python_schedule_xarray_find=getattr(args,'python_schedule_xarray_find_exit_diagnostic',False)
    if python_schedule_xarray_find and not (python_schedule and python_profile=='xarray6992'):
        raise ValueError('Xarray find exit policy requires explicit Xarray seven-role schedule')
    if python_schedule_find and not (python_schedule and python_profile=='sphinx7590'):
        raise ValueError('find exit policy requires explicit Sphinx seven-role schedule')
    if python_branch_policy!='original' and not (python_schedule and python_profile=='sphinx7590'):
        raise ValueError('early2 policy requires explicit Sphinx seven-role schedule')
    sphinx=python_profile=='sphinx7590'
    if sphinx and not python_inputs:raise ValueError('Sphinx profile requires prepared Python task inputs')
    python_oci=getattr(args,'python_backbone_oci',None)
    python_capacity=getattr(args,'python_backbone_capacity_gib',32)
    python_diagnostic=getattr(args,'python_diagnostic_only',False)
    python_readonly_replay=getattr(args,'python_readonly_replay',False)
    python_persistent=getattr(args,'python_persistent_sampling',False)
    python_monitor_mode=getattr(args,'python_monitor_mode','continuous')
    light_monitor=getattr(args,'light_monitor',False)
    damon_observe=getattr(args,'damon_observe',False)
    damon_disable=getattr(args,'damon_disable',False)
    if damon_disable and not damon_observe:
        raise ValueError('DAMON disable requires counter observation in a complete light Prettier task')
    if damon_observe and not (light_monitor and prettier6604 and prettier_readonly and prettier_monitor_mode=='continuous' and not (prettier_checkpoint or prettier_export or prettier_physical or live_task_cache)):
        raise ValueError('DAMON observation requires a complete light Prettier readonly replay')
    python_physical=getattr(args,'python_physical_cache_after','')
    python_live_cache=getattr(args,'python_live_cache',False)
    python_cache_baseline=getattr(args,'python_cache_baseline',False)
    if python_cache_baseline and not python_live_cache:raise ValueError('Python cache baseline requires live Python cache replay')
    python_cache_target=getattr(args,'python_cache_target_mib',None)
    if python_cache_target is not None and (type(python_cache_target) is not int or python_cache_target<=0 or python_cache_baseline or not python_live_cache):
        raise ValueError('Python cache target requires positive MiB and live Python replay without baseline mode')
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
    tool_source=getattr(args,'python_tool_replay_source',None)
    host_current_only=getattr(args,'host_current_only',False)
    if tool_source and not (python_tool_capture and getattr(args,'python_tool_task_config',None)):
        raise ValueError('tool replay source requires explicit Python tool task config')
    if host_current_only and not (current_monitor_off or external_off and getattr(args,'host_file_monitor',False) or (python_tool_capture and tool_source or exp1_light or external_config or ordinary_current_only(args)) and light_monitor and getattr(args,'host_file_monitor',False)):
        raise ValueError('host-current-only requires a complete ordinary light/monitor-off task, or light frozen tool replay/Exp1')
    online_agent=getattr(args,'python_online_agent_config',None)
    if online_agent and not (python_tool_capture or online_task):raise ValueError('online agent requires tool capture mode')
    python_durable=getattr(args,'python_durable_checkpoint_demo',False)
    durable_bundle=getattr(args,'python_durable_checkpoint_bundle',None)
    if durable_bundle and not python_durable:raise ValueError('checkpoint bundle requires durable checkpoint demo')
    if python_find_diagnostic and not (python_runtime_smoke or python_command_diagnostic):
        raise ValueError('find diagnostic requires runtime smoke or command diagnostic')
    env_command_timing=getattr(args,'python_env_command_timing',None)
    if env_command_timing and not (python_command_diagnostic and not python_find_diagnostic and
            not (getattr(args,'light_monitor',False) or getattr(args,'host_file_monitor',False))):
        raise ValueError('environment timing requires separate command diagnostic without find or memory sampling')
    short_args=['--find-diagnostic'] if python_find_diagnostic else []
    if env_command_timing:short_args+=['--env-command-timing',env_command_timing]
    if python_durable:short_args=['--phase','restore','--bundle',str(durable_bundle.resolve())] if durable_bundle else ['--phase','capture']
    if getattr(args,'python_tool_task_config',None):short_args+=['--task-config',str(args.python_tool_task_config.resolve())]
    if tool_source:short_args+=['--replay-source',str(tool_source.resolve())]
    if online_agent:
        from model_tool_agent import configuration
        configuration(online_agent)
        short_args+=['--agent-config',str(online_agent.resolve())]
    if sum(map(bool,(python_runtime_smoke,python_cache_smoke,python_command_diagnostic,python_tool_capture,python_durable)))>1:
        raise ValueError('choose one short Python validation mode')
    python_short=python_cache_smoke or python_runtime_smoke or python_command_diagnostic or python_tool_capture or python_durable
    if python_schedule and not (python_inputs and cache_seed and python_readonly_replay and python_persistent and
            python_monitor_mode=='continuous' and not any((python_short,python_checkpoint,python_expected,
            python_diagnostic,python_evaluation,python_physical,python_live_cache,light_monitor,task_endpoint_metrics,
            persistent_seed,local_no_proxy,schedule_smoke)) and
            (sphinx and sphinx_pipeline and pipeline_version==2 or not sphinx and python_continuation)):
        raise ValueError('Python schedule requires explicit original seven-role full monitoring and pinned diagnostic policy')
    if python_live_cache and not (diagnostic_kernel and python_inputs and python_readonly_replay and python_persistent and
                                 python_monitor_mode=='continuous' and not (python_checkpoint or python_short or python_diagnostic or python_evaluation or python_physical or cache_metadata_probe or live_task_cache)):
        raise ValueError('live Python cache requires diagnostic kernel and one complete continuous readonly replay')
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
    if python_monitor_mode=='off' and not (python_inputs and python_readonly_replay and python_persistent and cache_seed and not (python_short or python_checkpoint or python_diagnostic or python_evaluation or python_physical or python_live_cache)):
        raise ValueError('monitor-off control requires a complete readonly Python replay without checkpoint or physical analysis')
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
    if exp2_mode and (not cache_seed or prettier6604 or python_inputs or args.exp1_checkpoint or platform_cfg is not None):
        raise ValueError('Exp2 requires verified Prettier cache and no other workload')
    if cache_seed and not (exp1_light or online_task or exp2_mode or prettier_export or prettier_readonly or prettier_checkpoint or python_diagnostic or python_readonly_replay or python_evaluation or child_cache_probe):
        raise ValueError('extra cache seed is limited to corrected Prettier paths')
    seed_value=None
    if persistent_seed:
        from host_persistent_input import validate_seed
        expected_task='sphinx-doc__sphinx-7590' if sphinx else 'pydata__xarray-6992' if python_inputs else 'prettier__prettier-6604'
        cache_seed=cache_seed.resolve();seed_value,seed_root=validate_seed(cache_seed,task_id=expected_task)
    elif cache_seed:
        cache_seed=cache_seed.resolve()
        assert cache_seed.is_relative_to(ROOT/'.artifacts'/('sphinx-backbone' if sphinx else 'python-backbone' if python_inputs else 'prettier6604'))
        seed_value=json.loads((cache_seed/'seed.json').read_text())
        assert seed_value['status']=='PASS' and seed_value['server_image']==IMAGE
        assert seed_value['task_id']==('sphinx-doc__sphinx-7590' if sphinx else 'pydata__xarray-6992' if python_inputs else 'prettier__prettier-6604')
        if exp1_light:
            assert 'sha256-e625c9b9776870e2cc87172e886bbc90cfc3d6ce1521492f113a46b3f6dfcf44-image.json' in seed_value['parts']['image-cache/configs'], 'verified seed does not contain the Exp1 image'
        if sphinx:
            from sphinx7590_inputs import IMAGE as task_image
            assert seed_value['task_image']==task_image
        seed_root=Path(seed_value['source_env']).resolve()
        assert seed_root==cache_seed/'readonly/env'
        mount_identity(cache_seed/'readonly',seed_value['loop_device'])
        assert (Path('/sys/class/block')/Path(seed_value['loop_device']).name/'ro').read_text().strip()=='1'
    if prettier_checkpoint and not (prettier6604 and expected_audit):
        raise ValueError('Prettier checkpoint requires pinned task and verified official reference')
    capacity=prettier_capacity(args,prettier_export,prettier_role,prettier_schedule,prettier_checkpoint,cache_seed or capacity_recovery)
    if density:
        if density['task']=='prettier6604' and (not (prettier6604 and prettier_readonly and persistent_seed and local_no_proxy and light_monitor and host_current_only
                and getattr(args,'host_file_monitor',False)) or any((prettier_export,external_config,full_rootfs_mode,scaling,
                private_control,diagnostic_kernel,provider_binary,damon_observe,damon_disable,prettier_checkpoint))):
            raise ValueError('density requires the explicit local light current-only complete Prettier backbone')
        capacity=80*GIB
        if density['task']=='prettier6604':
            from prettier_no_proxy import metadata as density_local_metadata
            density_local_metadata(persistent_seed/'local-no-proxy-metadata')
        else:
            if not (external_config and light_monitor and host_current_only and getattr(args,'host_file_monitor',False)) or any((cache_seed,prettier6604,full_rootfs_mode,scaling,private_control,diagnostic_kernel,provider_binary,damon_observe,damon_disable)):
                raise ValueError('C4 density requires its separate full light current-only external task')
            from c4_density_input import validate as validate_c4_input
            c4_input_state,c4_layout,_=validate_c4_input(density['oci_input'])
    if external_config:
        if not (host_current_only and (light_monitor or external_off) and getattr(args,'host_file_monitor',False)) or any((cache_seed,prettier6604,python_inputs,online_task,exp1_light,exp2_mode,args.exp1_checkpoint,platform_cfg,host_pair_smoke,cache_metadata_probe,scaling,private_control,full_rootfs_mode)):
            raise ValueError('external workload requires a separate complete light current-only run')
        from external_workload import load as load_external,budgets as external_budgets
        external_value,_,_=load_external(external_config,external_upstream)
        if vmm_pss_interval!=1 and not (task_endpoint_metrics or external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing')):
            raise ValueError('non-default PSS interval requires external endpoint metrics')
        if external_oci_input:
            if c4_schedule:
                from c4_density_input import validate as validate_c4_oci
                external_oci_state,external_oci_layout,_=validate_c4_oci(external_oci_input)
            else:
                from external_oci import validate as validate_external_oci
                external_oci_state,external_oci_layout,_=validate_external_oci(external_oci_input,external_value)
        if external_off and external_value['task_id'] not in ('reshard-c4-data','django__django-16819','build-cython-ext'):raise ValueError('sampling off is limited to the explicit C4/Django/Cython controls')
        if creation_only and external_value['task_id']!='django__django-16819':raise ValueError('creation controls are limited to the fixed Django image')
        if django_oci_input:
            from django_oci import validate as validate_django_oci,IMAGE as django_image
            if external_value['image']!=django_image:raise ValueError('local OCI API image differs')
            django_oci_state,django_oci_layout=validate_django_oci(django_oci_input)
        if density:
            if external_value['task_id']!='reshard-c4-data' or density['task']!='c4':raise ValueError('density external task differs')
            capacity=80*GIB
        else:capacity=32*GIB
        if django_schedule:
            from django_schedule_inputs import task_plan as django_plan,volume_gib as django_volume_gib
            if external_value['task_id']!='django__django-16819':raise ValueError('Django schedule task differs')
            django_plan(django_schedule_inputs)
            capacity=django_volume_gib(django_schedule)*GIB
        if c4_schedule:
            from c4_schedule_inputs import task_plan as c4_plan,volume_gib as c4_volume_gib
            if external_value['task_id']!='reshard-c4-data':raise ValueError('C4 schedule task differs')
            c4_plan(c4_schedule_inputs)
            capacity=c4_volume_gib(c4_schedule)*GIB
        if matplotlib_schedule:
            from matplotlib_schedule_inputs import task_plan as matplotlib_plan,volume_gib as matplotlib_volume_gib,START_MARGIN_BYTES
            if external_value['task_id']!='matplotlib__matplotlib-24177':raise ValueError('Matplotlib schedule task differs')
            matplotlib_plan(matplotlib_schedule_inputs)
            capacity=matplotlib_volume_gib(matplotlib_schedule)*GIB
        if text_editing_schedule:
            from text_editing_schedule_inputs import task_plan as text_plan,volume_gib as text_volume_gib
            if external_value['task_id']!='large-scale-text-editing':raise ValueError('Text editing schedule task differs')
            text_plan(text_editing_schedule_inputs)
            capacity=text_volume_gib(text_editing_schedule)*GIB
        if cython_schedule:
            from cython_schedule_inputs import task_plan as cython_plan,volume_gib as cython_volume_gib
            if external_value['task_id']!='build-cython-ext':raise ValueError('Cython schedule task differs')
            cython_plan(cython_schedule_inputs)
            capacity=cython_volume_gib(cython_schedule)*GIB
    if getattr(args,'external_workload_relay',False) and not (external_config and external_value['task_id']=='build-cython-ext' and (task_endpoint_metrics or cython_schedule)):
        raise ValueError('explicit relay requires upgraded Cython')
    if getattr(args,'external_delete_trace',False) and not (external_config and getattr(args,'external_guest_count',1)==2 and getattr(args,'external_sampling_control',False)):raise ValueError('delete trace requires explicit two-guest C4 control')
    if getattr(args,'external_guest_count',1)!=1 and not (getattr(args,'external_guest_count',1)==2 and external_config and external_value['task_id']=='reshard-c4-data' and getattr(args,'external_sampling_control',False)):raise ValueError('two guests require explicit C4 sampling control')
    if getattr(args,'external_sampling_control',False) and not (external_config and external_value['task_id']=='reshard-c4-data' and not creation_only):raise ValueError('sampling control requires complete C4')
    if task_endpoint_metrics and not (external_config and not external_off and not creation_only or python_inputs and light_monitor and host_current_only and python_persistent and not (python_checkpoint or python_short or python_diagnostic or python_evaluation)):
        raise ValueError('endpoint metrics require a complete sampled external or light Python task')
    if cache_metadata_probe:
        if prettier6604 or python_inputs or exp2_mode or args.exp1_checkpoint or platform_cfg:
            raise ValueError('guest cache metadata probe is a separate short workflow')
        capacity=(64 if seven_restored else 24 if cache_sessions else 40 if light_restore else 24 if light_memory and child_cache_count==7 else 22 if pair_cache_probe else 24 if cache_restore_probe else 16 if child_cache_probe and child_cache_count==2 else 12)*GIB
    if exp2_mode:
        capacity=32*GIB
        subprocess.run([sys.executable,'-B',ROOT/'scripts/run_exp2.py','--cache-mode',exp2_mode,'--check-only'],check=True,capture_output=True,timeout=15)
    if python_inputs:
        capacity=python_capacity*GIB
        if python_diagnostic or python_evaluation:capacity=2*GIB
        if python_readonly_replay:capacity=8*GIB
        # AgentENV snapshots copy immutable managed layers as well as VM memory.
        # Use the already verified Sphinx restore volume, even for small smoke VMs.
        if python_runtime_smoke:capacity=16*GIB
        if python_checkpoint:capacity=16*GIB
        if python_schedule:capacity=(40 if python_schedule=='grpo' else 56 if sphinx else 64)*GIB
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
        if python_cache_baseline or python_cache_target is not None:
            check_command+=['--live-cache',*(['--cache-baseline'] if python_cache_baseline else ['--cache-target-mib',str(python_cache_target)]),*(['--persistent-sampling'] if '--persistent-sampling' not in check_command else [])]
        if python_monitor_mode=='off':
            check_command+=['--monitor-mode','off']
            if python_continuation:check_command+=['--diagnostic-continuation','--persistent-sampling']
        if light_monitor:check_command+=['--light-monitor']
        if python_schedule:
            check_command=[sys.executable,'-B',str(ROOT/'scripts/run_python_schedule.py'),'--inputs',str(python_inputs),
                '--task-profile',python_profile,'--schedule',python_schedule,'--check-only']
            if python_branch_policy!='original':check_command+=['--branch-pipeline-policy',python_branch_policy]
            if python_schedule_find:check_command+=['--find-exit-diagnostic']
            if python_schedule_xarray_find:check_command+=['--xarray-find-exit-diagnostic']
        subprocess.run(check_command,check=True,capture_output=True,timeout=30)
    if prettier6604 and (args.exp1_checkpoint or platform_cfg is not None):
        raise ValueError('Prettier6604 entry cannot be combined with Exp1 config')
    if prettier6604 and not online_task and not startup_sampling:
        check_args=['--checkpoint','--expected-official-audit',str(expected_audit)] if prettier_checkpoint else []
        if prettier_readonly:
            if not expected_audit:raise ValueError('Prettier readonly replay requires original task outcome reference')
            check_args=['--monitor-mode',prettier_monitor_mode,'--expected-official-audit',str(expected_audit)]
        if light_monitor:check_args+=['--light-monitor']
        if damon_observe:check_args+=['--damon-observe']
        if damon_disable:check_args+=['--damon-disable']
        runner='run_prettier6604.py'
        if prettier_export:
            runner='run_prettier6604_grpo.py'
            check_args=['--frozen-export',str(prettier_export),'--expected-official-audit',str(expected_audit),'--schedule',prettier_schedule]
            if prettier_monitor_mode=='off':check_args+=['--monitor-mode','off']
            if create_before_setup:check_args+=['--grpo-create-before-setup']
            if skip_boundary_vmstat:check_args+=['--light-skip-boundary-vmstat']
            if merge_boundary_meminfo:check_args+=['--light-merge-boundary-meminfo']
            if host_restore:check_args+=['--host-matched-task' if host_matched else '--host-restore-short']
            if host_trace_dual:check_args+=['--host-trace-dual']
            if independent_phase:check_args+=phase_arguments(independent_phase,host_bundle,getattr(args,'host_checkpoint_source',None))
            if rootfs_short:check_args+=['--host-rootfs-short']
            if prettier_role:check_args+=['--role',prettier_role]
            if prettier_shared:check_args+=['--shared-cache','--root-count',str(prettier_root_count or 7)]
            if light_monitor:check_args+=['--light-monitor']
            if full_shared:check_args+=['--host-shared-files']
            if schedule_smoke:check_args+=['--schedule-smoke-metrics']
            if getattr(args,'schedule_expanded_files',False):check_args+=['--schedule-expanded-files']
            if getattr(args,'schedule_cache_layers',False):check_args+=['--schedule-cache-layers']
            if getattr(args,'schedule_block_cache_method',None) is not None:check_args+=['--schedule-block-cache-method',args.schedule_block_cache_method]
            if getattr(args,'schedule_split_ram_cache',False):check_args+=['--schedule-split-ram-cache']
            if getattr(args,'schedule_memory_backing_links',False):check_args+=['--schedule-memory-backing-links']
            if getattr(args,'schedule_sampling_control',False):check_args+=['--schedule-sampling-control']
            if full_rootfs_mode:check_args+=['--host-full-rootfs-mode',full_rootfs_mode]
            if full_rootfs_residency=='off':check_args+=['--host-full-rootfs-residency','off']
            if full_rootfs_cold_read:check_args+=['--host-full-rootfs-cold-read']
            if prefix_count:check_args+=['--host-prefix-count',str(prefix_count)]
            if scaling:check_args+=scaling_arguments(scaling,host_bundle,getattr(args,'host_checkpoint_source',None))
            if current_pair:check_args+=['--host-current-pair']
            if persistent_seed and current_pair:check_args+=['--host-persistent-seed',str(cache_seed)]
        subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/runner),'--official-task',str(prettier6604),'--check-only',*check_args],check=True,capture_output=True,timeout=15)
    if online_task:
        online_args=['--task-config',str(online_task.resolve()),*(['--agent-config',str(online_agent.resolve())] if online_agent else []),*(['--replay-source',str(online_source.resolve())] if online_source else []),*(['--physical-after',*map(str,online_points)] if online_points else [])]
        subprocess.run([sys.executable,'-B',ROOT/'scripts/run_tool_capture.py','--inputs',prettier6604,'--check-only',*online_args],check=True,capture_output=True,timeout=30)
    exp1=args.exp1_checkpoint or platform_cfg is not None
    window=execution_window(args.window_start_utc,args.window_end_utc,automatic=getattr(args,'automatic_window',False) or not getattr(args,'confirmed_window',False))
    out=ROOT/'.artifacts/storage-isolated-service'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False)
    (out/'code').mkdir()
    if current_pair:shutil.copy2(ROOT/'scripts/host_current_pair.py',out/'code/host_current_pair.py')
    if persistent_seed:shutil.copy2(ROOT/'scripts/host_persistent_input.py',out/'code/host_persistent_input.py')
    if exp1_light:
        for name in ('exp1_light.py','guest_exp1_actions.py','exp1_physical_workload.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if capacity_sequence or capacity_recovery:
        shutil.copy2(ROOT/'scripts/capacity_checkpoint_recovery.py',out/'code/capacity_checkpoint_recovery.py')
    if python_cache_baseline or python_cache_target is not None:
        for name in ('guest_cache_capacity.py','kernel_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if prettier6604 or python_inputs or exp2_mode or exp1_light:
        for name in ('sandbox_runtime_monitor.py','report_sandbox_runtime.py','report_task_overview.py','guest_memory_collector.py',
                     'guest_performance.py','guest_diskstats.py','host_sandbox_monitor.py','report_sandbox_performance.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if prettier6604 and prettier_monitor_mode=='off' or python_inputs and python_monitor_mode=='off':
        shutil.copy2(ROOT/'scripts/task_metrics_prefix.py',out/'code/task_metrics_prefix.py')
    for name in ('validate_isolated_agentenv.py','isolated_storage.py','probe_storage_boundary.py','experiment_disk_guard.py','exp1-process-probe.sh',
                 'run_exp1_checkpoint.py','report_exp1_checkpoint.py','exp1_inputs.py','exp1_official_task.py','exp1_independent_check.cjs'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if prettier6604:
        for name in ('run_prettier6604.py','agentenv_cold_start.py','prettier6604_boundary.py','audit_prettier6604_official.py','prettier6604_inputs.py','prettier6604_check.cjs'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if density:
            for name in ('host_density.py','run_density_guests.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            shutil.copy2(density_path,out/'density-request.json')
        if damon_disable:shutil.copy2(ROOT/'scripts/guest_task_damon.py',out/'code/guest_task_damon.py')
        if prettier_export:
            for name in ('run_prettier6604_grpo.py','prepare_prettier6604_rollouts.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if guest_cache_policy is not None or cache_target is not None:
            for name in ('guest_page_cache_control.py','kernel_cache_metadata.py','guest_diskstats.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if cache_target is not None:shutil.copy2(ROOT/'scripts/guest_cache_capacity.py',out/'code/guest_cache_capacity.py')
    if density and density['task']=='c4':
        for name in ('c4_density_input.py','host_density.py','run_density_guests.py','run_external_workload.py','external_workload.py','agentenv_cold_start.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        shutil.copy2(density_path,out/'density-request.json')
    (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    if cache_metadata_probe:
        for name in ('run_guest_cache_metadata.py','guest_pagecache_marker.py','kernel_cache_metadata.py',
                     'task_physical_cache.py','agentenv_whole_cache.py','measure_file_physical.py',
                     'guest_whole_cache_probe.py','agentenv_cold_start.py','trenvx_whole_cache.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if cache_restore_probe:
            for name in ('run_snapshot_cache_calibration.py','guest_snapshot_cache_fixture.py',
                         'snapshot_cache_residency.py','snapshot_layer_metadata.py','provider_slot_cache.py','run_snapshot_cache_pair.py','collect_snapshot_child.py','live_task_cache.py','fast_cache_metadata.py','guest_cache_exporter.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if capacity_probe:
            for name in ('run_guest_cache_capacity.py','guest_cache_capacity.py','live_task_cache.py','guest_cache_exporter.py','fast_cache_metadata.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if child_cache_probe:
            if seven_restored:shutil.copy2(ROOT/'scripts/seven_restored_pages.py',out/'code/seven_restored_pages.py')
            if cache_sessions:
                for name in ('provider_slot_cache.py','snapshot_cache_residency.py','snapshot_lower_owner.py','snapshot_layer_metadata.py','cache_round_clock.py','run_rollout_cache_calibration.py','rollout_cache_sessions.py','light_prettier_rollout.py','run_guest_light_memory.py','light_memory_round.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if light_memory:
                for name in ('run_guest_light_memory.py','light_memory_round.py','host_sandbox_monitor.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            for name in ('run_guest_cache_child.py','guest_cache_child_fixture.py','live_task_cache.py','guest_cache_exporter.py','fast_cache_metadata.py','prettier_no_proxy.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if dynamic_file_cache:
                for name in ('guest_file_cache_query.py','file_cache_metadata.py','report_dynamic_file_cache.py','report_dynamic_file_calibration.py','report_dynamic_two_child_cache.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if ram_page_evidence:
                shutil.copy2(ROOT/'scripts/ram_page_evidence.py',out/'code/ram_page_evidence.py')
            if vmm_page_evidence:
                for name in ('vmm_page_evidence.py','host_special_metadata.py','host_schedule_files.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if host_backing_evidence:
                from host_backing_evidence import CODE_FILES
                for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if host_backing_pfn_evidence:
                from host_backing_pfn_live import CODE_FILES
                for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if host_backing_full_pfn_evidence:
                from host_backing_pfn_batch import CODE_FILES
                for name in CODE_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if child_cache_count==2:
                for name in ('run_guest_cache_two_children.py','report_two_child_cache.py','report_live_child_calibration.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    if exp2_mode:
        for name in ('run_exp2.py','report_exp2.py','agentenv_cold_start.py','guest_checkpoint_probe.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        shutil.copy2(ROOT/'configs/exp2-prettier6604.json',out/'code/exp2-prettier6604.json')
        (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    if python_inputs:
        if python_schedule:
            for name in ('run_python_schedule.py','prepare_python_schedule_matrix.py','schedule_smoke_metrics.py',
                         'host_schedule_files.py','host_schedule_cache_layers.py','host_deleted_backing.py',
                         'host_file_monitor.py','experiment_memory.py','sandbox_runtime_monitor.py','host_sandbox_monitor.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        for name in ('run_xarray6992.py','xarray6992_inputs.py','xarray6992_check.py','xarray6992_test_ids.py','agentenv_cold_start.py',
                     'run_prettier6604_grpo.py','prepare_prettier6604_rollouts.py','prettier6604_inputs.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if sphinx:
            for name in ('sphinx7590_inputs.py','sphinx7590_check.py','guest_find_diagnostic.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if python_command_diagnostic:
                for name in ('run_sphinx_command_diagnostic.py','guest_command_diagnostic.py','task_metrics_prefix.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
                if env_command_timing:
                    for name in ('sphinx_env_timing.py','guest_sphinx_env_timing.py','report_sphinx_command_diagnostic.py'):shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
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
            if online_agent:
                shutil.copy2(ROOT/'scripts/model_tool_agent.py',out/'code/model_tool_agent.py')
                shutil.copy2(ROOT/'scripts/online_task.py',out/'code/online_task.py')
                shutil.copy2(online_agent,out/'code/online-agent-config.json')
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
    if online_task:
        for name in ('run_tool_capture.py','tool_trajectory.py','guest_tool_capture.py','platform_management.py','model_tool_agent.py','online_task.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        shutil.copy2(online_task,out/'code/online-task-config.json')
        if online_agent:shutil.copy2(online_agent,out/'code/online-agent-config.json')
        if online_points:
            for name in ('task_physical_cache.py','agentenv_whole_cache.py','trenvx_whole_cache.py','cached_file_inventory.py','paused_agentenv_boundary.py','kernel_file_cache.py','report_file_inventory.py','guest_whole_cache_probe.py','guest_cache_marker.py','guest_cached_file_inventory.py','guest_named_file_probe.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        (out/'source-sha256.json').write_text(json.dumps(immutable_manifest(out/'code'),indent=2)+'\n')
    receipt=dict(status='FAIL',scope='temporary actual AgentENV service and snapshot/restore; not rollout or performance acceptance',
                 checks={},capacity_bytes=capacity,result_dir=str(out),failure_fixture_retained=True)
    if django_oci_input:
        receipt['django_oci_input']=dict(status='VALIDATED',directory=str(django_oci_input.resolve()),layout=str(django_oci_layout),
            image=django_oci_state['image'],downloaded_bytes=django_oci_state['downloaded_bytes'],
            accounting_scope='Download occurs outside this slice; later validation/copy faults may charge here. This is a sampling-impact control, not full input-cache accounting')
        shutil.copy2(ROOT/'scripts/django_oci.py',out/'code/django_oci.py')
    image=out/'volume.ext4'; mount=out/'mount'; mount.mkdir()
    container=None; device=None; mounted=False; success=False; sid=None; stage='preflight'; creation_attempted=False
    host_group=host_slice=None
    delete_trace=None
    host_trace=None
    if getattr(args,'host_file_monitor',False):
        from host_file_monitor import measurement_group
        host_group,host_slice=measurement_group()
        shutil.copy2(ROOT/'scripts/host_file_monitor.py',out/'code/host_file_monitor.py')
        shutil.copy2(ROOT/'scripts/experiment_memory.py',out/'code/experiment_memory.py')
        if host_pair_smoke:shutil.copy2(ROOT/'scripts/run_host_file_pair.py',out/'code/run_host_file_pair.py')
        if host_restore:shutil.copy2(ROOT/'scripts/run_host_file_restore.py',out/'code/run_host_file_restore.py')
        if host_matched:shutil.copy2(ROOT/'scripts/host_file_matched_task.py',out/'code/host_file_matched_task.py')
        if prefix_count:shutil.copy2(ROOT/'scripts/host_prefix_compare.py',out/'code/host_prefix_compare.py')
        if scaling:
            for name in ('host_scaling.py','host_prefix_compare.py','run_host_file_restore.py','host_independent_task.py','native_checkpoint_bundle.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if full_shared or rootfs_short:
            for name in ('host_rollout_shared.py','host_rootfs_layers.py','host_prefix_compare.py','host_scaling.py','run_host_file_restore.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if os.environ.get('ASB_HOST_EXPANDED_FILES')=='on':shutil.copy2(ROOT/'scripts/host_expanded_files.py',out/'code/host_expanded_files.py')
        if independent_phase:
            for name in ('native_checkpoint_bundle.py','host_independent_task.py','host_existing_cache_owner.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
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
    if external_config and (external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing') or cython_schedule or task_endpoint_metrics or getattr(args,'external_sampling_control',False)):
        env.update({key:'' for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')})
    if local_no_proxy or schedule_smoke or python_schedule:
        from prettier_no_proxy import clean_environment
        env=clean_environment(env)
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
        ended=time.monotonic_ns()
        commands.append(dict(stage=label,exit_code=r.returncode,duration_ns=ended-start,
            start_monotonic_ns=start,end_monotonic_ns=ended))
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
        receipt['source_service_name']=SERVICE
        receipt['source_service_kind']=original['Config']['Labels'].get('agentenv.experiment')
        receipt['original_service']={k:original['State'][k] for k in ['Pid','StartedAt','Running']}
        if online_agent or online_task or rootfs_short or private_control or full_rootfs_mode or os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':receipt['original_service_cgroup']=Path('/proc',str(original['State']['Pid']),'cgroup').read_text()
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
            if persistent_seed:
                receipt['persistent_cache_seed']={k:seed_value[k] for k in ('profile','task_id','group','group_id','source_env','import_start_monotonic_ns','import_end_monotonic_ns','scratch_removed_before_runtime','scope')}
                receipt['cache_state']='fresh plain-file import in the measurement group; verified warm input, no RAM seed'
            else:
                receipt['immutable_cache_seed']={k:seed_value[k] for k in ('source_backup','backup_sha256','uncompressed_sha256','task_id','ram_allocated_bytes','elapsed_ns')}
                receipt['cache_state']='reused task-converted immutable layers; no cold-download performance claim'
        archive_reserve=0
        if python_durable and not durable_bundle or independent_phase=='capture':
            from native_checkpoint_bundle import ARCHIVE_LIMIT
            archive_reserve=ARCHIVE_LIMIT
        budget=capacity_budget(available(ROOT)-archive_reserve,capacity,**(dict(margin=START_MARGIN_BYTES) if matplotlib_schedule else dict(margin=4*GIB) if c4_schedule else {}))
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
        if capacity_recovery:
            from capacity_checkpoint_recovery import restore_volume
            receipt['capacity_recovery']=restore_volume(capacity_recovery,image)
        else:call('mkfs',['mkfs.ext4','-q','-F','-m','0','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',image],120)
        device=call('attach',['losetup','--find','--show','--',image])
        discard=Path('/sys/class/block')/Path(device).name/'queue/discard_max_bytes'
        discard.write_text('0\n');assert discard.read_text().strip()=='0'
        call('mount',['mount','-t','ext4','-o','nodiscard',device,mount])
        mounted=True
        receipt['mount_identity']=mount_identity(mount,device)
        for name in ['env','run','tmp','controller']:(mount/name).mkdir(exist_ok=bool(capacity_recovery))
        if capacity_recovery:
            from capacity_checkpoint_recovery import prepare_recovered_controller
            recovered_source,slots=prepare_recovered_controller(mount)
            receipt['released_provider_slots']=slots
        with (mount/'cleanup-reserve').open('xb') as stream:os.posix_fallocate(stream.fileno(),0,512*2**20)
        (mount/'controller/config/aenv').mkdir(parents=True,exist_ok=bool(capacity_recovery))
        env['XDG_CONFIG_HOME']=str(mount/'controller/config')
        sampler=threading.Thread(target=sample_disk,daemon=True);sampler.start()
        stage='copy-immutable-cache'
        if full_rootfs_mode:full_copy_start=time.monotonic_ns();full_copy_cpu=time.thread_time_ns()
        # Normal initialization excludes source-service state. Recovery above
        # restores only this experiment's stopped, verified full-volume backup.
        parts=['deps'] if durable_bundle or external_config else ['deps']+['image-cache/'+n for n in ['configs','indexes','commits','remote-blocks','convert-blocks']]
        if capacity_recovery:parts=[]
        copied={}
        for part in parts:
            source_part=source/part;dest=mount/'env'/part
            dest.parent.mkdir(parents=True,exist_ok=True)
            before_copy=immutable_manifest(source_part)
            if seed_value:assert before_copy==seed_value['parts'][part], 'verified seed changed: '+part
            if (private_control or full_rootfs_mode) and part=='image-cache/commits':
                from host_private_files import copy_commits
                receipt['private_commit_preparation']=copy_commits(source_part,dest,private_control or {},call)
                copied[part]=receipt['private_commit_preparation']
                continue
            if not density and (readonly_fixture or python_inputs and seed_value or prettier_readonly or online_task or exp1_light or current_pair or persistent_seed and host_current_only) and part=='image-cache/commits':
                dest.mkdir();copied[part]=before_copy
                receipt['readonly_commits_bytes']=sum(row.get('size',0) for row in before_copy.values())
                continue
            call('copy-'+part.replace('/','-'),['cp','-a','--reflink=never','--sparse=always','--',source_part,dest],120)
            assert immutable_manifest(dest)==before_copy==immutable_manifest(source_part), 'cache changed during copy: '+part
            copied[part]=before_copy
        (out/'copied-input-hashes.json').write_text(json.dumps(copied,indent=2)+'\n')
        receipt['checks']['verified_full_volume_restore' if capacity_recovery else 'immutable_cache_copy']='PASS'
        if full_rootfs_mode:
            from host_full_rootfs_control import prepare as prepare_rootfs_copies
            full_initial_copy=dict(start_monotonic_ns=full_copy_start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-full_copy_cpu,
                scope='Initial immutable-cache copy; parent thread CPU excludes copy subprocess CPU')
            stage='prepare-full-rootfs-copies'
            receipt['full_rootfs_preparation']=prepare_rootfs_copies(mount,full_rootfs_mode,call,schedule=prettier_schedule)
            receipt['full_rootfs_preparation']['initial_cache_copy']=full_initial_copy
            (mount/'env/rootfs-copy-control/preparation.json').write_text(json.dumps(receipt['full_rootfs_preparation'],indent=2)+'\n')
            shutil.copy2(full_rootfs_daemon,mount/'env/deps/rootfs-copy-daemon')
            shutil.copy2(ROOT/'scripts/host_full_rootfs_control.py',out/'code/host_full_rootfs_control.py')
        if density and density['task']=='prettier6604':
            from host_density import prepare as prepare_density
            receipt['density_preparation']=prepare_density(mount,density,call)
            shutil.copy2(density['daemon'],mount/'env/deps/rootfs-copy-daemon')
        receipt['volume_available_after_copy_bytes']=available(mount)
        if django_schedule or c4_schedule:
            # Seven 4 GiB writable allowances; two 8 GiB RAM snapshots; three
            # 4 GiB image-layer allowances (original plus two copies), scratch
            # and margin. These are disk reservations, not measured memory.
            components=dict(writable_workload=28,ram_snapshots=0 if (django_schedule or c4_schedule)=='grpo' else 16,
                image_layers=4 if (django_schedule or c4_schedule)=='grpo' else 12,restore_scratch=0 if (django_schedule or c4_schedule)=='grpo' else 4,
                margin=4,cleanup_reserve=.5)
            required=sum(components.values())*GIB
            receipt['c4_schedule_storage_budget' if c4_schedule else 'django_schedule_storage_budget']=dict(capacity_bytes=capacity,
                remaining_after_copy_bytes=available(mount),components_gib=components,required_remaining_bytes=required)
            assert available(mount)>required,'External seven-guest disk reservation insufficient'
        if cython_schedule:
            from cython_schedule_inputs import storage_components as cython_storage_components
            components=cython_storage_components(cython_schedule);required=sum(components.values())*GIB
            receipt['cython_schedule_storage_budget']=dict(capacity_bytes=capacity,remaining_after_copy_bytes=available(mount),components_gib=components,required_remaining_bytes=required)
            assert available(mount)>required,'Cython seven-guest disk reservation insufficient'
        if text_editing_schedule:
            from text_editing_schedule_inputs import storage_components as text_storage_components
            components=text_storage_components(text_editing_schedule);required=sum(components.values())*GIB
            receipt['text_editing_schedule_storage_budget']=dict(capacity_bytes=capacity,remaining_after_copy_bytes=available(mount),
                components_gib=components,required_remaining_bytes=required)
            assert available(mount)>required,'Text editing seven-guest disk reservation insufficient'
        if matplotlib_schedule:
            from matplotlib_schedule_inputs import storage_components
            components=storage_components(matplotlib_schedule);required=sum(components.values())*GIB
            receipt['matplotlib_schedule_storage_budget']=dict(capacity_bytes=capacity,remaining_after_copy_bytes=available(mount),
                components_gib=components,required_remaining_bytes=required,
                launch_rule='User approved displayed ~104 GiB (103.950 GiB observed); integer rounding gives 103.5 GiB floor, 20 GiB runtime reserve unchanged')
            assert available(mount)>required,'Matplotlib seven-guest disk reservation insufficient'
        assert receipt['volume_available_after_copy_bytes']>(4*GIB if cache_metadata_probe else 14*GIB if python_runtime_smoke else GIB if python_diagnostic or python_evaluation else 6*GIB if python_readonly_replay or prettier_readonly or current_pair else 8*GIB)
        if python_schedule:
            from prettier_no_proxy import check_cache as check_python_cache
            task_id='sphinx-doc__sphinx-7590' if sphinx else 'pydata__xarray-6992'
            initial=check_python_cache(seed_root,task_id=task_id)
            lower_bytes=sum((seed_root/name).stat().st_size for name in initial['local_files'])
            restoring=python_schedule!='grpo'
            ram_budget=8*GIB if restoring else 0
            layer_budget=2*lower_bytes if restoring else 0
            scratch_budget=4*GIB if restoring else 0
            required=28*GIB+ram_budget+layer_budget+scratch_budget+4*GIB+GIB//2
            receipt['python_schedule_storage_budget']=dict(capacity_bytes=capacity,remaining_after_copy_bytes=available(mount),
                guest_count=7,writable_workload_budget_bytes=28*GIB,two_ram_snapshot_bytes=ram_budget,
                two_managed_layer_copy_bytes=layer_budget,restore_scratch_bytes=scratch_budget,
                margin_bytes=4*GIB,cleanup_reserve_bytes=GIB//2,required_remaining_bytes=required,
                scope='Conservative allocation budget, not observed writes or physical memory; source immutable commits are RAM-backed read-only inputs')
            assert available(mount)>required,'seven-role Python snapshot/workload budget insufficient'
        if cache_metadata_probe:
            if seven_restored:
                receipt['seven_restored_budget']=dict(volume_bytes=64*GIB,required_remaining_bytes=55*GIB,
                    source_snapshot=4*GIB,managed_layer=6*GIB,restore_workspaces=28*GIB,
                    workload=7*GIB,page_evidence=7*GIB,margin=3*GIB)
                assert receipt['volume_available_after_copy_bytes']>55*GIB,'seven restore workspace headroom insufficient'
            elif cache_sessions:
                assert receipt['volume_available_after_copy_bytes']>14*GIB, 'cache sessions snapshot headroom insufficient'
                receipt['cache_sessions_budget']=dict(volume_bytes=24*GIB,required_remaining_bytes=14*GIB,ram_snapshot_bytes=4*GIB,restore_scratch_bytes=4*GIB,managed_layer_allowance_bytes=4*GIB,margin_bytes=2*GIB)
            if cache_restore_probe:
                assert receipt['volume_available_after_copy_bytes'] > 14*GIB, 'snapshot calibration headroom insufficient'
            receipt['guest_cache_storage_budget']=dict(capacity_bytes=capacity,remaining_after_input_copy_bytes=receipt['volume_available_after_copy_bytes'],
                workload_write_budget_bytes=GIB,margin_bytes=3*GIB,
                vm_memory_snapshot_bytes=4*GIB if cache_restore_probe else 0,
                scope='one short snapshot cache calibration' if cache_restore_probe else 'one short cache probe, copied native cached layers, no snapshots or full task writes')
            if light_restore:
                assert receipt['volume_available_after_copy_bytes']>24*GIB, 'light restore requires >24 GiB after input copy'
                receipt['light_restore_budget']=dict(required_remaining_bytes=24*GIB,ram_snapshot_bytes=4*GIB,
                    managed_layer_copy_allowance_bytes=6*GIB,restore_scratch_bytes=4*GIB,workload_bytes=7*GIB,margin_bytes=3*GIB)
            if light_memory and child_cache_count==7:
                assert receipt['volume_available_after_copy_bytes']>10*GIB, 'seven light guests require 7 GiB write allowance plus 3 GiB margin'
                receipt['guest_cache_storage_budget'].update(guest_count=7,workload_write_budget_bytes=7*GIB,
                    scope='seven light guests; 16 MiB each, no snapshots; conservative 7 GiB writes plus 3 GiB margin')
            if child_cache_probe and child_cache_count==2:
                assert receipt['volume_available_after_copy_bytes']>5*GIB, 'two private cache guests require 2 GiB writes plus 3 GiB margin'
                receipt['guest_cache_storage_budget'].update(guest_count=2,workload_write_budget_bytes=2*GIB,
                    scope='two private cache guests; 16 MiB per guest, no snapshots; conservative 2 GiB write allowance plus 3 GiB margin')
            if pair_cache_probe:
                receipt['guest_cache_storage_budget'].update(vm_memory_snapshot_bytes=4*GIB,
                    workload_write_budget_bytes=2*GIB,restore_scratch_budget_bytes=4*GIB,margin_bytes=2*GIB,
                    reference_run='20261015T081431Z-1934688',reference_observed_peak_bytes=5557846016,
                    scope='one snapshot, two restored synthetic guests; >14 GiB after immutable copy; 22 GiB fixed volume')
        if exp2_mode:
            assert receipt['volume_available_after_copy_bytes']>24*GIB, 'four snapshots and restore headroom insufficient'
            receipt['exp2_storage_budget']=dict(capacity_bytes=capacity,vm_memory_snapshots_bytes=16*GIB,
                restore_and_writable_budget_bytes=6*GIB,margin_bytes=2*GIB,
                scope='four retained 4 GiB snapshots; fixed volume, guest-only cache control')
        if prettier_export and capacity==16*GIB:
            receipt['grpo_storage_budget']=dict(capacity_bytes=capacity,reference_run='20261008T132220Z-3722480',
                reference_observed_volume_used_peak_bytes=6049792000,writable_workload_budget_bytes=4*GIB,
                margin_bytes=4*GIB,cleanup_reserve_bytes=GIB//2,
                scope='seven GRPO roots only; >8 GiB remains after copying the same converted immutable seed; no snapshot budget')
        if prettier_cache_pair and not capacity_recovery:
            assert receipt['volume_available_after_copy_bytes']>14*GIB, 'actual-task pair headroom insufficient'
            receipt['prettier_pair_storage_budget']=dict(capacity_bytes=capacity,vm_memory_snapshot_bytes=4*GIB,
                writable_layers_budget_bytes=4*GIB,restore_scratch_budget_bytes=4*GIB,margin_bytes=2*GIB,
                remaining_after_copy_bytes=receipt['volume_available_after_copy_bytes'],
                scope='one step-11 snapshot, two restored real Prettier suffixes, fixed 24 GiB volume')
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
        elif python_readonly_replay or prettier_readonly or current_pair:
            # >=6 GiB remains after dependencies/metadata and a separate .5 GiB
            # cleanup reserve: 4 GiB writable workload plus 2 GiB margin.
            receipt['readonly_replay_budget']=dict(capacity_bytes=capacity,writable_workload_budget_bytes=4*GIB,
                margin_bytes=2*GIB,cleanup_reserve_bytes=GIB//2,immutable_commits='external verified read-only RAM cache')
            if persistent_seed:receipt['readonly_replay_budget']['immutable_commits']='external verified read-only fresh plain files'
            if python_schedule:receipt.pop('readonly_replay_budget',None)
        if durable_bundle:
            receipt['readonly_replay_budget']=dict(capacity_bytes=capacity,cleanup_reserve_bytes=GIB//2,immutable_commits='NOT_MOUNTED; native repository plus fixed deps only')
        if python_oci and not python_diagnostic:
            from xarray6992_offline_image import install
            receipt['offline_image_transport']=install(mount,task_image) if sphinx else install(mount)
        if durable_bundle or host_bundle:
            from native_checkpoint_bundle import restore as restore_bundle
            receipt['checkpoint_import']=restore_bundle((durable_bundle or host_bundle).resolve(),mount/'env/snapshot-store')
            if independent_phase in ('single','dual'):
                stage='sync-imported-checkpoint'
                sync_start=time.monotonic_ns()
                call(stage,['sync','-f',mount],60)
                receipt['checkpoint_import_sync']=dict(status='PASS',start_monotonic_ns=sync_start,
                    end_monotonic_ns=time.monotonic_ns(),scope='owned filesystem after import, before service and guest creation')
        if scaling:
            from host_scaling import sync_preparation
            stage='sync-scaling-preparation'
            receipt['host_scaling_preparation_sync']=sync_preparation(call,mount)
        elif persistent_seed:
            if local_no_proxy:
                from prettier_no_proxy import install as install_local_no_proxy
                receipt['local_no_proxy']=install_local_no_proxy(persistent_seed,mount,task_id=local_task_id)
                shutil.copy2(ROOT/'scripts/prettier_no_proxy.py',out/'code/prettier_no_proxy.py')
            from host_scaling import sync_preparation
            stage='sync-persistent-input-preparation'
            receipt['persistent_input_preparation_sync']=sync_preparation(call,mount,**({'timeout':300} if density else {}))
        elif external_config:
            if external_oci_input:
                if c4_schedule:
                    from c4_density_input import install as install_c4_schedule_oci
                    receipt['external_oci_transport']=install_c4_schedule_oci(mount,external_oci_input)
                    shutil.copy2(ROOT/'scripts/c4_density_input.py',out/'code/c4_density_input.py')
                else:
                    from external_oci import install as install_external_oci
                    receipt['external_oci_transport']=install_external_oci(mount,external_oci_input,external_value)
                shutil.copy2(ROOT/'scripts/external_oci.py',out/'code/external_oci.py')
            if density:
                from c4_density_input import install as install_c4_input
                receipt['c4_oci_input']=install_c4_input(mount,density['oci_input'])
                shutil.copy2(density['daemon'],mount/'env/deps/rootfs-copy-daemon')
            if django_oci_input:
                from django_oci import install as install_django_oci
                receipt['django_oci_input']['entrypoint']=install_django_oci(mount,
                    **({'local_metadata_layout':django_oci_layout} if django_schedule else {}))
            from host_scaling import sync_preparation
            receipt['external_workload_preparation_sync']=sync_preparation(call,mount)
        if full_shared or rootfs_short or full_rootfs_mode:
            from host_scaling import sync_preparation
            stage='sync-shared-preparation'
            if full_rootfs_mode:sync_cpu=time.thread_time_ns()
            if full_rootfs_mode:
                # Seven prepared sets can have far more pending writes than the old single set.
                receipt['host_shared_preparation_sync']=sync_preparation(call,mount,timeout=300)
            else:receipt['host_shared_preparation_sync']=sync_preparation(call,mount)
            if full_rootfs_mode:receipt['host_shared_preparation_sync']['cpu_ns']=time.thread_time_ns()-sync_cpu
        config=call('original-config',['docker','exec',SERVICE,'cat','/workspace/config/default.toml'])
        if diagnostic_kernel:
            target=mount/'env/deps/kernel/asb-diagnostic-vmlinux.bin'
            shutil.copy2(diagnostic_kernel,target)
            assert hashlib.file_digest(target.open('rb'),'sha256').digest()==hashlib.file_digest(diagnostic_kernel.open('rb'),'sha256').digest()
            assert 'image_path' not in tomllib.loads(config).get('kernel',{})
            assert config.count('[kernel]\n')==1
            config=config.replace('[kernel]\n','[kernel]\nimage_path = "/workspace/env/deps/kernel/asb-diagnostic-vmlinux.bin"\n')
            receipt['diagnostic_guest_kernel']=dict(source=str(diagnostic_kernel),sha256=hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),
                scope='new private service only; original kernel and host kernel unchanged')
        overrides=[('image.cache.gc','enabled','false'),('pool.network','maintenance_enabled','false'),
                   ('pool.block','enabled','false'),('pool.firecracker','enabled','false'),
                   ('pool.firecracker','startup_prewarm','false'),('ublk','daemon_metrics_listen_addr','""'),
                   ('network.internal','host_interaction_cidr','"10.211.0.0/16"'),
                   ('network.internal','veth_cidr','"10.212.0.0/16"')]
        if provider_binary:
            target=mount/'env/deps/uvm-ublk-daemon'
            shutil.copy2(provider_binary,target)
            digest=hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
            assert digest==hashlib.file_digest(provider_binary.open('rb'),'sha256').hexdigest()
            receipt['provider_binary']=dict(source=str(provider_binary),sha256=digest,scope='owned service only')
        # Separate network pools avoid overlap with the existing host-network service.
        routes=call('routes',['ip','-j','route','show','table','all'],10)
        if rootfs_short or private_control or full_rootfs_mode or density or os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':
            from isolated_storage import rootfs_network_pools
            pair=rootfs_network_pools(routes) if not private_control else private_control['network_pools']
            if private_control:
                import ipaddress
                assigned=[ipaddress.ip_network(v) for v in pair]
                occupied=[ipaddress.ip_network(row['dst']) for row in json.loads(routes) if row.get('dst') not in (None,'default')]
                assert not any(a.overlaps(b) for a in assigned for b in occupied if a.version==b.version), 'private network pools overlap'
            overrides=[(s,k,json.dumps(pair[0] if k=='host_interaction_cidr' else pair[1])) if s=='network.internal' else (s,k,v) for s,k,v in overrides]
            receipt['rootfs_network_pools']=pair
            if getattr(args,'external_workload_relay',False) and os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':
                proxy_network=Path(os.environ['ASB_EXTERNAL_GUEST_RELAY_STATE'])/'guest-proxy-network.json'
                assert json.loads(proxy_network.read_text())['pools']==pair,'relay and native private pools changed during preparation'
        else:assert '10.211.' not in routes and '10.212.' not in routes, 'candidate network pools already in use'
        for section,key,value in overrides:config=config_value(config,section,key,value)
        if density and density['task']=='c4':
            assert 'posix_fs' not in tomllib.loads(config).get('backend',{})
            config+='\n[backend.posix_fs]\nsnapshot_store = \"/workspace/env/snapshot-store\"\n'
        if python_durable:
            assert 'posix_fs' not in tomllib.loads(config).get('backend',{})
            config+='\n[backend.posix_fs]\nsnapshot_store = \"/workspace/env/snapshot-store\"\n'
        if vmm_check:
            assert 'binary_path' not in tomllib.loads(config)['firecracker']
            config=config.replace('[firecracker]\n','[firecracker]\nbinary_path = "/workspace/env/deps/vmm-cgroup-launcher"\n',1)
            shutil.copy2(vmm_check/'vmm-cgroup-launcher',mount/'env/deps/vmm-cgroup-launcher')
            for filename in ('vmm_cgroup_launcher.c','vmm_child_cgroup_check.py','host_existing_cache_owner.py','host_sandbox_monitor.py','host_file_monitor.py','experiment_memory.py'):
                shutil.copy2(ROOT/'scripts'/filename,out/'code'/filename)
        tomllib.loads(config)
        (out/'isolated-config.toml').write_text(config)
        receipt['config_overrides']=[section+'.'+key+'='+value for section,key,value in overrides]
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        receipt['address']='http://127.0.0.1:'+str(port)
        (mount/'env/logs').mkdir(exist_ok=bool(capacity_recovery))
        name='asb-storage-check-'+out.name
        argv=['docker','create','--name',name,'--label','agentenv.experiment='+('initial-smoke' if exp1 else 'isolated-storage-check'),
              '--label','agentsandboxbench.run_id='+out.name,'--privileged','--read-only','--log-driver','none',
              '--network','host','--ulimit','memlock=-1:-1',
              '--mount','type=bind,src=/dev,dst=/dev',
              '--mount','type=bind,src='+str(out/'isolated-config.toml')+',dst=/workspace/config/default.toml,readonly']
        if diagnostic_server:
            server_copy=out/'diagnostic-server'
            shutil.copy2(diagnostic_server,server_copy)
            argv+=['--mount','type=bind,src='+str(server_copy)+',dst=/server,readonly']
            receipt['diagnostic_server']=dict(source=str(diagnostic_server),private_copy=str(server_copy),size_bytes=server_copy.stat().st_size)
        if host_slice:argv+=['--cgroup-parent',host_slice]
        if vmm_check:
            assert not host_slice
            parent=Path(os.environ['ASB_VMM_CGROUP_PARENT'])
            assert parent.is_relative_to('/sys/fs/cgroup') and parent.name.startswith('asbvmm')
            argv+=['--cgroupns','host','--cgroup-parent',parent.name,'--label','asb.vmm-child-cgroup=on',
                   '--label','asb.vmm-parent='+str(parent),'--env','ASB_VMM_CGROUP_PARENT='+str(parent)]
        for src,dst in write_mounts(mount).items():argv+=['--mount','type=bind,src='+src+',dst='+dst]
        if external_config:
            # Forward approved proxy variables by name; values never enter commands/receipts.
            if (external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing') or cython_schedule or task_endpoint_metrics or getattr(args,'external_sampling_control',False)):
                argv+=[item for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') for item in ('--env',key+'=')]
            else:
                for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
                    if key in os.environ:argv+=['--env',key]
            argv+=['--env','NO_PROXY=127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16',
                   '--env','no_proxy=127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16']
        if private_control:
            from host_private_files import target_mounts
            receipt['private_target_mounts']=target_mounts(private_control,mount)
            for row in receipt['private_target_mounts']:
                argv+=['--mount','type=bind,src='+row['source']+',dst='+row['destination']+',readonly']
        if not density and not durable_bundle and (readonly_fixture or python_inputs and seed_value or prettier_readonly or online_task or exp1_light or current_pair or persistent_seed and host_current_only):
            argv+=['--mount','type=bind,src='+str(source/'image-cache/commits')+',dst=/workspace/env/image-cache/commits,readonly']
        if python_oci:
            argv+=['--mount','type=bind,src='+str(python_oci.resolve()/'ram/oci')+',dst=/asb-frozen-oci,readonly']
        if density and density['task']=='c4':
            argv+=['--mount','type=bind,src='+str(c4_layout)+',dst=/asb-c4-oci,readonly']
        if django_oci_input:
            argv+=['--mount','type=bind,src='+str(django_oci_layout)+',dst=/asb-django-oci,readonly']
        if external_oci_input:
            argv+=['--mount','type=bind,src='+str(external_oci_layout)+(',dst=/asb-c4-oci,readonly' if c4_schedule else ',dst=/asb-external-oci,readonly')]
        # Only these path variables are copied; no credentials or source-service state.
        original_env=dict(e.split('=',1) for e in original['Config']['Env'] if '=' in e)
        for key in ['AENV_CONFIG_PATH','AENV_HOME_PATH','AENV_UBLK_DAEMON_BINARY_PATH']:
            if (provider_binary or full_rootfs_mode or density) and key=='AENV_UBLK_DAEMON_BINARY_PATH':continue
            if key in original_env:argv+=['-e',key+'='+original_env[key]]
        if provider_binary:
            argv+=['-e','AENV_UBLK_DAEMON_BINARY_PATH=/workspace/env/deps/uvm-ublk-daemon',
                   '-e','AENV_PROVIDER_OBSERVE_DIR=/workspace/env/provider-slots']
            if provider_get_data:argv+=['-e','AENV_PROVIDER_NEED_GET_DATA=1']
        if full_rootfs_mode or density:
            argv+=['-e','AENV_UBLK_DAEMON_BINARY_PATH=/workspace/env/deps/rootfs-copy-daemon',
                   '-e','AENV_ROOTFS_COPY_MANIFEST=/workspace/env/rootfs-copy-control/manifest.json']
        if schedule_smoke:
            from schedule_smoke_metrics import install_offline
            receipt['schedule_smoke_offline']=install_offline(mount,out)
            for name in ('schedule_smoke_metrics.py','prettier_no_proxy.py',*
                         (('host_schedule_files.py','host_file_monitor.py','experiment_memory.py','host_special_metadata.py') if getattr(args,'schedule_expanded_files',False) else ())):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if getattr(args,'schedule_cache_layers',False):
                for name in ('host_schedule_cache_layers.py','host_deleted_backing.py','isolated_storage.py','host_memory_backing_links.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        if python_schedule:
            from prettier_no_proxy import prepare_metadata as prepare_local_metadata, install as install_python_local
            local_input=out/'python-schedule-local-input';local_input.mkdir()
            task_id='sphinx-doc__sphinx-7590' if sphinx else 'pydata__xarray-6992'
            prepare_local_metadata(local_input/'local-no-proxy-metadata',task_id=task_id)
            receipt['python_schedule_offline']=install_python_local(local_input,mount,task_id=task_id,cache_root=seed_root)
        if child_cache_probe:
            from prettier_no_proxy import prepare_metadata, install
            local_input=out/'child-cache-local-input';local_input.mkdir()
            prepare_metadata(local_input/'local-no-proxy-metadata')
            receipt['child_cache_offline']=install(local_input,mount,cache_root=seed_root)
        if (prettier6604 or python_inputs) and not (local_no_proxy or schedule_smoke or python_schedule):
            for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
                argv+=['-e',key+'=http://127.0.0.1:17891']
            bypass='localhost,127.0.0.1,::1,10.211.0.0/16,10.212.0.0/16'
            if rootfs_short or private_control or full_rootfs_mode:bypass='localhost,127.0.0.1,::1,'+','.join(receipt['rootfs_network_pools'])
            for key in ('NO_PROXY','no_proxy'):
                argv+=['-e',key+'='+bypass]
        if local_no_proxy or schedule_smoke or python_schedule or child_cache_probe or density and density['task']=='c4':
            from prettier_no_proxy import container_environment
            argv+=container_environment()
        if getattr(args,'external_delete_trace',False):
            from private_delete_trace import LOG_FILTER
            argv+=['--env','RUST_LOG='+LOG_FILTER,'--env','AENV_LOG_SPAN_EVENTS=close']
            shutil.copy2(ROOT/'scripts/private_delete_trace.py',out/'code/private_delete_trace.py')
        argv+=['-e','API_ADDR=127.0.0.1:'+str(port),'-e','AENV_FIRECRACKER_SERIAL_DIR=/workspace/env/logs/serial',
               '--entrypoint','/bin/sh',IMAGE,'-c','exec /server >>/workspace/env/logs/server.log 2>&1']
        stage='isolated-server-start'
        creation_attempted=True
        container=call('create-server',argv,60)
        receipt['container_id']=container
        receipt['boundary']=verify_container_boundary(inspect(container),mount,device)
        if diagnostic_server:
            server_mounts=[m for m in inspect(container)['Mounts'] if m['Destination']=='/server']
            assert len(server_mounts)==1 and server_mounts[0]['Source']==str(server_copy) and not server_mounts[0]['RW'], 'private server read-only mount mismatch'
            receipt['diagnostic_server']['read_only_mount_verified']=True
        if django_oci_input:
            from django_oci import verify_mount as verify_django_oci_mount
            verify_django_oci_mount(inspect(container),django_oci_layout)
            receipt['django_oci_input']['read_only_bind_verified']=True
        if c4_schedule:
            assert any(m['Type']=='bind' and m['Source']==str(external_oci_layout) and m['Destination']=='/asb-c4-oci' and not m['RW'] for m in inspect(container)['Mounts'])
            receipt['c4_oci_read_only_bind_verified']=True
        if private_control:
            from host_private_files import verify_mounts
            verify_mounts(inspect(container),receipt['private_target_mounts'])
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
        if vmm_check:
            from vmm_child_cgroup_check import setup_service
            stage='vmm-cgroup-setup'
            vmm_sampler=setup_service(info,mount,out)
            receipt['vmm_child_cgroups']=json.loads((out/'vmm-cgroup-setup.json').read_text())
        if getattr(args,'external_delete_trace',False):
            from private_delete_trace import DeleteTrace
            delete_trace=DeleteTrace(container,info['State']['Pid'],out/'delete-trace')
        key=call('private-key',['docker','exec',container,'cat','/workspace/env/secrets/api-key'],sensitive=True)
        credentials=mount/'controller/config/aenv/credentials'
        credentials.write_text('url = '+json.dumps(receipt['address'])+'\napi_key = '+json.dumps(key)+'\n')
        credentials.chmod(0o600)
        assert json.loads(cli('list-before','list','--output','json'))==[]
        if host_group:
            from host_file_monitor import CGROOT, controller_state, counters as host_counters, pids
            actual=next(line[3:] for line in Path('/proc',str(info['State']['Pid']),'cgroup').read_text().splitlines() if line.startswith('0::'))
            assert (CGROOT/actual.lstrip('/')).is_relative_to(host_group)
            pools=tomllib.loads(config)['pool']
            assert pools['firecracker']['enabled'] is False and pools['firecracker']['startup_prewarm'] is False and pools['block']['enabled'] is False
            assert not [pid for pid in pids(host_group) if Path('/proc',str(pid),'comm').read_text().strip()=='firecracker']
            io_state=dict(measurement_slice=controller_state(host_group),private_service=controller_state(CGROOT/actual.lstrip('/')))
            assert 'io' in io_state['measurement_slice']['subtree_control'].split() and io_state['private_service']['io_stat_exists'], 'host file monitoring requires I/O accounting before guest creation'
            receipt['host_file_creation']=dict(cgroup=str(host_group),docker_cgroup=actual,host_sampling=host_sampling,
                verified_before_guest_creation_ns=time.monotonic_ns(),preexisting_firecrackers=0,
                before_guest_counters=host_counters(host_group) if host_sampling=='on' or scaling else None,
                io_controller_state=io_state,
                firecracker_pool=False,startup_prewarm=False,block_pool=False,
                scope='Fresh workflow slice covers cache preparation and new Docker service; no pooled guest process reused')
        receipt['checks']['isolated_service_ready']='PASS'
        if private_control:
            from host_private_files import gate
            gate('service-ready',dict(container=container,mount=str(mount),storage=str(out),creation=receipt['host_file_creation']))
        if capacity_recovery:
            binding=json.loads((recovered_source/'capacity-checkpoint.json').read_text())
            checkpoint=json.loads((recovered_source/'checkpoint-11.json').read_text())
            (mount/'controller/capacity-recovery.json').write_text(json.dumps(dict(status='PASS',
                previous_binding=binding,new_binding=dict(container=container,config_home=str((mount/'controller/config').resolve()),
                service_url=receipt['address']),template=checkpoint['template'],volume_restore=receipt['capacity_recovery']),indent=2)+'\n')
        frozen=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())
        if density:
            stage='host-density';replay=mount/'controller/density'
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_density_guests.py','--request',density_path,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address'],
                '--output',replay,*(['--external-config',external_config,'--upstream',external_upstream] if density['task']=='c4' else
                ['--official-task',prettier6604,'--expected-official-audit',expected_audit])],1440 if 'pressure_context' in density else 840,environment=env)
            density_result=json.loads((replay/'result.json').read_text())
            assert density_result['status']=='PASS' and density_result['actions']==density['count']*(36 if density['task']=='c4' else 29)
            assert density_result['identity']['status']=='PASS' and density_result['cleanup']['service_sandbox_list_empty']
            receipt['density_result']=density_result
            receipt['checks']['density_identical_backbones_and_identity']='PASS'
        elif creation_only:
            stage='external-creation-controls';replay=mount/'controller/external-creation'
            for name in ('host_creation_controls.py','agentenv_cold_start.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            call(stage,[sys.executable,'-B',ROOT/'scripts/host_creation_controls.py','--config',external_config,
                '--output',replay,'--config-home',mount/'controller/config','--service-url',receipt['address'],
                '--mount',mount],720,environment=env)
            created=json.loads((replay/'result.json').read_text())
            assert created['status']=='PASS' and created['actions']==0 and len(created['runs'])==4 and len(created['guests'])==5
            assert created['cleanup']['service_sandbox_list_empty'] and created['cleanup']['known_guests_deleted']
            receipt['external_creation_result']=created
            receipt['checks']['host_only_creation_controls']='PASS'
        elif c4_schedule:
            stage='c4-schedule-'+c4_schedule;replay=mount/'controller/external-workload'
            for name in ('run_python_schedule.py','c4_schedule_inputs.py','schedule_smoke_metrics.py','host_schedule_files.py',
                         'host_schedule_cache_layers.py','sandbox_runtime_monitor.py','host_sandbox_monitor.py','host_file_monitor.py','experiment_memory.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',c4_schedule_inputs,
                '--task-profile','c4','--schedule',c4_schedule,'--output',replay,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address']],1200,environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            assert external_result['status']=='PASS' and external_result['actual_action_count']==(252 if c4_schedule=='grpo' else 93)
            assert external_result['task_id']=='reshard-c4-data' and external_result['strict_replay_status']=='PASS'
            assert external_result['checks']['endpoint_identity_and_queries']=='PASS' and all(external_result['cleanup'].values())
            receipt['external_workload_result']=external_result
            receipt['checks']['c4_original_path_prefix_reuse_and_metrics']='PASS'
            receipt['scope']='Controlled seven-guest C4 original-path prefix reuse; original 9898-file roundtrip/constraints/syntax, no hidden verifier'
        elif cython_schedule:
            stage='cython-schedule-'+cython_schedule;replay=mount/'controller/external-workload'
            for name in ('run_python_schedule.py','cython_schedule_inputs.py','cython_schedule_proxy.py','external_guest_proxy.py','schedule_smoke_metrics.py','host_schedule_files.py',
                         'host_schedule_cache_layers.py','report_python_schedule_matrix.py','external_workload.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if getattr(args,'external_workload_relay',False):
                assert receipt.get('rootfs_network_pools')
                env['ASB_EXTERNAL_GUEST_RELAY_POOL']=receipt['rootfs_network_pools'][0]
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',cython_schedule_inputs,
                '--task-profile','cython','--schedule',cython_schedule,'--output',replay,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address'],
                *(['--guest-relay'] if getattr(args,'external_workload_relay',False) else [])],1800,environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            assert external_result['status']=='PASS' and external_result['actual_action_count']==(581 if cython_schedule=='grpo' else 176)
            assert external_result['logical_action_count']==581 and external_result['functional_validation']==external_result['strict_replay_status']=='PASS'
            assert external_result['checks']['endpoint_identity_and_queries']=='PASS' and all(external_result['cleanup'].values())
            receipt['external_workload_result']=external_result
            receipt['checks']['cython_original_path_prefix_reuse_and_metrics']='PASS'
            receipt['scope']='Controlled seven-guest original Cython path; original NumPy snippet, two 18-test contexts, compiled imports and tracked patch; no hidden verifier'
        elif text_editing_schedule:
            stage='text-editing-schedule-'+text_editing_schedule;replay=mount/'controller/external-workload'
            for name in ('run_python_schedule.py','text_editing_schedule_inputs.py','schedule_smoke_metrics.py','host_schedule_files.py',
                         'host_schedule_cache_layers.py','report_python_schedule_matrix.py','external_workload.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',text_editing_schedule_inputs,
                '--task-profile','text_editing','--schedule',text_editing_schedule,'--output',replay,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address']],1800,environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            assert external_result['status']=='PASS' and external_result['actual_action_count']==(350 if text_editing_schedule=='grpo' else 113)
            assert external_result['logical_action_count']==350 and external_result['functional_validation']==external_result['strict_replay_status']=='PASS'
            assert external_result['checks']['endpoint_identity_and_queries']=='PASS' and all(external_result['cleanup'].values())
            receipt['external_workload_result']=external_result
            receipt['checks']['text_editing_original_path_prefix_reuse_and_metrics']='PASS'
            receipt['scope']='Controlled seven-guest original text editing path; byte-exact output and both solve variants; no hidden verifier'
        elif matplotlib_schedule:
            stage='matplotlib-schedule-'+matplotlib_schedule;replay=mount/'controller/external-workload'
            for name in ('run_python_schedule.py','matplotlib_schedule_inputs.py','schedule_smoke_metrics.py','host_schedule_files.py',
                         'host_schedule_cache_layers.py','report_python_schedule_matrix.py','external_workload.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',matplotlib_schedule_inputs,
                '--task-profile','matplotlib24177','--schedule',matplotlib_schedule,'--output',replay,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address']],1800,environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            assert external_result['status']=='PASS' and external_result['actual_action_count']==(665 if matplotlib_schedule=='grpo' else 185)
            assert external_result['logical_action_count']==665 and external_result['functional_validation']==external_result['strict_replay_status']=='PASS'
            assert external_result['checks']['endpoint_identity_and_queries']=='PASS' and all(external_result['cleanup'].values())
            receipt['external_workload_result']=external_result
            receipt['checks']['matplotlib_original_path_prefix_reuse_and_metrics']='PASS'
            receipt['scope']='Controlled seven-guest Matplotlib original path; original two 60-pass histogram tests, full staged patch and imports; no hidden verifier'
        elif django_schedule:
            stage='django-schedule-'+django_schedule;replay=mount/'controller/external-workload'
            for name in ('run_python_schedule.py','prepare_python_schedule_matrix.py','django_schedule_inputs.py',
                         'schedule_smoke_metrics.py','host_schedule_files.py','host_schedule_cache_layers.py',
                         'sandbox_runtime_monitor.py','host_sandbox_monitor.py','host_file_monitor.py','experiment_memory.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',django_schedule_inputs,
                '--task-profile','django16819','--schedule',django_schedule,'--output',replay,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address']],1200,environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            expected=196 if django_schedule=='grpo' else 64
            assert external_result['status']=='PASS' and external_result['actual_action_count']==expected
            assert external_result['task_id']=='django__django-16819' and external_result['strict_replay_status']=='PASS'
            assert external_result['checks']['endpoint_identity_and_queries']=='PASS'
            assert all(external_result['cleanup'].values())
            receipt['external_workload_result']=external_result
            receipt['checks']['django_original_path_prefix_reuse_and_metrics']='PASS'
            receipt['scope']='Controlled seven-guest Django original-path prefix reuse, not native seven branches; syntax and fixed patch only, no official Django suite'
        elif external_config:
            stage='external-workload';replay=mount/'controller/external-workload'
            if getattr(args,'external_workload_relay',False) and receipt.get('rootfs_network_pools'):
                env['ASB_EXTERNAL_GUEST_RELAY_POOL']=receipt['rootfs_network_pools'][0]
            for name in ('external_workload.py','external_guest_proxy.py','run_external_workload.py','agentenv_cold_start.py'):
                shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if (external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing') or task_endpoint_metrics):
                from external_task_metrics import SNAPSHOT_FILES
                for name in SNAPSHOT_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
            if getattr(args,'external_guest_count',1)==2:shutil.copy2(ROOT/'scripts/run_c4_parallel.py',out/'code/run_c4_parallel.py')
            call(stage,[sys.executable,'-B',ROOT/'scripts'/('run_c4_parallel.py' if getattr(args,'external_guest_count',1)==2 else 'run_external_workload.py'),'--config',external_config,
                '--upstream',external_upstream,'--output',replay,'--container',container,
                '--config-home',mount/'controller/config','--service-url',receipt['address'],
                *(['--sampling','off'] if external_off else []),*(['--endpoint-metrics'] if task_endpoint_metrics else []),*(['--vmm-pss-interval-seconds',str(vmm_pss_interval)] if vmm_pss_interval!=1 else []),*(['--guest-relay'] if getattr(args,'external_workload_relay',False) else []),*(['--sampling-control'] if getattr(args,'external_sampling_control',False) else [])],external_budgets(external_value['task_id'])['controller'],environment=env)
            external_result=json.loads((replay/'result.json').read_text())
            assert external_result['status']=='PASS' and external_result['actions']==getattr(args,'external_guest_count',1)*external_value['actions'] and external_result['task_id']==external_value['task_id'] and external_result['strict_exit_match']
            if external_value['task_id']=='reshard-c4-data':assert external_result['roundtrip_validation']=='PASS'
            if external_value['task_id']=='build-cython-ext':assert external_result['functional_validation']=='PASS'
            if (external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing') or task_endpoint_metrics):
                from external_task_metrics import validate_result
                if getattr(args,'external_guest_count',1)==2:
                    from run_c4_parallel import validate_parallel
                    validate_parallel(external_result,'off' if external_off else 'on',pss_interval_seconds=vmm_pss_interval)
                else:validate_result(external_result,pss_interval_seconds=vmm_pss_interval)
            assert external_result['cleanup']['sandbox_removed'] and external_result['cleanup']['service_sandbox_list_empty']
            if external_off:assert external_result['sampling']=='off' and external_result['memory']=='NOT_MEASURED' and external_result['cleanup']['memory_sampling_disabled']
            receipt['external_workload_result']=external_result
            key='external_fixed_tools_and_extensions_and_tests' if external_value['task_id']=='build-cython-ext' else 'external_frozen_tools_and_original_syntax_check'
            if (external_value['task_id'] in ('matplotlib__matplotlib-24177','large-scale-text-editing') or task_endpoint_metrics):key='external_fixed_tools_original_function_and_endpoint_metrics'
            receipt['checks'][key]='PASS'
        elif host_pair_smoke:
            stage='host-file-pair-smoke';replay=mount/'controller/host-file-pair'
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_host_file_pair.py','guests',
                '--container',container,'--config-home',mount/'controller/config',
                '--service-url',receipt['address'],'--output',replay],240,environment=env)
            pair_result=json.loads((replay/'result.json').read_text())
            assert pair_result['status']=='PASS' and len(pair_result['guests'])==2
            assert all(value=='PASS' for value in pair_result['checks'].values())
            receipt['host_file_pair_result']=pair_result
            receipt['checks']['two_cold_guests_host_file_smoke']='PASS'
            receipt['scope']=pair_result['scope']
        elif cache_metadata_probe:
            stage='guest-cache-metadata-probe';replay=mount/'controller/guest-cache'
            runner=('run_guest_cache_two_children.py' if child_cache_count==2 else 'run_guest_cache_child.py') if child_cache_probe else 'run_guest_cache_capacity.py' if capacity_probe else 'run_snapshot_cache_pair.py' if pair_cache_probe else 'run_snapshot_cache_calibration.py' if cache_restore_probe else 'run_guest_cache_metadata.py'
            if light_memory:runner='run_guest_light_memory.py'
            if cache_sessions:runner='run_paused_native.py' if paused_native else 'run_rollout_cache_calibration.py'
            call(stage,[sys.executable,'-B',ROOT/'scripts'/runner,'--container',container,
                '--config-home',mount/'controller/config','--service-url',receipt['address'],'--output',replay,*(['--seven-restored'] if seven_restored else []),*(['--immediate-restores'] if immediate else []),*(['--restored-lower'] if restored_lower else []),*(['--unified-rounds'] if unified_cache_rounds else []),*(['--guests',str(child_cache_count)] if light_memory else []),*(['--restore-one'] if light_restore else []),*(['--lower-cache'] if lower_cache_probe and not pair_cache_probe else []),*(['--provider-cache'] if provider_binary else []),*(['--batch-lookup'] if batch_lookup else []),*(['--dynamic-file'] if dynamic_file_cache else []),*(['--ram-page-evidence'] if ram_page_evidence else []),*(['--vmm-page-evidence'] if vmm_page_evidence else []),*(['--io-uring-page-evidence'] if io_uring_page_evidence else []),*(['--host-backing-evidence'] if host_backing_evidence else []),*(['--host-backing-pfn-evidence'] if host_backing_pfn_evidence else []),*(['--host-backing-full-pfn-evidence'] if host_backing_full_pfn_evidence else []),*(['--host-backing-charge-evidence'] if host_backing_charge_evidence else [])],900 if seven_restored else 480 if light_restore else 420 if cache_restore_probe else 240,environment=env)
            measured=json.loads((replay/'result.json').read_text())
            assert measured['status']=='PASS'
            receipt['guest_cache_metadata']=measured
            receipt['checks']['paused_native_entry_boundary' if paused_native else 'guest_cache_metadata_without_pause']='PASS'
            receipt['scope']='private diagnostic guest kernel; one snapshot cache restore calibration; native checkpoint pause only' if cache_restore_probe else 'private diagnostic guest kernel; whole-cache metadata to host residency; no VM pause or trajectory replay'
            if cache_sessions:receipt['scope']=measured['scope']
            if light_memory:
                receipt['scope']=measured['scope']
                receipt['checks']['light_memory_rounds']='PASS'
            if capacity_probe:receipt['scope']='one owned guest; clean regular-file cache periodic reclamation, two targets, synthetic file demand; VMM residency only, no snapshot or full task'
        elif online_task:
            stage='online-task-tools';replay=mount/'controller/prettier6604'
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_tool_capture.py','--inputs',prettier6604,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address'],'--output',replay,*online_args],780,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS' and task_result['checks']['task_outcome_preserved']=='PASS'
            assert task_result['checks']['frozen_replay_matches_calls_and_final_file']=='PASS' and all(task_result['cleanup'].values())
            receipt['online_task_result']=task_result
            receipt['checks']['online_task_freeze_replay']='PASS';receipt['scope']=task_result['scope']
        elif python_inputs:
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
            if python_live_cache:task_command+=['--live-cache']
            if python_cache_baseline:task_command+=['--cache-baseline']
            if python_cache_target is not None:task_command+=['--cache-target-mib',str(python_cache_target)]
            if light_monitor:task_command+=['--light-monitor']
            if task_endpoint_metrics:
                from external_task_metrics import SNAPSHOT_FILES
                for name in SNAPSHOT_FILES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
                task_command+=['--endpoint-metrics']
            if python_schedule:
                stage=python_profile+'-'+python_schedule
                task_command=[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',python_inputs,
                    '--task-profile',python_profile,'--schedule',python_schedule,'--container',container,
                    '--config-home',mount/'controller/config','--service-url',receipt['address'],'--output',replay]
                if python_branch_policy!='original':task_command+=['--branch-pipeline-policy',python_branch_policy]
                if python_schedule_find:task_command+=['--find-exit-diagnostic']
                if python_schedule_xarray_find:task_command+=['--xarray-find-exit-diagnostic']
            call(stage,task_command,900,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS'
            if task_endpoint_metrics:
                from external_task_metrics import validate_result
                validate_result(task_result)
            if local_no_proxy:
                assert task_result['local_no_proxy']=='PASS'
                receipt['local_no_proxy']['guest_check']='PASS'
                log=mount/'env/logs/local-no-proxy-regctl.log'
                registry=log.read_text().splitlines() if log.exists() else []
                assert 'version' in registry and all(x in ('version','manifest') for x in registry),'unexpected Python-task regctl operation'
                receipt['local_no_proxy']['registry_commands']=registry
            if python_command_diagnostic:
                assert task_result['command_diagnostic']
                if env_command_timing:
                    assert task_result['env_command_timing']==env_command_timing and task_result['checks']['env_command_timing_and_original_prefix']=='PASS'
                else:assert task_result['checks']['find_captures_and_source_unchanged' if python_find_diagnostic else 'repeats_outputs_exits_and_source_unchanged']=='PASS'
            elif python_durable:assert task_result['native_checkpoint_demo'] and task_result['checks']['process_memory_open_fd_files_and_boundary' if durable_bundle else 'snapshot_after_two_actual_calls']=='PASS'
            elif python_tool_capture:
                assert task_result['tool_capture_demo'] and task_result['checks']['frozen_replay_matches_calls_and_final_file']=='PASS'
                if tool_source:
                    expected=json.loads((tool_source/'manifest.json').read_text())
                    assert task_result['replay']['action_count']==expected['action_count']
                    assert task_result['replay']['expected_failures']==expected['expected_failures']
                    assert task_result['task_id']==json.loads(args.python_tool_task_config.read_text())['task_id']
                    assert task_result['checks']['independent_task']==task_result['checks']['independent_replayed_task']==task_result['task_outcome']
                    assert task_result['checks']['task_outcome_preserved']=='PASS'
                elif online_agent:
                    assert task_result['online_agent'] and task_result['replay']['action_count']>0
                    assert task_result['checks']['independent_csv_task']==task_result['checks']['independent_replayed_csv_task']==task_result['task_outcome']
                    assert task_result['checks']['task_outcome_preserved']=='PASS'
                else:assert task_result['replay']['action_count']==4
            elif python_runtime_smoke:assert task_result['runtime_monitor_smoke'] and task_result['checks']['continuous_idle_and_per_instance_curves']=='PASS'
            elif python_cache_smoke:assert task_result['cache_metrics_smoke'] and task_result['checks']['exact_kernel_physical_cache']=='PASS'
            elif python_evaluation:assert task_result['evaluation_only'] and task_result['checks']['complete_benchmark_results_and_source_unchanged']=='PASS'
            elif python_diagnostic:assert task_result['checks']['find_diagnostic_capture']=='PASS'
            elif python_schedule:
                assert task_result['schedule']==python_schedule and task_result['actual_action_count']==(230 if python_schedule=='grpo' else 104)
                assert task_result['checks']['endpoint_identity_and_queries']=='PASS'
            else:assert task_result['replay']['action_count']==35
            assert task_result['cleanup']['service_sandbox_list_empty']
            if python_live_cache:
                assert task_result['cleanup']['live_cache_stopped'] and task_result['live_cache']['status']=='PASS'
                receipt['checks']['whole_cache_sampling_without_pause']='PASS'
            if python_cache_baseline:
                assert task_result['checks']['cache_baseline_no_reclaim_and_damon_restored']=='PASS'
                receipt['checks']['cache_baseline_no_reclaim_and_damon_restored']='PASS'
            if python_cache_target is not None:
                assert task_result['cache_target_mib']==python_cache_target and task_result['checks']['cache_target_and_damon_restored']=='PASS'
                receipt['checks']['cache_target_and_damon_restored']='PASS'
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
            if python_schedule:
                receipt['scope']='Original seven-role Python task; explicit '+python_schedule+' scheduling, branch results and service-cgroup physical metrics'
                receipt['python_schedule']=python_schedule
                receipt['checks'].pop('xarray6992_backbone_and_separate_tests',None)
                receipt['checks']['python_seven_role_schedule_and_metrics']='PASS'
            if python_physical:receipt['scope']+='; paused physical cache points and continuous curves, analysis cost included'
            if python_live_cache:receipt['scope']+='; no-pause whole guest cache and confirmed host residency with unknown pages separate'
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
        elif exp2_mode:
            stage='exp2-four-rounds';replay=mount/'controller/prettier6604'
            call(stage,[sys.executable,'-B',ROOT/'scripts/run_exp2.py','--cache-mode',exp2_mode,
                '--container',container,'--config-home',mount/'controller/config','--service-url',receipt['address'],'--output',replay],780,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS' and task_result['action_count']==32
            assert task_result['checkpoint_count']==task_result['restore_count']==4 and all(task_result['cleanup'].values())
            receipt['exp2_result']=task_result;receipt['checks']['exp2_four_rounds_restore_state_and_continuation']='PASS'
            receipt['scope']=task_result['scope']
            stage='exp2-report'
            call(stage,[sys.executable,'-B',ROOT/'scripts/report_exp2.py',replay,mount/'controller/report'],60,environment=env)
        elif startup_sampling:
            stage='cold-start-sampling';replay=mount/'controller/prettier6604'
            call(stage,[sys.executable,'-B',ROOT/'scripts/cold_start_sampling.py',
                '--container',container,'--config-home',mount/'controller/config',
                '--service-url',receipt['address'],'--output',replay,'--mode',startup_sampling,
                *(['--server-metrics'] if getattr(args,'startup_server_metrics',False) else [])],720,environment=env)
            measured=json.loads((replay/'result.json').read_text())
            assert measured['status']=='PASS' and measured['actual_action_count']==0
            assert len(measured['guests'])==7 and measured['cleanup']['service_sandbox_list_empty']
            receipt['cold_start_sampling_result']=measured
            receipt['checks']['seven_cold_guests_sampling_only']='PASS';receipt['scope']=measured['scope']
        elif local_no_proxy_check:
            from prettier_no_proxy import IMAGE as local_image,PROXY_KEYS,check_network
            stage='local-no-proxy-create'
            info=inspect(container)
            actual_env=dict(item.split('=',1) for item in info['Config']['Env'] if '=' in item)
            assert not any(actual_env.get(k) for k in PROXY_KEYS),'private service inherited a proxy'
            sid=call(stage,[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
                '--config-home',mount/'controller/config','--service-url',receipt['address'],
                '--image',local_image,'--local-no-proxy'],610,environment=env)
            assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid)
            stage='local-no-proxy-environment'
            probe='import os; assert not any(os.environ.get(k) for k in '+repr(PROXY_KEYS)+'); print("guest-no-proxy-ready")'
            import shlex
            assert cli(stage,'exec',sid,'bash','-lc','python3 -c '+shlex.quote(probe))=='guest-no-proxy-ready'
            receipt['local_no_proxy']['guest_check']='PASS'
            stage='local-no-proxy-network'
            network=check_network(info['Id'],before,call)
            log=mount/'env/logs/local-no-proxy-regctl.log'
            registry=log.read_text().splitlines() if log.exists() else []
            assert 'version' in registry and all(x in ('version','manifest','config') for x in registry),'unexpected regctl operation'
            receipt['local_no_proxy']['registry_commands']=registry
            receipt['local_no_proxy_check']=dict(status='PASS',sandbox_id=sid,image=local_image,
                actions=0,created_guests=1,service_proxy_empty=True,guest_proxy_empty=True,
                network=network,registry_commands=registry,guest_sampling='NOT_REQUESTED')
            receipt['scope']='One local creation and metadata checks; no task, evaluation, snapshot or restore'
            receipt['checks']['local_no_proxy_creation_environment_network']='PASS'
        elif prettier6604:
            stage='prettier6604-paths' if prettier_export else 'prettier6604-backbone'
            replay=mount/'controller/prettier6604'
            task_args=['--checkpoint','--expected-official-audit',expected_audit] if prettier_checkpoint else []
            if prettier_readonly:task_args=['--monitor-mode',prettier_monitor_mode,'--expected-official-audit',expected_audit]
            if light_monitor:task_args+=['--light-monitor']
            if damon_observe:task_args+=['--damon-observe']
            if damon_disable:task_args+=['--damon-disable']
            if prettier_physical:task_args+=['--physical-cache-after',prettier_physical]
            if live_task_cache:task_args+=['--live-cache']
            if live_task_cache_lower:task_args+=['--live-cache-lower']
            runner='run_prettier6604.py'
            if prettier_export:
                runner='run_prettier6604_grpo.py'
                task_args=['--frozen-export',prettier_export,'--expected-official-audit',expected_audit,'--schedule',prettier_schedule]
                if prettier_monitor_mode=='off':task_args+=['--monitor-mode','off']
                if create_before_setup:task_args+=['--grpo-create-before-setup']
                if skip_boundary_vmstat:task_args+=['--light-skip-boundary-vmstat']
                if merge_boundary_meminfo:task_args+=['--light-merge-boundary-meminfo']
                if host_restore:task_args+=['--host-matched-task' if host_matched else '--host-restore-short']
                if host_trace_dual:task_args+=['--host-trace-dual']
                if independent_phase:task_args+=phase_arguments(independent_phase,host_bundle,getattr(args,'host_checkpoint_source',None))
                if rootfs_short:task_args+=['--host-rootfs-short']
                if prettier_role:task_args+=['--role',prettier_role]
                if prettier_cache_pair:
                    task_args+=['--snapshot-cache-pair']
                    if pair_sampling is not None:task_args+=['--snapshot-cache-sampling',pair_sampling]
                    if guest_cache_policy is not None:task_args+=['--snapshot-guest-cache-policy',guest_cache_policy]
                    if cache_target is not None:task_args+=['--snapshot-cache-target-mib',str(cache_target)]
                    if capacity_sequence:task_args+=['--retain-capacity-checkpoint']
                    if damon_before_checkpoint:task_args+=['--snapshot-damon-before-checkpoint']
                    if capacity_recovery:task_args+=['--reuse-capacity-checkpoint',recovered_source,
                        '--capacity-checkpoint-recovery',mount/'controller/capacity-recovery.json']
                    if provider_binary:task_args+=['--snapshot-provider-cache']
                    if batch_lookup:task_args+=['--snapshot-bpf-batch']
                if prettier_shared:task_args+=['--shared-cache','--root-count',str(prettier_root_count or 7)]
                if light_monitor:task_args+=['--light-monitor']
                if full_shared:task_args+=['--host-shared-files']
                if schedule_smoke:task_args+=['--schedule-smoke-metrics']
                if light_rollout:task_args+=['--light-rollout']
                if getattr(args,'schedule_expanded_files',False):task_args+=['--schedule-expanded-files']
                if getattr(args,'schedule_cache_layers',False):task_args+=['--schedule-cache-layers']
                if getattr(args,'schedule_block_cache_method',None) is not None:task_args+=['--schedule-block-cache-method',args.schedule_block_cache_method]
                if getattr(args,'schedule_split_ram_cache',False):task_args+=['--schedule-split-ram-cache']
                if getattr(args,'schedule_memory_backing_links',False):task_args+=['--schedule-memory-backing-links']
                if getattr(args,'schedule_sampling_control',False):task_args+=['--schedule-sampling-control']
                if full_rootfs_mode:task_args+=['--host-full-rootfs-mode',full_rootfs_mode]
                if full_rootfs_residency=='off':task_args+=['--host-full-rootfs-residency','off']
                if full_rootfs_cold_read:task_args+=['--host-full-rootfs-cold-read']
                if prefix_count:
                    roots_file=out/'prefix-roots.json'
                    roots_file.write_text(json.dumps([str(source/'image-cache/commits')])+'\n')
                    task_args+=['--host-prefix-count',str(prefix_count),'--host-prefix-roots',roots_file]
                if scaling:
                    task_args+=scaling_arguments(scaling,host_bundle,getattr(args,'host_checkpoint_source',None))
                    if current_pair:task_args+=['--host-current-pair']
                    if persistent_seed:task_args+=['--host-persistent-seed',str(cache_seed)]
                    if scaling=='cold':
                        roots_file=out/'prefix-roots.json'
                        roots_file.write_text(json.dumps([str(source/'image-cache/commits')])+'\n')
                        task_args+=['--host-prefix-roots',roots_file]
            if host_trace_dual:
                from host_file_trace import FileCapture
                host_trace=FileCapture(out/'file-trace',image,mount,host_group)
                host_trace.start()
            if host_matched and host_sampling=='on' and not rootfs_short:
                from host_file_monitor import backing_roots
                roots=backing_roots(source,mount,image,cache_seed=cache_seed if seed_value else None,restore=True)
                if host_bundle:roots.append(host_bundle.resolve()/'repository.tar.gz')
                roots_file=out/'host-file-roots.json'
                roots_file.write_text(json.dumps(list(map(str,roots)),indent=2)+'\n')
                task_args+=['--host-file-roots',roots_file]
            call(stage,[sys.executable,'-B',ROOT/'scripts'/runner,
                 '--official-task',prettier6604,'--container',container,
                 '--config-home',mount/'controller/config','--service-url',receipt['address'],
                 '--output',replay,*task_args],900,environment=env)
            task_result=json.loads((replay/'result.json').read_text())
            assert task_result['status']=='PASS'
            if local_no_proxy:
                assert task_result['local_no_proxy']=='PASS'
                receipt['local_no_proxy']['guest_check']='PASS'
                log=mount/'env/logs/local-no-proxy-regctl.log'
                receipt['local_no_proxy']['registry_commands']=log.read_text().splitlines() if log.exists() else []
            if full_shared:assert task_result['host_shared_files']['status']=='OBSERVED'
            if full_rootfs_mode:
                from host_full_rootfs_control import validate_observation
                validate_observation(task_result['host_full_rootfs_control'],full_rootfs_residency)
            receipt['prettier6604_result']=task_result
            if prettier_export:
                replay_check='sampled_actions_order_exit_codes_and_distinct_instances' if prettier_schedule!='grpo' or scaling=='restore' else 'sampled_actions_order_exit_codes_and_distinct_roots'
                assert task_result['checks'][replay_check]=='PASS'
                if independent_phase=='capture':
                    assert task_result['checks']['checkpoint_saved_at_step_11']=='PASS'
                    assert task_result['official_evaluation']=='NOT_RUN_PREFIX_ONLY'
                elif host_matched:
                    assert task_result['host_matched']['status']=='PASS'
                    assert task_result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
                elif host_restore:
                    assert task_result['host_restore']['status']=='PASS' and task_result['official_evaluation']=='NOT_RUN_PREFIX_ONLY'
                elif prefix_count:
                    assert task_result['official_evaluation']=='NOT_RUN_PREFIX_ONLY'
                elif scaling:
                    assert task_result['host_scaling']['status']=='PASS'
                    if scaling=='cold':assert task_result['official_evaluation']=='NOT_RUN_PREFIX_ONLY'
                    else:
                        assert task_result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
                        assert task_result['checks']['restored_files_progress_and_process']=='PASS'
                        assert len(task_result['cleanup']['snapshots_removed'])==1
                else:assert task_result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
                expected_count=(39 if prettier_role else 84) if prettier_schedule=='tvcache' else 84 if prettier_schedule=='bpo' and not prettier_role else 189 if not prettier_role else 21 if prettier_role.startswith('early') else 33 if prettier_role=='late_3' else 32
                if prefix_count:expected_count=11*prefix_count
                if prettier_shared:expected_count={1:29,2:50,7:189}[prettier_root_count or 7]
                if prettier_cache_pair:expected_count=31
                if host_restore:expected_count=31 if host_trace_dual else 41 if host_matched else 11
                if independent_phase:expected_count={'capture':11,'single':10,'dual':20}[independent_phase]
                if capacity_recovery:expected_count=20
                if scaling:expected_count=22 if current_pair else 77 if scaling=='cold' else 70
                assert task_result['action_count']==expected_count
                if prettier_schedule in ('bpo','tvcache'):
                    if independent_phase!='capture':assert task_result['checks']['restored_files_progress_and_process']=='PASS'
                    removed_key='snapshots_retained_for_export' if independent_phase=='capture' else 'snapshots_retained_for_reuse' if capacity_sequence else 'snapshots_removed'
                    assert len(task_result['cleanup'][removed_key])==(1 if prettier_role or prettier_cache_pair or host_restore else 2)
                if prettier_schedule=='tvcache':assert task_result['checks']['deterministic_prefix_tree_order']=='PASS'
                assert task_result['cleanup']['service_sandbox_list_empty']
                label='prettier6604_bpo_restore_smoke' if prettier_role else 'prettier6604_corrected_bpo'
                if prettier_schedule=='grpo':label='prettier6604_corrected_branch' if prettier_role else 'prettier6604_corrected_grpo'
                if prettier_schedule=='tvcache':label='prettier6604_tvcache_restore_smoke' if prettier_role else 'prettier6604_corrected_tvcache'
                if prettier_cache_pair:label='prettier6604_two_restored_cache_branches'
                if host_restore:label='host_file_one_and_two_restored_guests'
                if independent_phase:label='host_independent_'+independent_phase
                if prefix_count:label='host_same_prefix_'+str(prefix_count)
                if scaling:label='host_scaling_'+scaling
                receipt['checks'][label]='PASS'
                receipt['scope']=task_result['scope']+'; platform consistency PASS; official task results reported separately; not performance comparison'
                if capacity_recovery:
                    assert task_result['actual_action_count']==20 and task_result['reused_action_count']==11
                    checkpoint=json.loads((recovered_source/'checkpoint-11.json').read_text())
                    assert task_result['cleanup']['snapshots_removed']==[checkpoint['template']]
                    receipt['checks']['recovered_original_checkpoint_only_128_suffixes']='PASS'
                if capacity_sequence:
                    stage='prettier6604-reused-capacity-128'
                    if provider_binary:
                        from capacity_checkpoint_recovery import archive_released_slots
                        receipt['released_provider_slots']=archive_released_slots(mount/'env/provider-slots',replay/'released-provider-slots')
                    second=mount/'controller/prettier6604-128'
                    second_args=task_args.copy();second_args.remove('--retain-capacity-checkpoint')
                    second_args[second_args.index('--snapshot-cache-target-mib')+1]='128'
                    call(stage,[sys.executable,'-B',ROOT/'scripts'/runner,
                        '--official-task',prettier6604,'--container',container,
                        '--config-home',mount/'controller/config','--service-url',receipt['address'],
                        '--output',second,*second_args,'--reuse-capacity-checkpoint',replay],600,environment=env)
                    next_result=json.loads((second/'result.json').read_text())
                    assert next_result['status']=='PASS' and all(v=='PASS' for v in next_result['checks'].values())
                    assert (next_result['action_count'],next_result['actual_action_count'],next_result['reused_action_count'],next_result['verified_action_count'])==(20,20,11,31)
                    assert task_result['actual_action_count']==31
                    assert next_result['cleanup']['snapshots_removed']==task_result['cleanup']['snapshots_retained_for_reuse']
                    assert next_result['cleanup']['service_sandbox_list_empty']
                    receipt['capacity_sequence_results']={'64':task_result,'128':next_result}
                    receipt['checks']['one_checkpoint_64_then_128_actual_actions_51']='PASS'
                    receipt['scope']='One actual step-11 checkpoint, 64 then 128 MiB; 51 actual actions; four restored children; original checks retained'
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
            if exp1_light:
                cfg['light_monitor']=True
                cfg['expected_run_id']=out.name
                cfg_path.write_text(json.dumps(cfg,indent=2)+'\n')
            call('exp1-checkpoint-workflow',[sys.executable,'-B',ROOT/'scripts/run_exp1_checkpoint.py',cfg_path],300,environment=env)
            audits=list((replay/'audit').iterdir());assert len(audits)==1
            audit=audits[0]
            checks=json.loads((audit/'checks.json').read_text())
            assert json.loads((audit/'result.json').read_text())['status']=='PASS'
            required=['input_hashes','replay_correctness','patch_matches_full_replay',
                      'independent_svg_properties','official_task_tests','cleanup']
            required+=['guest_light_sampling','uploaded_action_hashes'] if exp1_light else ['host_sampling']
            if boundary:required+=['process_continuity','restored_progress_and_files']
            for key in required:
                assert checks.get(key)=='PASS', 'missing Exp1 check: '+key
            if boundary:
                call('exp1-phase-report',[sys.executable,'-B',ROOT/'scripts/report_exp1_checkpoint.py',audit],30,environment=env)
                receipt['exp1_report']=json.loads((audit/'phase-breakdown.json').read_text())
            receipt['exp1_checks']=checks
            if exp1_light:
                exp1_light_raw=Path(json.loads((audit/'result.json').read_text())['raw_dir'])
                receipt['exp1_light']=dict(status='PASS',raw_relative=str(exp1_light_raw.relative_to(replay)),
                    clock='monotonic_ns',guest_interval_seconds=.1,host_pss=False,mincore=False)
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
        if local_no_proxy_check:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='Short metadata-only check; no guest sampler or task curves')
        elif external_config:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='External guest and host lightweight reports are generated from saved output after cleanup')
        elif startup_sampling:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='Startup-only timing, no task replay, official tests or fabricated task curves')
        elif exp1_light:
            stage='default-runtime-report'
            call(stage,[sys.executable,'-B',ROOT/'scripts/report_sandbox_runtime.py',exp1_light_raw,mount/'controller/report'],60,environment=env)
            receipt['default_runtime_report']=dict(status='PASS',directory=str(out/'report'),scope='Exp1 guest light curves; host current reported by outer workflow')
        elif density:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='Density keeps each original guest raw report input; batch publisher creates per-run host and guest curves')
        elif host_restore:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='phase-specific host restore report generated by outer workflow; guest light samples retained')
        elif host_pair_smoke:
            receipt['default_runtime_report']=dict(status='NOT_REQUESTED',scope='host-only short pair; guest meminfo snapshots, no benchmark replay or periodic guest collector')
        elif prettier6604 and prettier_monitor_mode=='continuous' or python_inputs and not (python_cache_smoke or python_command_diagnostic) and python_monitor_mode=='continuous':
            stage='default-runtime-report'
            report_script='report_task_overview.py' if light_monitor and prettier_export else 'report_sandbox_runtime.py'
            call(stage,[sys.executable,'-B',ROOT/'scripts'/report_script,replay,mount/'controller/report'],60,environment=env)
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
                membership=(proc/'cgroup').read_text()
                if vmm_check:
                    parent=receipt['vmm_child_cgroups']['parent'].removeprefix('/sys/fs/cgroup')
                    if membership!=cg and not any(l.startswith('0::'+parent+'/vmm-') for l in membership.splitlines()):continue
                elif membership!=cg:continue
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
        if vmm_check and os.environ.get('ASB_VMM_ACCOUNTING_ONLY')=='on' and not light_rollout:
            from vmm_child_cgroup_check import accounting_acceptance
            stage='experiment-memory-closure-acceptance'
            series=Path(os.environ['ASB_EXPERIMENT_MEMORY_SERIES'])
            with series.open() as stream:
                samples=[json.loads(line) for line in stream if line.endswith('\n')]
            metrics=json.loads((mount/'controller/prettier6604/schedule-smoke-metrics.json').read_text())
            receipt['experiment_memory_acceptance']=accounting_acceptance(metrics,samples)
            assert receipt['experiment_memory_acceptance']['status']=='PASS', 'experiment memory closure failed; retain volume'
        success=True
    except BaseException as exc:
        receipt['error']=repr(exc);receipt['failed_stage']=stage
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        # Only this labelled container, mount and loop. Never terminate the current service.
        for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,signal.SIG_IGN)
        cleaning=True
        cleanup={}
        if vmm_sampler is not None:
            try:receipt['vmm_cgroup_sampling']=vmm_sampler.stop()
            except Exception as error:cleanup['vmm_cgroup_sampler_error']=repr(error);success=False
        if delete_trace is not None:
            try:
                receipt['delete_trace']=delete_trace.stop()
                if receipt['delete_trace']['status']!='PASS':success=False
            except Exception as error:cleanup['delete_trace_error']=repr(error);success=False
        if host_trace is not None:
            try:
                receipt['host_file_trace']=host_trace.stop(matched=mount/'controller/prettier6604/host-matched.json')
                covered=receipt['host_file_trace']['coverage_status']=='MATCHES_QUERY_BOUNDS'
                receipt['checks']['host_shared_device_event_coverage']='PASS' if covered else 'FAIL'
                if not covered:success=False
            except Exception as error:
                receipt['host_file_trace']=dict(status='FAIL',error=repr(error));success=False
        if sampler:
            sampler_stop.set();sampler.join(timeout=5)
            if sampler.is_alive() or sample_errors:
                cleanup['disk_sampler_error']=sample_errors or ['sampler did not exit'];success=False
        try:
            creation_unresolved=False
            if creation_attempted and not container:
                container=late_owned_container(out.name,call)
                creation_unresolved=container is None
                if creation_unresolved:success=False
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
            if host_group and mounted and host_sampling=='on' and not (private_control or full_rootfs_mode or host_current_only or current_pair or os.environ.get('ASB_HOST_EXPANDED_FILES')=='on'):
                # Stop our disk sampler and service writers before checking the loop images.
                # The images remain mounted and available; there is no VM sampling pause.
                try:
                    from host_file_monitor import backing_roots, residency_check
                    roots=backing_roots(source,mount,image,cache_seed=cache_seed if seed_value else None,restore=host_restore)
                    if scaling:
                        if scaling=='restore':roots += [mount/'env/snapshot-store',mount/'env/snapshot-local-cache']
                        roots.append(mount/'env/firecracker-work')
                    if host_bundle:roots.append(host_bundle.resolve()/'repository.tar.gz')
                    final_options={}
                    if independent_phase or light_monitor and prettier_export:
                        # Scan inner files first. Their metadata lookups may dirty the owned FS.
                        # Flush only that FS, then query its backing image last without remounting.
                        roots=[path for path in roots if path!=image]+[image]
                        final_options['before_last_file']=lambda:call('flush-own-filesystem',['sync','-f',mount],60)
                    receipt['host_file_residency']=residency_check(host_group,
                        roots,out/'host-file-residency.json',**final_options)
                    cleanup['host_file_residency_captured']=receipt['host_file_residency']['status']=='OBSERVED'
                    if not cleanup['host_file_residency_captured']:success=False
                except Exception as exc:
                    cleanup['host_file_residency_error']=repr(exc);success=False
            elif host_current_only or current_pair or os.environ.get('ASB_HOST_EXPANDED_FILES')=='on':
                receipt['host_file_residency']=dict(status='NOT_REQUESTED',scope='Explicit current-only replay; no final residency inventory requested')
            elif private_control or full_rootfs_mode:
                receipt['host_file_residency']=dict(status='NOT_REQUESTED',scope='Explicit P0/P1 pinned-file queries owned by private-control coordinator; no broad cleanup scan')
            elif host_group and host_sampling=='off':
                receipt['host_file_residency']=dict(status='NOT_REQUESTED',host_sampling='off')
            if container:
                call('remove-own-server',['docker','rm',container],30)
            cleanup['owned_container_removed']=not creation_unresolved
            if mounted and success and independent_phase=='capture':
                from host_independent_task import export_prefix
                try:receipt['checkpoint_export']=export_prefix(mount/'env/snapshot-store',out/'checkpoint-bundle',mount/'controller/prettier6604')
                except BaseException as exc:
                    cleanup['checkpoint_export_error']=repr(exc);receipt['error']=repr(exc);receipt['failed_stage']='checkpoint-export';success=False
                    (out/'checkpoint-export-failure.txt').write_text(traceback.format_exc())
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
                if density:
                    density_output=mount/'controller/density'
                    if density_output.exists():shutil.copytree(density_output,out/'density')
                    for guest_output in sorted((mount/'controller').glob('density-guest-*')):
                        shutil.copytree(guest_output,out/'density'/guest_output.name)
                pair_output=mount/'controller/host-file-pair'
                if pair_output.exists():shutil.copytree(pair_output,out/'host-file-pair')
                creation_output=mount/'controller/external-creation'
                if creation_output.exists():shutil.copytree(creation_output,out/'external-creation')
                external_output=mount/'controller/external-workload'
                if external_output.exists():shutil.copytree(external_output,out/'external-workload')
                second_capacity=mount/'controller/prettier6604-128'
                if second_capacity.exists():shutil.copytree(second_capacity,out/'prettier6604-128')
                if capacity_recovery:
                    for name in ('prettier6604-prefix-source','prettier6604-128-failed'):
                        shutil.copytree(mount/'controller'/name,out/name)
                python_output=mount/'controller/python-backbone'
                if python_output.exists():
                    shutil.copytree(python_output,out/'python-backbone')
                    receipt['preserved_python_backbone_dir']=str(out/'python-backbone')
                cache_output=mount/'controller/guest-cache'
                if cache_output.exists():shutil.copytree(cache_output,out/'guest-cache')
                runtime_report=mount/'controller/runtime-report'
                if runtime_report.exists():shutil.copytree(runtime_report,out/'runtime-report')
                default_report=mount/'controller/report'
                if default_report.exists():shutil.copytree(default_report,out/'report')
                for name in ['logs','provider-slots']:
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
            if success and getattr(args,'experiment_memory_standard',False):
                # Validate saved measurements before removing a successful volume.
                # Failure retains the now-unmounted fixture, not live guests.
                try:
                    from report_experiment_schedule import collect_parent
                    checked=collect_parent(out,creation_receipt=receipt)
                    receipt['experiment_memory_acceptance']=checked
                    if checked['experiment_scope_validation']!='PASS':
                        success=False
                        receipt['failed_stage']='experiment-memory-acceptance'
                        receipt['error']=str(checked['experiment_scope_errors'])
                except Exception as error:
                    success=False
                    receipt['failed_stage']='experiment-memory-acceptance'
                    receipt['error']=repr(error)
            finish_fixture(image,success,receipt,cleanup,retain_for_final_acceptance=retain_fixture)
        except BaseException as exc:
            cleanup['error']=repr(exc);success=False
        after=identities()
        (out/'firecracker-after.json').write_text(json.dumps(after,indent=2)+'\n')
        cleanup['original_firecracker_identities_unchanged']=before==after
        if online_agent or online_task or rootfs_short or private_control or full_rootfs_mode or os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':
            options=dict(allow_empty=True) if rootfs_short or private_control or full_rootfs_mode or os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on' else {}
            cleanup['original_source_firecracker_identities_unchanged']=source_vm_identities_unchanged(before,after,receipt.get('original_service_cgroup','UNAVAILABLE'),**options)
            receipt['external_firecracker_changes']={'removed':[v for v in before if v not in after],
                'added':[v for v in after if v not in before],
                'scope':'observed host VM churn; functional validation checks owned/source resources, not host exclusivity or causal performance'}
            if not cleanup['original_source_firecracker_identities_unchanged']:success=False
        elif before!=after:success=False
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
    parser.add_argument('--light-rollout',action='store_true',help='Explicit BPO light rounds, closure diagnostic only')
    parser.add_argument('--diagnostic-server-binary',type=Path,help='Explicit child-cgroup light restore only; read-only private /server override')
    parser.add_argument('--window-start-utc',help='Optional runtime start with timezone; provide together with end')
    parser.add_argument('--window-end-utc',help='Optional runtime end; by default use a new bounded 20-minute runtime')
    parser.add_argument('--platform-config',type=Path,help='Validated unified isolated Exp1 config')
    parser.add_argument('--diagnostic-guest-kernel',type=Path,help='Local experimental ELF copied only into the new private service')
    parser.add_argument('--guest-cache-metadata-probe',action='store_true',help='12 GiB private volume; whole-cache metadata short validation, no checkpoint or VM pause')
    parser.add_argument('--guest-cache-restored-lower',action='store_true',help='Explicit restored cache session lower-layer query')
    parser.add_argument('--guest-cache-unified-rounds',action='store_true',help='Explicit cache-session shared clock only')
    parser.add_argument('--immediate-restores',action='store_true')
    parser.add_argument('--seven-restored',action='store_true')
    parser.add_argument('--paused-native',action='store_true',help='One restored VMM, in-place pause and native page collector dependency check')
    parser.add_argument('--guest-cache-rollout-sessions',action='store_true',help='One source plus one restored guest with page-cache sessions, explicit only')
    parser.add_argument('--guest-cache-light-restore-one',action='store_true',help='Explicit seven-light-guest single save/restore')
    parser.add_argument('--guest-cache-light-memory',action='store_true',help='Explicit two-guest light memory rounds; no page probes')
    parser.add_argument('--guest-cache-child-calibration',action='store_true',help='Explicit private-VMM child-cgroup calibration; local Prettier image, unlocked file, no checkpoint or pause')
    parser.add_argument('--guest-cache-child-count',type=int,choices=(1,2,7),default=1,help='Explicit private cache calibration only; two guests use 16 GiB, default one guest unchanged')
    parser.add_argument('--guest-cache-dynamic-file',action='store_true',help='Private children only: current file cache write/evict/reread calibration; ordinary paths unchanged')
    parser.add_argument('--guest-cache-ram-page-evidence',action='store_true',help='Explicit private child calibration only: persist already-read full RAM pagemap triples; default off')
    parser.add_argument('--guest-cache-vmm-page-evidence',action='store_true',help='Explicit private child calibration with RAM evidence only: full VMM maps/smaps and non-RAM pagemap triples; default off')
    parser.add_argument('--guest-cache-io-uring-page-evidence',action='store_true',help='Explicit VMM/RAM evidence only: restricted io_uring mixed mappings; default off')
    parser.add_argument('--guest-cache-host-backing-charge-evidence',action='store_true',help='Explicit private page charge attribution; default off')
    parser.add_argument('--guest-cache-host-backing-full-pfn-evidence',action='store_true',help='Explicit one-private-guest full-list PFNs; default off')
    parser.add_argument('--retain-fixture-for-final-acceptance',action='store_true',help='Private full-list calibration only: retain the unmounted volume for final acceptance and explicit later cleanup')
    parser.add_argument('--guest-cache-host-backing-pfn-evidence',action='store_true',help='One private guest only: fixed bounded first-lower PFN query; default off')
    parser.add_argument('--guest-cache-host-backing-evidence',action='store_true',help='Explicit private child calibration only: endpoint host objects and references, default off')
    parser.add_argument('--guest-cache-capacity-probe',action='store_true',help='12 GiB private volume; one guest, 128/64/128 MiB periodic clean-file reclaim targets; diagnostic reclaim kernel required')
    parser.add_argument('--snapshot-cache-pair',action='store_true',help='One two-guest shared snapshot / divergent write calibration')
    parser.add_argument('--snapshot-lower-cache-calibration',action='store_true',help='One raw snapshot-layer residency calibration, metadata reads only')
    parser.add_argument('--snapshot-provider-binary',type=Path,help='Owned diagnostic daemon; one short restore calibration with slot metadata')
    parser.add_argument('--snapshot-provider-get-data',action='store_true',help='Opt-in GET_DATA only for the owned observed memory device')
    parser.add_argument('--snapshot-bpf-batch',action='store_true',help='Private provider calibration only; read-only batch BPF queries')
    parser.add_argument('--snapshot-cache-calibration',action='store_true',help='24 GiB private volume; one 16 MiB file restore calibration, no sampler pause or prefetch')
    parser.add_argument('--exp2-cache-mode',choices=['preserve','drop'],help='Original four-round Prettier workload; 32 GiB volume and guest-only cache control')
    parser.add_argument('--python-backbone-inputs',type=Path,help='Prepared pinned Xarray backbone; volume budget depends on selected mode')
    parser.add_argument('--prettier6604-readonly-replay',action='store_true',help='8 GiB single backbone; converted immutable commits remain read-only in verified RAM cache')
    parser.add_argument('--prettier6604-monitor-mode',choices=('continuous','off'),default='continuous')
    parser.add_argument('--prettier6604-grpo-create-before-setup',action='store_true',help='Guest-only GRPO: complete every create before concurrent setup')
    parser.add_argument('--light-skip-boundary-vmstat',action='store_true',help='Sampled create-before-setup or local current GRPO; preserve memory and skip boundary vmstat')
    parser.add_argument('--light-merge-boundary-meminfo',action='store_true',help='Explicit local current GRPO with skipped vmstat; merge boundary meminfo into existing timer Python calls')
    parser.add_argument('--startup-sampling',choices=('on','off'),help='Seven-guest startup-only sampling control; ordinary seeded 16 GiB GRPO environment')
    parser.add_argument('--startup-server-metrics',action='store_true',help='Two service metric snapshots in the explicit startup sampling experiment')
    parser.add_argument('--light-monitor',action='store_true',help='Complete task: guest meminfo at 0.1 s, no host or CPU/I/O collectors')
    parser.add_argument('--local-no-proxy-check',action='store_true',help='Explicit local Prettier only: create one guest and inspect proxy/network metadata, no task')
    parser.add_argument('--damon-observe',action='store_true',help='Light Prettier only: read default DAMON state/counters without writes')
    parser.add_argument('--damon-disable',action='store_true',help='Observed light Prettier only: disable owned guest DAMON for task and restore after sampling')
    parser.add_argument('--host-prefix-count',type=int,choices=(1,7))
    parser.add_argument('--density-request',type=Path,help='Explicit identical-backbone shared/independent density experiment; ordinary defaults unchanged')
    parser.add_argument('--host-scaling-mode',choices=('cold','restore'))
    parser.add_argument('--host-current-pair',action='store_true')
    parser.add_argument('--host-file-monitor',action='store_true',help='Fresh host workflow slice; file main curve, PSS and pre-cleanup backing mincore')
    parser.add_argument('--host-current-only',action='store_true',help='Explicit light full task, frozen tool replay or Exp1: skip final backing residency inventory')
    parser.add_argument('--experiment-memory-standard',action='store_true',help='Check saved slice closure before deleting the successful private volume')
    parser.add_argument('--external-creation-controls',action='store_true',help='Fixed Django: one preparation and four host-only creation controls in one service; no workload')
    parser.add_argument('--external-creation-oci-input',type=Path,help='Explicit creation controls only: complete fixed Django disk OCI input, mounted read-only')
    parser.add_argument('--external-delete-trace',action='store_true',help='Same binary, private two-guest C4 deletion diagnostics')
    parser.add_argument('--external-guest-count',type=int,choices=(1,2),default=1,help='Explicit concurrent C4 sampling control')
    parser.add_argument('--external-sampling-control',action='store_true',help='Explicit C4 sampling on/off comparison')
    parser.add_argument('--external-workload-relay',action='store_true',help='Explicit upgraded Cython owned relay')
    parser.add_argument('--task-endpoint-metrics',action='store_true',help='Complete single Python/external task: VMM PSS, lower P1 and task window')
    parser.add_argument('--vmm-pss-interval-seconds',type=validate_pss_interval,default=1,help='External endpoint metrics only: periodic VMM PSS interval; default 1s')
    parser.add_argument('--external-workload-config',type=Path,help='Pinned public Django fixed-tool replay; separate 32 GiB private service volume')
    parser.add_argument('--c4-schedule',choices=('grpo','bpo','tvcache'),help='Approved C4 same-path seven-instance prefix reuse; 104 GiB gate for this mode only')
    parser.add_argument('--c4-schedule-inputs',type=Path)
    parser.add_argument('--matplotlib-schedule',choices=('grpo','bpo','tvcache'),help='Approved Matplotlib 73/87 prefix reuse, seven guests; displayed ~104 GiB gate')
    parser.add_argument('--matplotlib-schedule-inputs',type=Path)
    parser.add_argument('--cython-schedule',choices=('grpo','bpo','tvcache'),help='Approved original Cython 60/75 prefix reuse; seven guests, 64 GiB volume')
    parser.add_argument('--cython-schedule-inputs',type=Path)
    parser.add_argument('--text-editing-schedule',choices=('grpo','bpo','tvcache'),help='Approved original text editing 33/46 prefix reuse; seven guests, 64 GiB volume')
    parser.add_argument('--text-editing-schedule-inputs',type=Path)
    parser.add_argument('--django-schedule',choices=('grpo','bpo','tvcache'),help='Explicit approved Django original-path prefix reuse, seven guests')
    parser.add_argument('--django-schedule-inputs',type=Path,help='Prepared pinned Django original scripts and declared prefix boundaries')
    parser.add_argument('--external-workload-upstream',type=Path,help='Checked-out pinned AgentSandboxWorkloadResults repository')
    parser.add_argument('--external-workload-oci-input',type=Path,help='Two new tasks only: fixed local OCI layout, mounted read-only')
    parser.add_argument('--external-workload-sampling',choices=('on','off'),default='on',help='C4/Django/Cython explicit host and guest memory sampling control')
    parser.add_argument('--exp1-light',action='store_true',help='Explicit full Exp1 light sampler and host current; old Exp1 remains unchanged')
    parser.add_argument('--host-shared-files',action='store_true',help='Full light GRPO/BPO: selected shared-file query before official checks')
    parser.add_argument('--host-full-rootfs-mode',choices=('shared','private'),help='Full GRPO/BPO: fixed initial 19 lower-file copy control')
    parser.add_argument('--host-full-rootfs-residency',choices=('on','off'),default='on')
    parser.add_argument('--host-full-rootfs-cold-read',action='store_true',help='Full GRPO only: cold initial target files and background residency curve')
    parser.add_argument('--host-full-rootfs-daemon',type=Path,help='Explicit experiment daemon, copied only into the new private service')
    parser.add_argument('--host-trace-dual',action='store_true',help='Only two matched restores with targeted cache tracing')
    parser.add_argument('--host-independent-phase',choices=('capture','single','dual'))
    parser.add_argument('--host-checkpoint-bundle',type=Path)
    parser.add_argument('--host-checkpoint-source',type=Path)
    parser.add_argument('--host-matched-task',action='store_true',help='Identical frozen early_1 suffix in N=1/N=2 restores; 41 actual actions')
    parser.add_argument('--host-rootfs-short',action='store_true',help='Imported dual suffix: selected shared files only, no PSS')
    parser.add_argument('--host-restore-short',action='store_true',help='Ordinary-kernel snapshot, N=1/N=2 five-second observations, no suffix')
    parser.add_argument('--host-file-pair-smoke',action='store_true',help='Only two cold guests and tiny synthetic markers; no Prettier replay')
    parser.add_argument('--prettier6604-snapshot-cache-pair',action='store_true',help='24 GiB, one snapshot and early_1/early_2 actual suffixes; no-pause layer PFNs')
    parser.add_argument('--prettier6604-snapshot-cache-sampling',choices=('on','off'),help='Actual pair only; disable the cache sampler for a matched timing control')
    parser.add_argument('--prettier6604-guest-cache-policy',choices=('preserve','drop-clean'),help='Sampled actual pair only; explicit guest-only initial file-cache intervention')
    parser.add_argument('--prettier6604-cache-target-mib',type=int,choices=(64,128),help='Sampled real pair only; continuous clean-file reclaim target, private reclaim kernel required')
    parser.add_argument('--prettier6604-capacity-sequence',action='store_true',help='64 MiB actual pair followed by 128 MiB from the same retained checkpoint; one prefix only')
    parser.add_argument('--prettier6604-damon-before-checkpoint',action='store_true',help='Capacity checkpoint comparison only; stop parent DAMON before checkpoint and restore its original state after suffixes')
    parser.add_argument('--prettier6604-capacity-recovery',type=Path,help='Verified stopped failed sequence volume backup; run only its unfinished 128 MiB suffixes')
    parser.add_argument('--prettier6604-live-cache-lower',action='store_true',help='Query raw snapshot memory-layer PFNs after step-24 restore')
    parser.add_argument('--prettier6604-live-cache',action='store_true',help='No-pause whole-cache diagnostic collection during complete replay')
    parser.add_argument('--prettier6604-physical-cache-after',default='',help='Completed segments for paused physical file-cache measurement')
    parser.add_argument('--prettier6604-shared-cache',action='store_true')
    parser.add_argument('--prettier6604-root-count',type=int,choices=[1,2,7])
    parser.add_argument('--python-backbone-oci',type=Path,help='Verified read-only local OCI cache; native conversion unchanged')
    parser.add_argument('--python-backbone-capacity-gib',type=int,choices=[18,20,22,24,28,32],default=32,help='Adjusted native volume requires verified OCI tar-size budget; 18/20/22 only for Sphinx')
    parser.add_argument('--python-task-profile',choices=['xarray6992','sphinx7590'],default='xarray6992')
    parser.add_argument('--python-schedule',choices=['grpo','bpo','tvcache'],help='Explicit original seven-role Python matrix; ordinary backbone unchanged')
    parser.add_argument('--python-branch-pipeline-policy',choices=['original','early2-dual-output-v1'],default='original',
                        help='User-approved Sphinx early_2_inspect/004 exact conditional-output policy; ordinary default unchanged')
    parser.add_argument('--python-schedule-find-exit-diagnostic',action='store_true',
                        help='Explicit Sphinx seven-role trace_cpp_parser/005 exact-output 0/1 policy; strict FAIL retained')
    parser.add_argument('--python-schedule-xarray-find-exit-diagnostic',action='store_true',
                        help='Explicit Xarray seven-role trace_index_state/001 exact-output 0/1 policy; strict FAIL retained')
    parser.add_argument('--python-live-cache',action='store_true',help='No-pause whole-cache collection during complete Python task replay')
    parser.add_argument('--python-cache-baseline',action='store_true',help='Live Python replay only; disable owned DAMON, observe without reclaim and restore original state')
    parser.add_argument('--python-cache-target-mib',type=int,help='Explicit complete live Python replay only; positive periodic clean-file target in MiB')
    parser.add_argument('--python-diagnostic-only',action='store_true',help='Owned 2 GiB find diagnostic, not replay acceptance')
    parser.add_argument('--python-diagnostic-probe',choices=['find','pipeline'],default='find')
    parser.add_argument('--python-readonly-replay',action='store_true',help='8 GiB write volume; verified task-converted commits bound read-only from RAM seed')
    parser.add_argument('--python-persistent-sampling',action='store_true',help='Explicit single-process Exp3 sampler variant')
    parser.add_argument('--python-physical-cache-after',default='',help='Completed Python-task segments for paused physical file-cache measurement')
    parser.add_argument('--python-sphinx-pipeline-policy-version',type=int,choices=(1,2),default=1)
    parser.add_argument('--python-sphinx-pipeline-diagnostic',action='store_true',help='Explicit approved Sphinx first-step diagnostic; strict FAIL retained')
    parser.add_argument('--python-monitor-mode',choices=['continuous','off'],default='continuous',help='Off control requires complete readonly Python replay; tool timing and correctness checks retained')
    parser.add_argument('--python-diagnostic-continuation',action='store_true',help='User-approved exact-output exception for one pinned SIGPIPE; not strict replay PASS')
    parser.add_argument('--python-evaluation-patch',type=Path,help='Independent evaluation from archived actual 35-step patch; 2 GiB write volume')
    parser.add_argument('--python-checkpoint',action='store_true',help='Snapshot after Xarray step 22 or Sphinx step 29; 16 GiB isolated write volume')
    parser.add_argument('--python-expected-evaluation-raw',type=Path,help='Complete archived evaluation raw for restored outcome comparison')
    parser.add_argument('--python-runtime-smoke',action='store_true',help='Two VMs, continuous guest sampling, six frozen Sphinx actions and one snapshot/restore')
    parser.add_argument('--python-find-diagnostic',action='store_true',help='Runtime smoke captures original find exits and suppressed errors before/after restore; no continuation replay acceptance')
    parser.add_argument('--python-command-diagnostic',action='store_true',help='13 original Sphinx actions and repeated slow-command CPU/I/O observations; no full replay')
    parser.add_argument('--python-env-command-timing',choices=('whole','split'),help='Separate 11-action Sphinx prefix and one first-use environment timing diagnostic')
    parser.add_argument('--python-durable-checkpoint-demo',action='store_true',help='Native POSIX repository export/import and full state continuity; 8 GiB functional fixture')
    parser.add_argument('--python-durable-checkpoint-bundle',type=Path,help='Verified offline bundle to import into a fresh private service')
    parser.add_argument('--python-tool-task-config',type=Path)
    parser.add_argument('--python-tool-capture-demo',action='store_true',help='Four actual tool calls captured and replayed in a fresh sandbox; no model inference')
    parser.add_argument('--python-tool-replay-source',type=Path,help='Frozen tool trajectory; no model call or new capture')
    parser.add_argument('--online-task-config',type=Path)
    parser.add_argument('--online-replay-source',type=Path)
    parser.add_argument('--online-physical-after',type=int,nargs='+')
    parser.add_argument('--python-online-agent-config',type=Path,help='Host-only model config for real captured tools and frozen replay')
    parser.add_argument('--python-cache-smoke',action='store_true',help='Three short Sphinx six-step cases; sampling and paused physical-cache calibration')
    parser.add_argument('--python-cache-reference',type=Path,help='Successful complete Sphinx raw; exact prefix outputs remain frozen')
    parser.add_argument('--python-readonly-fixture',type=Path,help='Stopped failed fixture; mount source read-only and share immutable commits')
    parser.add_argument('--prettier6604-task',type=Path,help='Prepared pinned official task; run one frozen backbone after smoke')
    parser.add_argument('--prettier6604-checkpoint',action='store_true',help='24 GiB fixture, checkpoint after 24/29, preserve original task failure')
    parser.add_argument('--prettier6604-export',type=Path,help='Role-specific frozen export; run GRPO, BPO or TVCache paths')
    parser.add_argument('--prettier6604-role',choices=['early_1','early_2','early_3','late_1','late_2','late_3'],help='GRPO branch 20 GiB, BPO/TVCache early_1 restore smoke 24 GiB; omit for full 32 GiB schedule')
    parser.add_argument('--prettier6604-schedule',choices=['grpo','bpo','tvcache'],default='grpo',help='BPO/TVCache uses 24 GiB restore smoke or 32 GiB full schedule')
    parser.add_argument('--schedule-sampling-control',action='store_true',help='Explicit GRPO sampling pair, same local-only backend')
    parser.add_argument('--schedule-block-cache-method',choices=('mincore','cachestat','compare'),help='Explicit block cache method; default mincore; compare queries both per pinned object; requires cache layers')
    parser.add_argument('--schedule-memory-backing-links',action='store_true',help='Opt-in saved memory config and lower identity/query links; requires cache layers')
    parser.add_argument('--schedule-split-ram-cache',action='store_true',help='Extra preplanned RAM cachestat; requires schedule cache layers and explicit cachestat')
    parser.add_argument('--schedule-cache-layers',action='store_true',help='Opt-in block/RAM, deleted TMPFS, private run/tmp and outer loop caches; requires expanded schedule files')
    parser.add_argument('--schedule-expanded-files',action='store_true',help='Opt-in endpoint regular-file inventory with explicit unknowns; requires schedule smoke metrics')
    parser.add_argument('--schedule-smoke-metrics',action='store_true',help='Explicit full-profile local-only three-schedule smoke; ordinary defaults unchanged')
    parser.add_argument('--prettier6604-grpo-capacity-gib',type=int,choices=[16,32],default=32,help='16 GiB only for seven GRPO roots using verified converted seed; retains host reserve and >8 GiB post-copy headroom')
    parser.add_argument('--immutable-cache-seed',type=Path,help='Verified read-only local backup cache for Prettier, Xarray or Sphinx paths')
    parser.add_argument('--persistent-cache-seed',type=Path,help='Current pair only: verified fresh plain files imported in this measurement group')
    parser.add_argument('--expected-official-audit',type=Path,help='Verified official result of original complete replay')
    parser.add_argument('--exp1-checkpoint',action='store_true',help='Also verify frozen Exp1 and step-022 restore on this owned service')
    parser.add_argument('--confirmed-window',action='store_true',help='Legacy explicit user-confirmed window; no longer required')
    parser.add_argument('--automatic-window',action='store_true',help='Given timestamps bound runtime under standing authorization, not exclusive disk access')
    args=parser.parse_args()
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,cancelled)
    inherited=os.environ.get('ASB_PRIVATE_WRITER_FD') if os.environ.get('ASB_HOST_PRIVATE_MEMBER') else None
    with writer_lock(inherited_fd=int(inherited) if inherited else None):raise SystemExit(main(args))
