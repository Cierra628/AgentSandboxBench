"""Configuration dispatch to the existing prepared-host task controllers."""
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
TASKS = {'prettier__prettier-6604': 'prettier6604',
         'sphinx-doc__sphinx-7590': 'sphinx7590', 'pydata__xarray-6992': 'xarray6992'}
FIELDS = {'version', 'task_id', 'backend', 'rollout', 'checkpoint_after_step',
          'inputs', 'reference', 'output_root', 'cache_backup_directory', 'frozen_export',
          'replay_policy', 'physical_cache_after_segments', 'shared_cache_measurement', 'root_count', 'experiment', 'cache_mode', 'live_cache_kernel', 'live_cache_lower', 'guest_cache_baseline', 'guest_cache_target_mib', 'light_monitor', 'monitor_mode', 'damon_observe', 'damon_disable', 'host_file_monitor', 'host_prefix_count', 'host_shared_files'}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def configuration(path, backend=None, boundary=None, storage=None):
    cfg = read(path)
    required = FIELDS - {'cache_backup_directory', 'frozen_export', 'replay_policy', 'physical_cache_after_segments', 'shared_cache_measurement', 'root_count', 'experiment', 'cache_mode', 'live_cache_kernel', 'live_cache_lower', 'guest_cache_baseline', 'guest_cache_target_mib', 'light_monitor', 'monitor_mode', 'damon_observe', 'damon_disable', 'host_file_monitor', 'host_prefix_count', 'host_shared_files'}
    if not required <= cfg.keys() or cfg.keys() - FIELDS or cfg['version'] != 2:
        raise ValueError('unsupported task workflow config fields/version')
    is_trenvx_exp2=cfg.get('experiment')=='exp2' and cfg['backend']=='trenvx'
    if cfg['task_id'] not in TASKS or (not is_trenvx_exp2 and (cfg['backend'] != 'agentenv' or backend not in (None, 'agentenv'))) or is_trenvx_exp2 and backend not in (None,'trenvx'):
        raise ValueError('these task adapters support AgentENV only')
    if cfg.get('experiment')=='exp2':
        if cfg['task_id']!='prettier__prettier-6604' or cfg['rollout']!='four-rounds' or cfg['cache_mode'] not in ('preserve','drop') or cfg['checkpoint_after_step'] is not None or boundary is not None or storage not in (None,'isolated'):
            raise ValueError('Exp2 requires Prettier four rounds, fixed boundaries and preserve/drop mode')
        if set(cfg)-required-{'experiment','cache_mode'} or cfg['inputs']!='configs/exp2-prettier6604.json' or cfg['reference'] is not None:
            raise ValueError('Exp2 uses only the pinned four-round input; other variants are separate')
        if is_trenvx_exp2 and cfg['cache_mode']!='drop':raise ValueError('original TrEnv-X Exp2 clears guest caches before snapshot')
        cfg['inputs']=str((ROOT/cfg['inputs']).resolve());cfg['output_root']=str((ROOT/cfg['output_root']).resolve())
        output=Path(cfg['output_root'])
        if output==ROOT/'.artifacts' or not output.is_relative_to(ROOT/'.artifacts'):raise ValueError('output_root must be inside project .artifacts')
        return cfg
    if cfg.get('experiment') or 'cache_mode' in cfg:raise ValueError('unsupported experiment or cache mode')
    if storage not in (None, 'isolated'):
        raise ValueError('these task adapters use isolated storage')
    if boundary is not None:
        cfg['checkpoint_after_step'] = int(boundary)
    boundary = cfg['checkpoint_after_step']
    if isinstance(boundary, bool) or boundary is not None and not isinstance(boundary, int):
        raise ValueError('checkpoint step must be an integer or null')
    profile = TASKS[cfg['task_id']]
    allowed = {'prettier6604': 24, 'sphinx7590': 29, 'xarray6992': 22}
    if boundary is not None and boundary != allowed[profile]:
        raise ValueError('supported checkpoint step: '+str(allowed[profile]))
    if cfg['rollout'] not in ('single', 'grpo', 'bpo', 'tvcache'):
        raise ValueError('unsupported rollout')
    if cfg['rollout'] != 'single' and (profile != 'prettier6604' or boundary is not None or not cfg.get('frozen_export')):
        raise ValueError('branch schedules require the corrected Prettier export; boundaries belong to the schedule')
    policy = cfg.get('replay_policy', 'strict')
    allowed_policies = ('strict', 'sphinx-pipeline-v2') if profile == 'sphinx7590' else ('xarray-approved-sigpipe',) if profile == 'xarray6992' else ('strict',)
    if policy not in allowed_policies or boundary is not None and policy == 'sphinx-pipeline-v2':
        raise ValueError('unsupported replay policy for this task/mode')
    cfg['replay_policy'] = policy
    shared=cfg.get('shared_cache_measurement',False)
    if not isinstance(shared,bool) or shared and (profile!='prettier6604' or cfg['rollout']!='grpo' or boundary is not None):
        raise ValueError('shared-cache analysis requires Prettier GRPO roots without restore')
    if cfg.get('root_count') is not None and (type(cfg['root_count']) is not int or cfg['root_count'] not in (1,2,7) or not shared):
        raise ValueError('root_count requires the shared-cache variant and must be 1, 2 or 7')
    physical = cfg.get('physical_cache_after_segments', [])
    if not isinstance(physical, list) or len(set(physical)) != len(physical):
        raise ValueError('physical cache boundaries must be a unique list')
    if physical and (cfg['rollout'] != 'single' or boundary is not None):
        raise ValueError('physical boundary analysis requires one complete replay without restore')
    segments = ('read_repo', 'read_typescript' if profile == 'prettier6604' else 'trace_cpp_parser' if profile == 'sphinx7590' else 'trace_index_state', 'core_patch', 'core_verify', 'backbone_finish')
    if any(s not in segments for s in physical):
        raise ValueError('unknown physical cache segment')
    if 'live_cache_kernel' in cfg:
        if not isinstance(cfg['live_cache_kernel'],str) or not cfg['live_cache_kernel'].strip():
            raise ValueError('live_cache_kernel must be an explicit diagnostic guest ELF path')
        if cfg['rollout']!='single' or boundary is not None and profile!='prettier6604' or physical or shared:
            raise ValueError('live cache supports single trajectories and Prettier step-24 restore without paused analysis')
        kernel=(ROOT/cfg['live_cache_kernel']).resolve()
        if not kernel.is_relative_to(ROOT/'.artifacts'):
            raise ValueError('diagnostic guest kernel must be inside project .artifacts')
    if 'live_cache_lower' in cfg:
        if type(cfg['live_cache_lower']) is not bool or cfg['live_cache_lower'] and not (cfg.get('live_cache_kernel') and profile=='prettier6604' and boundary==24):
            raise ValueError('live_cache_lower requires diagnostic Prettier step-24 restore')
    if 'guest_cache_baseline' in cfg:
        if type(cfg['guest_cache_baseline']) is not bool or cfg['guest_cache_baseline'] and not (cfg.get('live_cache_kernel') and profile in ('sphinx7590','xarray6992') and boundary is None):
            raise ValueError('guest_cache_baseline requires a complete live-cache Python task')
    if 'guest_cache_target_mib' in cfg:
        target=cfg['guest_cache_target_mib']
        if type(target) is not int or target<=0 or cfg.get('guest_cache_baseline') or not (cfg.get('live_cache_kernel') and profile in ('sphinx7590','xarray6992') and boundary is None):
            raise ValueError('guest_cache_target_mib requires positive MiB and a complete live-cache Python task without baseline mode')
    for key in ('inputs', 'reference', 'output_root', 'cache_backup_directory', 'frozen_export', 'live_cache_kernel'):
        if cfg.get(key): cfg[key] = str((ROOT / cfg[key]).resolve())
    simple = cfg['rollout']=='single' and boundary is None and not (physical or shared or cfg.get('live_cache_kernel'))
    mode = cfg.get('monitor_mode', 'continuous')
    if mode not in ('continuous', 'off') or mode=='off' and not simple:
        raise ValueError('monitor_mode off requires a complete single task without physical cache analysis')
    cfg['light_monitor'] = cfg.get('light_monitor', simple and mode!='off')
    light_rollout = profile=='prettier6604' and cfg['rollout'] in ('grpo','bpo') and boundary is None and not (physical or shared or cfg.get('live_cache_kernel'))
    if type(cfg['light_monitor']) is not bool or cfg['light_monitor'] and not (simple or light_rollout):
        raise ValueError('light_monitor requires an ordinary single task or Prettier GRPO/BPO without physical analysis')
    if mode=='off' and cfg['light_monitor']:
        raise ValueError('monitor_mode off cannot enable light_monitor')
    if 'damon_observe' in cfg and (type(cfg['damon_observe']) is not bool or cfg['damon_observe'] and not (profile=='prettier6604' and cfg['light_monitor'])):
        raise ValueError('damon_observe requires a complete light Prettier task')
    if 'damon_disable' in cfg and (type(cfg['damon_disable']) is not bool or cfg['damon_disable'] and not cfg.get('damon_observe')):
        raise ValueError('damon_disable requires complete light Prettier DAMON observation')
    if 'host_file_monitor' in cfg and (type(cfg['host_file_monitor']) is not bool or cfg['host_file_monitor'] and
            (boundary is not None or cfg.get('live_cache_kernel') or physical or shared or cfg['rollout'] not in ('single','grpo','bpo') or cfg['rollout']=='bpo' and not cfg['light_monitor'])):
        raise ValueError('host_file_monitor supports ordinary tasks, GRPO or explicit light BPO without fine physical analysis')
    if 'host_prefix_count' in cfg:
        if type(cfg['host_prefix_count']) is not int or cfg['host_prefix_count'] not in (1,7) or not (light_rollout and cfg['rollout']=='grpo' and cfg['light_monitor'] and cfg.get('host_file_monitor')):
            raise ValueError('host_prefix_count requires 1 or 7 ordinary light GRPO guests with host sampling')
    if 'host_shared_files' in cfg:
        if type(cfg['host_shared_files']) is not bool or cfg['host_shared_files'] and not (light_rollout and cfg['light_monitor'] and cfg.get('host_file_monitor') and not (cfg.get('host_prefix_count') or cfg.get('damon_observe') or cfg.get('damon_disable'))):
            raise ValueError('host_shared_files requires complete light host GRPO/BPO with default reclaim')
    output = Path(cfg['output_root'])
    if output == ROOT/'.artifacts' or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('output_root must be inside project .artifacts')
    if profile != 'prettier6604' and not cfg.get('cache_backup_directory'):
        raise ValueError('Python tasks require their verified cache backup directory')
    return cfg


def commands(cfg, cache, tool_capture=False, durable_phase=None, bundle=None, agent_config=None, task_config=None, replay_source=None, physical_after=None):
    """Return the existing input checker and isolated runtime commands."""
    if agent_config and not tool_capture:raise ValueError('online agent requires tool capture workflow')
    if cfg.get('host_file_monitor') and (tool_capture or durable_phase):
        raise ValueError('host_file_monitor currently uses frozen task replay')
    if cfg.get('live_cache_kernel') and (tool_capture or durable_phase):
        raise ValueError('live cache is currently for complete frozen task replay')
    if cfg.get('experiment')=='exp2':
        if tool_capture or durable_phase or bundle:raise ValueError('Exp2 is separate from tool capture and durable checkpoint demos')
        if cfg['backend']=='trenvx':
            return ([str(ROOT/'runtime/trenvx-venv/bin/python'),'-B',str(ROOT/'scripts/run_exp2_trenvx.py'),'--check-only'],
                ['bash',str(ROOT/'scripts/validate-isolated-trenvx.sh'),'--automatic-window','--mode','exp2'])
        return ([sys.executable,'-B',str(ROOT/'scripts/run_exp2.py'),'--cache-mode',cfg['cache_mode'],'--check-only'],
            ['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window','--immutable-cache-seed',str(cache),'--exp2-cache-mode',cfg['cache_mode']])
    profile = TASKS[cfg['task_id']]
    check = [sys.executable, '-B']
    live = ['bash', str(ROOT/'scripts/validate-isolated-storage.sh'), '--automatic-window', '--immutable-cache-seed', str(cache)]
    if tool_capture and task_config:
        from online_task import load
        task=load(task_config,cfg['inputs'])
        if task['profile']=='prettier6604':
            if profile!='prettier6604' or cfg['rollout']!='single' or cfg['checkpoint_after_step']:
                raise ValueError('online Prettier uses single clean sandbox without checkpoint')
            common=['--task-config',str(task_config)]
            if agent_config:common+=['--agent-config',str(agent_config)]
            if replay_source:common+=['--replay-source',str(replay_source)]
            if physical_after:common+=['--physical-after',*map(str,physical_after)]
            check += [str(ROOT/'scripts/run_tool_capture.py'),'--inputs',cfg['inputs'],*common]
            live += ['--prettier6604-task',cfg['inputs'],'--online-task-config',str(task_config)]
            if agent_config:live+=['--python-online-agent-config',str(agent_config)]
            if replay_source:live+=['--online-replay-source',str(replay_source)]
            if physical_after:live+=['--online-physical-after',*map(str,physical_after)]
            return check+['--check-only'],live
    if tool_capture or durable_phase:
        if durable_phase not in (None,'capture','restore'):raise ValueError('invalid durable checkpoint phase')
        if profile!='sphinx7590' or cfg['rollout']!='single' or cfg['checkpoint_after_step'] or cfg.get('physical_cache_after_segments'):
            raise ValueError('tool capture demo requires Sphinx image, single sandbox, no checkpoint or physical analysis')
        if durable_phase=='restore':live=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window']
        check += [str(ROOT/'scripts'/('run_native_checkpoint.py' if durable_phase else 'run_tool_capture.py')),'--inputs',cfg['inputs']]
        if durable_phase:check+=['--phase',durable_phase,*(['--bundle',str(bundle)] if bundle else [])]
        live += ['--python-task-profile',profile,'--python-backbone-inputs',cfg['inputs'],'--python-readonly-replay','--python-persistent-sampling','--python-cache-reference',cfg['reference'],'--python-durable-checkpoint-demo' if durable_phase else '--python-tool-capture-demo']
        if task_config:live+=['--python-tool-task-config',str(task_config)]
        if bundle:live+=['--python-durable-checkpoint-bundle',str(bundle)]
        if task_config:
            check+=['--task-config',str(task_config)]
        if agent_config:
            check+=['--agent-config',str(agent_config)]
            live+=['--python-online-agent-config',str(agent_config)]
        return check+['--check-only'],live
    if profile == 'prettier6604':
        check += [str(ROOT/'scripts/run_prettier6604.py'), '--official-task', cfg['inputs'], '--expected-official-audit', cfg['reference']]
        live += ['--prettier6604-task', cfg['inputs'], '--expected-official-audit', cfg['reference']]
        if cfg['rollout'] != 'single':
            check[2] = str(ROOT/'scripts/run_prettier6604_grpo.py')
            common = ['--frozen-export', cfg['frozen_export'], '--schedule', cfg['rollout']]
            check += common
            live += ['--prettier6604-export', cfg['frozen_export'], '--prettier6604-schedule', cfg['rollout']]
            if cfg['rollout'] == 'grpo': live += ['--prettier6604-grpo-capacity-gib', '16']
            if cfg.get('shared_cache_measurement'):
                check += ['--shared-cache','--root-count',str(cfg.get('root_count',7))]
                live += ['--prettier6604-shared-cache','--prettier6604-root-count',str(cfg.get('root_count',7))]
        elif cfg['checkpoint_after_step']:
            check += ['--checkpoint']; live += ['--prettier6604-checkpoint']
        else:
            live += ['--prettier6604-readonly-replay']
        if cfg.get('physical_cache_after_segments'):
            live += ['--prettier6604-physical-cache-after', ','.join(cfg['physical_cache_after_segments'])]
    else:
        check += [str(ROOT/'scripts/run_xarray6992.py'), '--inputs', cfg['inputs'], '--task-profile', profile, '--persistent-sampling']
        live += ['--python-task-profile', profile, '--python-backbone-inputs', cfg['inputs'], '--python-readonly-replay', '--python-persistent-sampling']
        if cfg['replay_policy'] == 'xarray-approved-sigpipe':
            check += ['--diagnostic-continuation']; live += ['--python-diagnostic-continuation']
        if cfg['replay_policy'] == 'sphinx-pipeline-v2':
            check += ['--sphinx-pipeline-diagnostic', '--sphinx-pipeline-policy-version', '2']
            live += ['--python-sphinx-pipeline-diagnostic', '--python-sphinx-pipeline-policy-version', '2']
        if cfg['checkpoint_after_step']:
            check += ['--checkpoint', '--expected-evaluation-raw', cfg['reference']]
            live += ['--python-checkpoint', '--python-expected-evaluation-raw', cfg['reference']]
        if cfg.get('physical_cache_after_segments'):
            boundaries = ','.join(cfg['physical_cache_after_segments'])
            check += ['--physical-cache-after', boundaries]
            live += ['--python-physical-cache-after', boundaries]
    if cfg.get('live_cache_kernel'):
        check += ['--live-cache']
        live += ['--diagnostic-guest-kernel',cfg['live_cache_kernel'],
                 '--prettier6604-live-cache' if profile=='prettier6604' else '--python-live-cache']
    if cfg.get('live_cache_lower'):
        check += ['--live-cache-lower']
        live += ['--prettier6604-live-cache-lower']
    if cfg.get('guest_cache_baseline'):
        check += ['--cache-baseline']
        live += ['--python-cache-baseline']
    if 'guest_cache_target_mib' in cfg:
        check += ['--cache-target-mib',str(cfg['guest_cache_target_mib'])]
        live += ['--python-cache-target-mib',str(cfg['guest_cache_target_mib'])]
    if cfg.get('light_monitor'):
        check += ['--light-monitor']; live += ['--light-monitor']
    if cfg.get('damon_observe'):
        check += ['--damon-observe']; live += ['--damon-observe']
    if cfg.get('damon_disable'):
        check += ['--damon-disable']; live += ['--damon-disable']
    if cfg.get('host_file_monitor'):
        live += ['--host-file-monitor']
    if cfg.get('host_shared_files'):
        check += ['--host-shared-files']; live += ['--host-shared-files']
    if cfg.get('host_prefix_count'):
        check += ['--host-prefix-count',str(cfg['host_prefix_count'])]
        live += ['--host-prefix-count',str(cfg['host_prefix_count'])]
    if cfg.get('monitor_mode')=='off':
        check += ['--monitor-mode','off']
        live += ['--prettier6604-monitor-mode' if profile=='prettier6604' else '--python-monitor-mode','off']
    return check + ['--check-only'], live


def preflight(cfg, offline=False, tool_capture=False, durable_phase=None, bundle=None, agent_config=None, task_config=None, replay_source=None, physical_after=None):
    if cfg.get('live_cache_kernel'):
        with Path(cfg['live_cache_kernel']).open('rb') as stream:
            if stream.read(4)!=b'\x7fELF':raise ValueError('diagnostic guest kernel is not ELF')
    check, _ = commands(cfg, ROOT/'.artifacts/not-created-cache',tool_capture,durable_phase,bundle,agent_config,task_config,replay_source,physical_after)
    verified = subprocess.run(check, cwd=ROOT, capture_output=True, text=True, timeout=60)
    if verified.returncode:
        raise ValueError('frozen input validation failed: '+verified.stderr[-2000:])
    state = dict(status='VALIDATED', scope='offline frozen inputs', input_check=verified.stdout.strip(), effective_config=cfg)
    if offline: return state
    from isolated_storage import available, capacity_budget, GIB
    capacity = 32 if cfg.get('experiment')=='exp2' or cfg['rollout'] in ('bpo', 'tvcache') else 16 if cfg['rollout'] == 'grpo' else 24 if cfg['task_id'] == 'prettier__prettier-6604' and cfg['checkpoint_after_step'] else 16 if cfg['checkpoint_after_step'] else 8
    if tool_capture and task_config and cfg['task_id']=='prettier__prettier-6604':capacity=16
    archive_reserve=0
    if durable_phase=='capture':
        from native_checkpoint_bundle import ARCHIVE_LIMIT
        archive_reserve=ARCHIVE_LIMIT
    budget = capacity_budget(available(ROOT)-archive_reserve, capacity*GIB)
    budget['checkpoint_archive_reserved_bytes']=archive_reserve
    budget['actual_host_available_bytes']=available(ROOT)
    if os.geteuid() != 0: raise RuntimeError('root required for prepared-host live runs')
    if not budget['ready']: raise RuntimeError('insufficient disk budget: '+json.dumps(budget))
    from validate_isolated_agentenv import SERVICE, IMAGE
    if cfg['backend']=='trenvx':
        if available(ROOT)<=60*GIB:raise RuntimeError('TrEnv-X fixed-volume boundary requires more than 60 GiB host available')
        state.update(status='READY',scope='prepared TrEnv-X Exp2 inputs and fixed-volume budget',storage=budget,restore_semantics='native VM memory and promoted writable-rootfs')
        return state
    service = read_json_command(['docker', 'inspect', SERVICE])[0]
    if not service['State']['Running'] or service['Image'] != IMAGE:
        raise RuntimeError('prepared source service/image unavailable; existing service untouched')
    state.update(status='READY', scope='prepared-host inputs and storage budget', storage=budget,
                 source_service_name=SERVICE,
                 restore_semantics='complete VM state' if cfg.get('experiment')=='exp2' or durable_phase or cfg['checkpoint_after_step'] or cfg['rollout'] in ('bpo', 'tvcache') else 'not exercised')
    return state


def read_json_command(argv):
    return json.loads(subprocess.check_output(argv, timeout=25))


def report(directory, output):
    receipt = read(directory/'result.json')
    if receipt['status'] != 'PASS': raise ValueError('workflow is failed/incomplete')
    cfg = read(directory/'effective-config.json'); child = Path(receipt['storage_result_dir'])
    outer = read(child/'result.json')
    required_cleanup=('mount_and_loop_removed','successful_fixture_removed','original_firecracker_identities_unchanged') if cfg['backend']=='trenvx' else ('owned_container_removed', 'mount_and_loop_removed', 'successful_fixture_removed', 'original_firecracker_identities_unchanged', 'original_service_unchanged')
    if receipt.get('online_agent'):
        required_cleanup=tuple('original_source_firecracker_identities_unchanged' if k=='original_firecracker_identities_unchanged' else k for k in required_cleanup)
    if outer['status'] != 'PASS' or not all(outer['cleanup'].get(k) for k in required_cleanup):
        raise ValueError('owned runtime or cleanup incomplete')
    raw = child/('prettier6604' if cfg['task_id'] == 'prettier__prettier-6604' else 'python-backbone')
    if cfg['backend']=='trenvx':
        owned=list((child/'raw').iterdir());assert len(owned)==1
        inner=read(owned[0]/'result.json')
        if not inner['host_cleanup']['cgroup_removed'] or inner['host_cleanup']['owned_loop_backings'] or inner['checks'].get('service_shutdown')!='PASS':raise ValueError('TrEnv-X service or cgroup cleanup incomplete')
        raw=owned[0]/'replay'
    if output.exists() or output.is_relative_to(directory) or output.is_relative_to(child) or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('report output must be new, separate, inside .artifacts')
    if receipt.get('workflow')=='tool-capture-demo':
        from tool_trajectory import report as capture_report
        capture_report(raw,output)
    elif cfg.get('experiment')=='exp2':
        from report_exp2 import report as exp2_report
        exp2_report(raw,output)
    elif cfg.get('monitor_mode')=='off':
        from report_task_overview import write_tool_report
        output.mkdir(parents=True, exist_ok=False)
        write(output/'summary.json', write_tool_report(raw, output))
    elif (raw/'runtime-monitor.json').exists() and read(raw/'runtime-monitor.json').get('profile')=='light':
        from report_task_overview import write_report as light_report
        output.mkdir(parents=True, exist_ok=False)
        write(output/'summary.json', light_report(raw, output))
    elif cfg['task_id'] == 'prettier__prettier-6604':
        if cfg['rollout'] != 'single':
            from report_prettier6604_grpo import report as schedule_report
            summary = schedule_report(raw, output, Path(cfg['inputs']))
        else:
            if cfg['checkpoint_after_step']:
                from report_prettier6604 import audit
                summary, _, _ = audit(raw, json.loads((ROOT/'configs/prettier6604-backbone.json').read_text()))
            else:
                from prettier6604_monitor_pairs import inspect_run
                summary = inspect_run(raw, 'continuous')
            from report_sandbox_runtime import write_report
            output.mkdir(parents=True, exist_ok=False)
            summary['runtime_metrics'] = write_report(raw, output)
            write(output/'summary.json', summary)
    else:
        from report_xarray6992 import report as python_report
        python_report(raw, output)
    summary = read(output/'summary.json')
    if cfg.get('host_file_monitor'):
        from report_host_file import report as host_report
        host_summary=host_report(directory/'host-file', child/'host-file-residency.json', output/'host-file')
        summary['host_file']=dict(report='host-file/summary.json',curve='host-file/overview.png',
                                  file_peak_bytes=host_summary['file_peak_bytes'],scope=host_summary['scope'])
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if cfg.get('host_shared_files'):
        from host_rollout_shared import report as shared_report
        shared=shared_report(raw,output/'shared-files')
        summary['shared_files']=dict(report='shared-files/REPORT.md',summary='shared-files/summary.json',
            original_paths_elapsed_ns=shared['original_paths_elapsed_ns'],query_elapsed_ns=shared['query_elapsed_ns'],
            rollout_includes_query=True,scope=shared['scope'])
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if cfg.get('live_cache_kernel'):
        from report_live_task_cache import report as live_report
        cache_summary=live_report(raw,output/'live-cache')
        if 'page_cache' in summary:
            summary['page_cache']['physical_bytes']='See live-cache/summary.json: confirmed host residency per sample; unconfirmed guest capacity separate'
            summary['page_cache']['physical_measurement']='No-pause diagnostic metadata; absent VMM PTE does not imply zero backing cache'
        summary['live_cache']=dict(report='live-cache/summary.json',curve='live-cache/cache.svg',
            sample_count=cache_summary['sample_count'],vm_pause=False,
            observed_guest_cached_peak_bytes=cache_summary['observed_guest_cached_peak_bytes'],
            observed_host_confirmed_peak_bytes=cache_summary['observed_host_confirmed_peak_bytes'],
            unconfirmed_capacity_max_bytes=cache_summary['unconfirmed_capacity_max_bytes'])
        if 'provider_buffer_breakdown' in cache_summary:
            files=cache_summary['provider_buffer_breakdown']['files']
            summary['live_cache']['provider_buffer_curve']='live-cache/'+files['curve']
            summary['live_cache']['provider_buffer_table']='live-cache/'+files['table']
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    write(output/'result.json', dict(status='PASS', task_id=summary.get('task_id','tool-interface-demo') if receipt.get('workflow')=='tool-capture-demo' else cfg['task_id'], rollout=cfg['rollout'],
        raw=str(raw), source_workflow=str(directory), source_status=outer['status'],
        strict_replay_status=summary.get('strict_replay_status',summary.get('replay',{}).get('strict_output_status','PASS')),
        official_task_status=summary.get('official_task_status','NOT_RUN') if receipt.get('workflow')=='tool-capture-demo' else read(raw/'result.json').get('official_task_status', 'SEE_TASK_SUMMARY'),
        online_task_status=summary.get('task_outcome'),
        scope='configured task capture/frozen replay; task outcome separate from platform status' if summary.get('task_id')!='tool-interface-demo' else 'actual tool capture/freeze/replay; no model inference or benchmark task acceptance' if receipt.get('workflow')=='tool-capture-demo' else 'platform replay and report; original model task outcomes remain separate', cleanup=outer['cleanup']))
    return output


def run(cfg, tool_capture=False, agent_config=None, task_config=None, replay_source=None, physical_after=None):
    host_group=None
    if cfg.get('host_file_monitor'):
        from host_file_monitor import measurement_group
        host_group,_=measurement_group()
    ready = preflight(cfg,tool_capture=tool_capture,agent_config=agent_config,task_config=task_config,replay_source=replay_source,physical_after=physical_after)
    out = Path(cfg['output_root'])/(time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'-'+str(os.getpid()))
    os.umask(0o077); out.mkdir(parents=True, exist_ok=False)
    write(out/'effective-config.json', cfg); write(out/'preflight.json', ready)
    if agent_config:shutil.copy2(agent_config,out/'agent-config.json')
    code=out/'workflow-code';code.mkdir()
    sources=('platform_tasks.py', 'use_platform.py', 'use-platform.sh')
    if cfg.get('host_file_monitor'):
        sources+=('host_file_monitor.py','report_host_file.py','run_host_file_workflow.py','run-host-file-monitor.sh')
    if cfg.get('host_prefix_count'):sources+=('host_prefix_compare.py',)
    if cfg.get('host_shared_files'):sources+=('host_rollout_shared.py','host_rootfs_layers.py','host_prefix_compare.py','run_host_file_restore.py','host_scaling.py')
    for name in sources:
        shutil.copy2(ROOT/'scripts'/name,code/name)
    write(out/'workflow-version.json',dict(git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in code.iterdir()},
        scope='executed dispatcher sources; controller and frozen input versions retained by the isolated child'))
    profile = TASKS[cfg['task_id']]
    cache_root = 'prettier6604' if profile == 'prettier6604' else 'sphinx-backbone' if profile == 'sphinx7590' else 'python-backbone'
    cache = ROOT/'.artifacts'/cache_root/('platform-cache-'+out.name)
    prepare = [sys.executable, '-B', str(ROOT/'scripts/prepare_prettier6604_cache.py'), str(cache)]
    if cfg.get('cache_backup_directory'): prepare += ['--source-backup-directory', cfg['cache_backup_directory'], '--task-profile', profile]
    result = dict(status='FAIL', workflow='tool-capture-demo' if tool_capture else 'configured-task-v2', task_id='tool-interface-demo' if tool_capture else cfg['task_id'], rollout=cfg['rollout'])
    if agent_config or task_config:result['online_agent']=True
    stage = 'cache-prepare'
    print('status=RUNNING result_dir='+str(out), flush=True)
    def call(argv, label, timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
    host_monitor=None
    try:
        if host_group:
            from host_file_monitor import HostFileMonitor
            host_monitor=HostFileMonitor(out/'host-file',host_group,include_pss=not cfg.get('host_shared_files',False))
        if cfg['backend']!='trenvx':call(prepare, stage, 300)
        stage = 'isolated-runtime'
        _, live = commands(cfg, cache,tool_capture,agent_config=agent_config,task_config=task_config,replay_source=replay_source,physical_after=physical_after); call(live, stage, 1300)
        matches = re.findall(r'status=PASS result_dir=(\S+)', (out/(stage+'.log')).read_text())
        storage_root='storage-isolated-trenvx' if cfg['backend']=='trenvx' else 'storage-isolated-service'
        children = [Path(p) for p in matches if Path(p).parent == ROOT/'.artifacts'/storage_root]
        if len(set(children)) != 1: raise ValueError('expected one owned isolated runtime result')
        result['storage_result_dir'] = str(children[0])
        result['status'] = 'PASS'
    except Exception as exc:
        result.update(error=repr(exc), failed_stage=stage); (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if host_monitor:
            try:result['host_file_monitor']=host_monitor.stop()
            except Exception as exc:result.update(status='FAIL',host_monitor_error=repr(exc))
        if (cache/'resources.json').exists():
            try:
                call([sys.executable, '-B', str(ROOT/'scripts/prepare_prettier6604_cache.py'), str(cache), '--release'], 'cache-release', 90)
                result['cache_released'] = read(cache/'resources.json')['released']
            except Exception as exc: result.update(status='FAIL', cache_release_error=repr(exc))
    write(out/'result.json', result)
    if result['status'] == 'PASS':
        try:
            destination=report(out, out.parent/('report-'+out.name))
            result.update(report_dir=str(destination),**{k:v for k,v in read(destination/'result.json').items()
                if k in ('strict_replay_status','official_task_status','cleanup')})
        except Exception as exc:
            result.update(status='FAIL', failed_stage='report', error=repr(exc))
            (out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out), flush=True)
    return 0 if result['status'] == 'PASS' else 1
