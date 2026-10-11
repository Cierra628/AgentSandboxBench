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
          'replay_policy', 'physical_cache_after_segments', 'shared_cache_measurement', 'root_count'}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def configuration(path, backend=None, boundary=None, storage=None):
    cfg = read(path)
    required = FIELDS - {'cache_backup_directory', 'frozen_export', 'replay_policy', 'physical_cache_after_segments', 'shared_cache_measurement', 'root_count'}
    if not required <= cfg.keys() or cfg.keys() - FIELDS or cfg['version'] != 2:
        raise ValueError('unsupported task workflow config fields/version')
    if cfg['task_id'] not in TASKS or cfg['backend'] != 'agentenv' or backend not in (None, 'agentenv'):
        raise ValueError('these task adapters support AgentENV only')
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
    for key in ('inputs', 'reference', 'output_root', 'cache_backup_directory', 'frozen_export'):
        if cfg.get(key): cfg[key] = str((ROOT / cfg[key]).resolve())
    output = Path(cfg['output_root'])
    if output == ROOT/'.artifacts' or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('output_root must be inside project .artifacts')
    if profile != 'prettier6604' and not cfg.get('cache_backup_directory'):
        raise ValueError('Python tasks require their verified cache backup directory')
    return cfg


def commands(cfg, cache, tool_capture=False, durable_phase=None, bundle=None):
    """Return the existing input checker and isolated runtime commands."""
    profile = TASKS[cfg['task_id']]
    check = [sys.executable, '-B']
    live = ['bash', str(ROOT/'scripts/validate-isolated-storage.sh'), '--automatic-window', '--immutable-cache-seed', str(cache)]
    if tool_capture or durable_phase:
        if durable_phase not in (None,'capture','restore'):raise ValueError('invalid durable checkpoint phase')
        if profile!='sphinx7590' or cfg['rollout']!='single' or cfg['checkpoint_after_step'] or cfg.get('physical_cache_after_segments'):
            raise ValueError('tool capture demo requires Sphinx image, single sandbox, no checkpoint or physical analysis')
        if durable_phase=='restore':live=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window']
        check += [str(ROOT/'scripts'/('run_native_checkpoint.py' if durable_phase else 'run_tool_capture.py')),'--inputs',cfg['inputs']]
        if durable_phase:check+=['--phase',durable_phase,*(['--bundle',str(bundle)] if bundle else [])]
        live += ['--python-task-profile',profile,'--python-backbone-inputs',cfg['inputs'],'--python-readonly-replay','--python-persistent-sampling','--python-cache-reference',cfg['reference'],'--python-durable-checkpoint-demo' if durable_phase else '--python-tool-capture-demo']
        if bundle:live+=['--python-durable-checkpoint-bundle',str(bundle)]
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
    return check + ['--check-only'], live


def preflight(cfg, offline=False, tool_capture=False, durable_phase=None, bundle=None):
    check, _ = commands(cfg, ROOT/'.artifacts/not-created-cache',tool_capture,durable_phase,bundle)
    verified = subprocess.run(check, cwd=ROOT, capture_output=True, text=True, timeout=60)
    if verified.returncode:
        raise ValueError('frozen input validation failed: '+verified.stderr[-2000:])
    state = dict(status='VALIDATED', scope='offline frozen inputs', input_check=verified.stdout.strip(), effective_config=cfg)
    if offline: return state
    from isolated_storage import available, capacity_budget, GIB
    capacity = 32 if cfg['rollout'] in ('bpo', 'tvcache') else 16 if cfg['rollout'] == 'grpo' else 24 if cfg['task_id'] == 'prettier__prettier-6604' and cfg['checkpoint_after_step'] else 16 if cfg['checkpoint_after_step'] else 8
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
    service = read_json_command(['docker', 'inspect', SERVICE])[0]
    if not service['State']['Running'] or service['Image'] != IMAGE:
        raise RuntimeError('prepared source service/image unavailable; existing service untouched')
    state.update(status='READY', scope='prepared-host inputs and storage budget', storage=budget,
                 restore_semantics='complete VM state' if durable_phase or cfg['checkpoint_after_step'] or cfg['rollout'] in ('bpo', 'tvcache') else 'not exercised')
    return state


def read_json_command(argv):
    return json.loads(subprocess.check_output(argv, timeout=25))


def report(directory, output):
    receipt = read(directory/'result.json')
    if receipt['status'] != 'PASS': raise ValueError('workflow is failed/incomplete')
    cfg = read(directory/'effective-config.json'); child = Path(receipt['storage_result_dir'])
    outer = read(child/'result.json')
    if outer['status'] != 'PASS' or not all(outer['cleanup'].get(k) for k in ('owned_container_removed', 'mount_and_loop_removed', 'successful_fixture_removed', 'original_firecracker_identities_unchanged', 'original_service_unchanged')):
        raise ValueError('owned runtime or cleanup incomplete')
    raw = child/('prettier6604' if cfg['task_id'] == 'prettier__prettier-6604' else 'python-backbone')
    if output.exists() or output.is_relative_to(directory) or output.is_relative_to(child) or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('report output must be new, separate, inside .artifacts')
    if receipt.get('workflow')=='tool-capture-demo':
        from tool_trajectory import report as capture_report
        capture_report(raw,output)
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
    write(output/'result.json', dict(status='PASS', task_id='tool-interface-demo' if receipt.get('workflow')=='tool-capture-demo' else cfg['task_id'], rollout=cfg['rollout'],
        raw=str(raw), source_workflow=str(directory), source_status=outer['status'],
        strict_replay_status=summary.get('strict_replay_status', 'PASS'),
        official_task_status='NOT_RUN' if receipt.get('workflow')=='tool-capture-demo' else read(raw/'result.json').get('official_task_status', 'SEE_TASK_SUMMARY'),
        scope='actual tool capture/freeze/replay; no model inference or benchmark task acceptance' if receipt.get('workflow')=='tool-capture-demo' else 'platform replay and report; original model task outcomes remain separate', cleanup=outer['cleanup']))
    return output


def run(cfg, tool_capture=False):
    ready = preflight(cfg,tool_capture=tool_capture)
    out = Path(cfg['output_root'])/(time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'-'+str(os.getpid()))
    os.umask(0o077); out.mkdir(parents=True, exist_ok=False)
    write(out/'effective-config.json', cfg); write(out/'preflight.json', ready)
    code=out/'workflow-code';code.mkdir()
    for name in ('platform_tasks.py', 'use_platform.py', 'use-platform.sh'):
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
    stage = 'cache-prepare'
    print('status=RUNNING result_dir='+str(out), flush=True)
    def call(argv, label, timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
    try:
        call(prepare, stage, 300)
        stage = 'isolated-runtime'
        _, live = commands(cfg, cache,tool_capture); call(live, stage, 1300)
        matches = re.findall(r'status=PASS result_dir=(\S+)', (out/(stage+'.log')).read_text())
        children = [Path(p) for p in matches if Path(p).parent == ROOT/'.artifacts/storage-isolated-service']
        if len(set(children)) != 1: raise ValueError('expected one owned isolated runtime result')
        result['storage_result_dir'] = str(children[0])
        result['status'] = 'PASS'
    except Exception as exc:
        result.update(error=repr(exc), failed_stage=stage); (out/'failure.txt').write_text(traceback.format_exc())
    finally:
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
