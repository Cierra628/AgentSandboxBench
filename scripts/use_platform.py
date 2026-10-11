"""Prepared-host Exp1 workflow: validate, replay/restore, then report in new directories."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import traceback

from exp1_inputs import checkpoint_step, validate_trajectory, sha
from experiment_disk_guard import writer_lock, RESERVE_BYTES
from storage_supervisor import supervised_run

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/platform-exp1.json'
FIELDS = {'version', 'task_id', 'backends', 'checkpoint_after_step', 'repository',
          'trajectory_dir', 'runtime_dir', 'server_result', 'official_task', 'output_root',
          'sandbox_cpu', 'sandbox_memory_mib', 'sample_interval_seconds', 'action_timeout_seconds'}


def interrupted(signum, frame):
    raise RuntimeError(f'workflow interrupted: {signum}')


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def source_name(path):
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def configuration(path, backend=None, boundary=None, storage=None):
    cfg = read_json(path)
    if not FIELDS <= set(cfg) or set(cfg)-FIELDS-{'storage'} or cfg['version'] != 1 or cfg['task_id'] != 'prettier__prettier-14400':
        raise ValueError('unsupported config fields/version/task; only frozen Exp1 is supported')
    cfg['storage'] = storage if storage is not None else cfg.get('storage', 'shared')
    if cfg['storage'] not in ('shared', 'isolated'): raise ValueError('storage must be shared or isolated')
    if backend:
        cfg['backends'] = ['agentenv', 'trenvx'] if backend == 'both' else [backend]
    if boundary is not None:
        cfg['checkpoint_after_step'] = boundary
    cfg['checkpoint_after_step'] = checkpoint_step(cfg['checkpoint_after_step'])
    if not cfg['backends'] or len(set(cfg['backends'])) != len(cfg['backends']) or any(b not in ('agentenv', 'trenvx') for b in cfg['backends']):
        raise ValueError('backends must be a nonempty unique list of agentenv/trenvx')
    if 'trenvx' in cfg['backends'] and cfg['checkpoint_after_step'] not in (None, '013', '022'):
        raise ValueError('TrEnv-X currently supports only 013/022 file-state checkpoints')
    if cfg['sandbox_cpu'] != 2 or cfg['sandbox_memory_mib'] != 4096:
        raise ValueError('this frozen adapter requires 2 CPU/4096 MiB')
    for key in ('sample_interval_seconds', 'action_timeout_seconds'):
        number = cfg[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError('invalid positive number: '+key)
    if cfg['sample_interval_seconds'] < .1 or cfg['action_timeout_seconds'] != 60:
        raise ValueError('sampling must be >= 0.1s and frozen action timeout must be 60s')
    for key in ('repository', 'trajectory_dir', 'runtime_dir', 'server_result', 'official_task', 'output_root'):
        cfg[key] = str((ROOT/cfg[key]).resolve())
    if not Path(cfg['output_root']).is_relative_to(ROOT/'.artifacts') or Path(cfg['output_root']) == ROOT/'.artifacts':
        raise ValueError('output_root must be inside project .artifacts')
    cfg['trajectory'] = validate_trajectory(Path(cfg['trajectory_dir']))
    if 'trenvx' in cfg['backends']:
        fixed = ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
        if Path(cfg['trajectory_dir']) != fixed.resolve() or Path(cfg['repository']) != ROOT/'IncrementalDAX_moti':
            raise ValueError('TrEnv-X adapter requires its fixed local repository/trajectory path')
        if Path(cfg['runtime_dir']) != ROOT/'runtime':
            raise ValueError('TrEnv-X adapter requires the fixed project runtime')
    from exp1_official_task import load
    if load(Path(cfg['official_task']))['instance_id'] != cfg['task_id']:
        raise ValueError('official task inputs do not match trajectory')
    if cfg['storage'] == 'isolated':
        if cfg['checkpoint_after_step'] not in (None, '022'):
            raise ValueError('isolated storage currently supports full replay or checkpoint 022 only')
        defaults = read_json(CONFIG)
        for key in ('runtime_dir', 'repository', 'server_result'):
            if Path(cfg[key]) != (ROOT/defaults[key]).resolve():
                raise ValueError('isolated storage requires the prepared '+key)
    return cfg


def environment(cfg):
    runtime = Path(cfg['runtime_dir'])
    return dict(os.environ, PATH=str(runtime/'bin')+':'+os.environ['PATH'],
                XDG_CONFIG_HOME=str(runtime/'config'), PYTHONDONTWRITEBYTECODE='1')


def query(argv, cfg):
    return subprocess.check_output(list(map(str, argv)), env=environment(cfg), timeout=25)


def preflight(cfg):
    if cfg.get('storage') == 'isolated': return isolated_preflight(cfg)
    if os.geteuid() != 0:
        raise RuntimeError('root required for this prepared-host workflow')
    # A 42 GiB start still crossed the 20 GiB reserve in the integration run.
    # Cover the observed ~36 GiB transient reduction plus reserve and margin.
    minimum = 60 if 'trenvx' in cfg['backends'] else 32
    free = shutil.disk_usage(ROOT).free
    if free <= minimum*2**30:
        raise RuntimeError(f'insufficient disk: free={free/2**30:.3f} GiB, required>{minimum} GiB')
    for name in ('aenv', 'docker', 'git', 'timeout'):
        if not shutil.which(name, path=environment(cfg)['PATH']):
            raise RuntimeError('missing tool: '+name)
    server = Path(cfg['server_result'])
    cid = (server/'container-id.txt').read_text().strip()
    info = json.loads(query(['docker', 'inspect', cid], cfg))[0]
    if not info['State']['Running'] or info['Config']['Labels'].get('agentenv.experiment') != 'initial-smoke':
        raise RuntimeError('configured project service is not running')
    credential = tomllib.loads((Path(cfg['runtime_dir'])/'config/aenv/credentials').read_text())
    if credential['url'] != 'http://'+(server/'address.txt').read_text().strip():
        raise RuntimeError('runtime credentials point to a different project service')
    for kind in ([], ['template']):
        if json.loads(query(['aenv', *kind, 'list', '--output', 'json'], cfg)) != []:
            raise RuntimeError('project sandbox/template API is not empty; existing resources are untouched')
    if 'trenvx' in cfg['backends']:
        from smoke_trenvx_service import SEED, CH, KERNEL, ENVD, BIN
        for path in (SEED, CH, KERNEL, ENVD, BIN/'orchestrator', BIN/'template-manager', BIN/'bind_mount', ROOT/'runtime/trenvx-venv/bin/python'):
            if not path.is_file():
                raise RuntimeError('missing TrEnv-X prerequisite: '+str(path))
    return dict(status='READY', free_bytes=free, minimum_free_gib=minimum,
                reserve_bytes=RESERVE_BYTES, server_image_id=info['Image'],
                service_cgroup=Path(f'/proc/{info["State"]["Pid"]}/cgroup').read_text().strip().split('::')[1],
                restore_semantics={b: 'complete VM state' if b == 'agentenv' else 'file-state-only' for b in cfg['backends']})


def resource_identity(project_group):
    vms = {}
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            if path.read_text().strip() == 'firecracker':
                row = dict(start_ticks=(path.parent/'stat').read_text().rsplit(')', 1)[1].split()[19],
                           cgroup=(path.parent/'cgroup').read_text().strip().split('::')[1])
                if row['cgroup'] == project_group:
                    argv = (path.parent/'cmdline').read_bytes().split(b'\0')
                    target = argv[argv.index(b'--api-sock')+1].decode()
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                        channel.settimeout(3); channel.connect(str(path.parent/'root')+target)
                        channel.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
                        response = b''
                        while b'\r\n\r\n' not in response:
                            chunk = channel.recv(4096)
                            if not chunk: raise RuntimeError('Firecracker API closed before headers')
                            response += chunk
                        headers, body = response.split(b'\r\n\r\n', 1)
                        length = int(re.search(rb'Content-Length: (\d+)', headers).group(1))
                        while len(body) < length:
                            chunk = channel.recv(4096)
                            if not chunk: raise RuntimeError('Firecracker API closed before body')
                            body += chunk
                    row['vm_state'] = json.loads(body)['state']
                vms[path.parent.name] = row
        except FileNotFoundError:
            continue
    return vms


def verify_resources(before, after, project_group):
    def pooled(row):
        return row['cgroup'] == project_group and row.get('vm_state') == 'Not started'
    before_fixed = {pid: row for pid, row in before.items() if not pooled(row)}
    after_fixed = {pid: row for pid, row in after.items() if not pooled(row)}
    if before_fixed != after_fixed or sum(map(pooled, before.values())) != sum(map(pooled, after.values())):
        raise ValueError('non-pool VM identities or project pool count changed; inspect resource records')
    return dict(status='PASS', existing_nonpool_identities_unchanged=True,
                project_pool_count=sum(map(pooled, after.values())),
                retired_project_pool_pids=sorted(before.keys()-after.keys()),
                new_project_pool_pids=sorted(after.keys()-before.keys()),
                scope='only same-service Not started pool entries may be replenished; running/unrelated VMs must be unchanged')


def bounded(argv, out, label, cfg):
    return supervised_run(argv, out=out, label=label, env=environment(cfg), filesystem=ROOT,
        raw_root=ROOT/'.artifacts/trenvx-service-smoke', diagnostics=cfg.get('storage_diagnostics', False))


def audit_items(items, cfg, destination):
    from report_memory_metrics import report, write_report
    from verify_initial_acceptance import read_only_audit
    summaries = []
    for index, item in enumerate(items):
        with read_only_audit():
            value = report(item)
            raw = Path(item['raw']); audit = Path(item.get('audit', item['raw']))
            if sha(raw/'replay/actions.tsv') != cfg['trajectory']['manifest_sha256'] or sha(raw/'replay/patch.diff') != cfg['trajectory']['expected_patch_sha256']:
                raise ValueError('final manifest or patch differs from fixed task')
            if value['final_checks']['independent_svg'] != 'PASS' or value['final_checks']['official_task_tests'] != 'PASS':
                raise ValueError('independent/official task checks are missing')
            from exp1_official_task import grade
            log = (audit/'official-task-tests.stdout').read_text() if item['backend'] == 'agentenv' else read_json(raw/'official-task-tests.json')['stdout']
            if grade(Path(cfg['official_task']), log) != read_json(audit/'official-task-result.json'):
                raise ValueError('official results differ from raw log')
            if cfg.get('storage')=='isolated':
                child=Path(item['storage_result_dir'])
                boundary=item['mode'].split('-')[1] if item['mode'].startswith('checkpoint-') else None
                if isolated_item(item['backend'],boundary,child)!=item:
                    raise ValueError('report item differs from verified isolated run paths')
                receipt=read_json(child/'result.json')
                value['storage']=dict(mode='isolated',capacity_bytes=receipt['capacity_bytes'],
                    observed_volume_used_peak_bytes=receipt['observed_volume_used_peak_bytes'],
                    observed_host_available_min_bytes=receipt['observed_host_available_min_bytes'],
                    cleanup=receipt['cleanup'],temporary_checkpoint_images_retained=False,
                    scope='fixed capacity for temporary bulk writes; host raw export and other users excluded')
                if item['backend']=='agentenv':
                    value['cleanup'].update(service='OWNED_TEMPORARY_SERVICE_REMOVED; ORIGINAL_SERVICE_RETAINED',
                        cgroup_and_loop='PASS_FROM_ISOLATED_SERVICE_CLEANUP',
                        archived_images='SUCCESS_VOLUME_REMOVED; RAW_AND_HASHES_RETAINED')
                    value['host_memory']['scope']='single temporary AgentENV service cgroup including cache and VMM; pool disabled'
        write_report(value, destination/f'{index+1:02d}-{item["backend"]}-{item["mode"]}')
        summaries.append(value)
    return summaries


def isolated_preflight(cfg):
    from isolated_storage import available, capacity_budget, GIB
    if os.geteuid() != 0: raise RuntimeError('root required for isolated storage')
    free = available(ROOT)
    capacities = {backend: (20 if backend == 'agentenv' else 24)*GIB for backend in cfg['backends']}
    minimum = 60 if 'trenvx' in capacities else 46
    if free <= minimum*GIB or not all(capacity_budget(free, cap)['ready'] for cap in capacities.values()):
        raise RuntimeError(f'insufficient disk: free={free/GIB:.3f} GiB, required>{minimum} GiB')
    for name in ('mkfs.ext4','losetup','mount','umount','timeout','git'):
        if not shutil.which(name): raise RuntimeError('missing tool: '+name)
    if 'agentenv' in capacities:
        from validate_isolated_agentenv import SERVICE, IMAGE
        for name in ('docker','aenv'):
            if not shutil.which(name, path=environment(cfg)['PATH']): raise RuntimeError('missing tool: '+name)
        cid = (Path(cfg['server_result'])/'container-id.txt').read_text().strip()
        info = json.loads(query(['docker','inspect',SERVICE],cfg))[0]
        if info['Id'] != cid or not info['State']['Running'] or info['Image'] != IMAGE:
            raise RuntimeError('immutable cache source is not the pinned prepared AgentENV service')
    if 'trenvx' in capacities:
        from smoke_trenvx_service import SEED, CH, KERNEL, ENVD, BIN
        python = ROOT/'runtime/trenvx-venv/bin/python'
        for path in (SEED,CH,KERNEL,ENVD,BIN/'orchestrator',BIN/'template-manager',BIN/'bind_mount',python):
            if not path.is_file(): raise RuntimeError('missing TrEnv-X prerequisite: '+str(path))
        if not shutil.which('pigz'): raise RuntimeError('missing tool: pigz')
        subprocess.run([str(python),'-B','-c','import sandbox_sdk'],env=environment(cfg),
                       capture_output=True,check=True,timeout=10)
    return dict(status='READY',storage='isolated',free_bytes=free,minimum_free_gib=minimum,
        reserve_bytes=RESERVE_BYTES,capacities_bytes=capacities,
        allocation='sequential fully allocated volumes; success images removed, failed images retained',
        restore_semantics={b:'complete VM state' if b=='agentenv' else 'file-state-only' for b in cfg['backends']})


def isolated_item(backend, boundary, child):
    value = read_json(child/'result.json')
    cleanup = value.get('cleanup',{})
    if (value['status']!='PASS' or not cleanup.get('successful_fixture_removed') or
            not cleanup.get('mount_and_loop_removed') or not cleanup.get('original_firecracker_identities_unchanged') or
            (child/'volume.ext4').exists()):
        raise ValueError('isolated backend task or cleanup did not pass')
    mode = 'checkpoint-'+boundary if boundary else ('full-light' if backend=='trenvx' else 'full')
    if backend=='agentenv':
        audits=list((child/'exp1/audit').glob('*/result.json'))
        if len(audits)!=1: raise ValueError('expected one isolated AgentENV audit')
        audit=audits[0].parent
        return dict(backend=backend,mode=mode,raw=str(child/'exp1/raw'/audit.name),audit=str(audit),storage_result_dir=str(child))
    runs=list((child/'raw').glob('*/result.json'))
    if len(runs)!=1: raise ValueError('expected one isolated TrEnv-X raw run')
    return dict(backend=backend,mode=mode,raw=str(runs[0].parent),storage_result_dir=str(child))


def isolated_backend(backend, boundary, reference, cfg, out, window):
    from isolated_storage import confirmed_window
    from datetime import datetime, timezone
    confirmed_window(window['start_utc'],window['end_utc'])
    remaining=(datetime.fromisoformat(window['end_utc'])-datetime.now(timezone.utc)).total_seconds()
    minimum=90 if backend=='agentenv' else 420
    if remaining<=minimum: raise RuntimeError('confirmed window too short for next '+backend+' run; never renew automatically')
    mode='checkpoint-'+boundary if boundary else 'full'
    label=backend+'-'+mode
    local={k:cfg[k] for k in FIELDS}
    local.update(storage='isolated',backends=[backend],checkpoint_after_step=boundary)
    config_path=out/(label+'-config.json');write_json(config_path,local)
    script='validate-isolated-storage.sh' if backend=='agentenv' else 'validate-isolated-trenvx.sh'
    argv=['bash',ROOT/'scripts'/script,'--platform-config',config_path,'--automatic-window' if window.get('status')=='STANDING_USER_AUTHORIZATION' else '--confirmed-window',
          '--window-start-utc',window['start_utc'],'--window-end-utc',window['end_utc']]
    if backend=='trenvx':
        argv+=['--mode','checkpoint' if boundary else 'full']
        if boundary: argv+=['--reference',reference]
    env=environment(cfg);env.pop('ASB_STORAGE_BOUNDARY',None)
    env['ASB_WRITER_LOCK']=str(out/'isolated-adapter.lock')
    parent=ROOT/('.artifacts/storage-isolated-service' if backend=='agentenv' else '.artifacts/storage-isolated-trenvx')
    supervised_run(argv,out=out,label=label,env=env,filesystem=ROOT,raw_root=parent,
        timeout=min(900,remaining-30),cleanup_timeout=180,diagnostics=cfg.get('storage_diagnostics',False))
    matches=set(re.findall(r'result_dir=(\S+)',(out/(label+'.log')).read_text()))
    if len(matches)!=1: raise ValueError('expected one isolated backend result directory')
    child=Path(matches.pop()).resolve()
    if child.parent!=parent: raise ValueError('isolated backend returned an unexpected path')
    item=isolated_item(backend,boundary,child)
    actual=read_json(Path(item['audit'])/'effective-config.json') if backend=='agentenv' else read_json(Path(item['raw'])/'config.json')
    actual_boundary=actual['checkpoint_after_step'] if backend=='agentenv' else actual['checkpoint_after']
    if actual_boundary!=boundary: raise ValueError('effective checkpoint boundary differs from requested config')
    intervals=[actual['sample_interval_seconds']] if backend=='agentenv' else [actual['host_sample_interval_seconds'],actual['guest_sample_interval_seconds']]
    if any(interval!=cfg['sample_interval_seconds'] for interval in intervals):
        raise ValueError('effective sampling interval differs from requested config')
    return item


def run_isolated(cfg, window):
    from isolated_storage import confirmed_window
    from datetime import datetime, timezone
    from probe_storage_boundary import identities
    if not window: raise ValueError('isolated run requires an explicitly confirmed window')
    confirmed_window(window['start_utc'],window['end_utc'])
    remaining=(datetime.fromisoformat(window['end_utc'])-datetime.now(timezone.utc)).total_seconds()
    stages=2 if cfg['checkpoint_after_step'] else 1
    required=stages*sum(90 if b=='agentenv' else 420 for b in cfg['backends'])+30
    if remaining<=required: raise RuntimeError('confirmed window too short for complete requested workflow')
    # Hold the project lock through preflight, all temporary backends and reports.
    with writer_lock():
        ready=isolated_preflight(cfg)
        out,paths,hashes=prepare_run(cfg)
        write_json(out/'preflight.json',ready);write_json(out/'coordination.json',window)
        result=dict(status='FAIL',storage='isolated',runs=[],scope='prepared-host frozen Exp1 functional workflow; no performance comparison')
        before=identities();write_json(out/'resources-before.json',before)
        print('status=RUNNING result_dir='+str(out),flush=True)
        try:
            for backend in cfg['backends']:
                reference=None
                for boundary in ([None,cfg['checkpoint_after_step']] if cfg['checkpoint_after_step'] else [None]):
                    isolated_preflight(cfg)
                    item=isolated_backend(backend,boundary,reference,cfg,out,window)
                    result['runs'].append(item)
                    if backend=='trenvx':reference=Path(item['raw'])
            if hashes!={source_name(p):sha(p) for p in paths}: raise ValueError('workflow source changed during run')
            reports=audit_items(result['runs'],cfg,out/'reports')
            result['audited_run_count']=len(reports)
            result.update(cleanup='PASS',status='PASS')
        except Exception as exc:
            result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
        finally:
            try:
                after=identities();write_json(out/'resources-after.json',after)
                unchanged=before==after
                write_json(out/'resource-check.json',dict(status='PASS' if unchanged else 'FAIL',existing_identities_unchanged=unchanged))
                if not unchanged:result.update(status='FAIL',resource_error='original Firecracker identities changed')
            except Exception as exc:
                result.update(status='FAIL',resource_error=repr(exc))
            write_json(out/'result.json',result)
        print('status='+result['status']+' result_dir='+str(out),flush=True)
        return 0 if result['status']=='PASS' else 1


def prepare_run(cfg):
    os.umask(0o077)
    out = Path(cfg['output_root'])/(time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True, exist_ok=False)
    write_json(out/'effective-config.json', cfg)
    code = out/'code'; code.mkdir()
    paths = [ROOT/'scripts'/name for name in ('use_platform.py', 'run_exp1_checkpoint.py', 'exp1_inputs.py',
        'exp1-process-probe.sh', 'exp1_independent_check.cjs', 'audit_exp1_metrics.py', 'report_memory_metrics.py',
        'memory_composition.py', 'exp1_official_task.py', 'verify_initial_acceptance.py',
        'storage_supervisor.py', 'storage_lifecycle.py', 'storage_observer.py', 'experiment_disk_guard.py')]
    if 'trenvx' in cfg['backends']:
        paths += [ROOT/'scripts'/name for name in ('45-replay-trenvx-exp1.sh', '44-smoke-trenvx-service.sh',
            'smoke_trenvx_service.py', 'replay_trenvx_exp1.py', 'archive_trenvx_run_data.py',
            'trenvx_diagnostic_metrics.py', 'experiment_disk_guard.py')]
    if cfg.get('storage') == 'isolated':
        paths += [ROOT/'scripts'/name for name in ('validate_isolated_agentenv.py', 'validate_isolated_trenvx.py',
            'validate-isolated-storage.sh', 'validate-isolated-trenvx.sh', 'isolated_storage.py', 'probe_storage_boundary.py')]
    experiment = Path(cfg['repository'])/'motivation/experiments'
    dependencies = [experiment/'exp1-single-app-smoke/systems/agentenv'/name for name in ('guest-runner.sh', 'config.env')]
    dependencies += [experiment/'lib/host-memory-monitor.sh', Path(cfg['trajectory_dir'])/'actions.tsv']
    paths += dependencies
    hashes = {source_name(p): sha(p) for p in paths}
    for path in paths: shutil.copy2(path, code/path.name)
    shutil.copytree(Path(cfg['trajectory_dir'])/'actions', out/'frozen-actions')
    write_json(out/'source-sha256.json', hashes)
    return out, paths, hashes


def run(cfg, window=None):
    if cfg.get('storage') == 'isolated': return run_isolated(cfg, window)
    out, paths, hashes = prepare_run(cfg)
    result = dict(status='FAIL', runs=[], scope='functional workflow; not a monitoring overhead or page-cache experiment')
    before = None; project_group = None
    print('status=RUNNING result_dir='+str(out), flush=True)
    try:
        ready = preflight(cfg); project_group = ready['service_cgroup']
        write_json(out/'preflight.json', ready)
        before = resource_identity(project_group); write_json(out/'resources-before.json', before)
        for backend in cfg['backends']:
            reference = None
            for boundary in ([None, cfg['checkpoint_after_step']] if cfg['checkpoint_after_step'] else [None]):
                preflight(cfg)
                mode = 'checkpoint-'+boundary if boundary else 'full'
                label = backend+'-'+mode
                if backend == 'agentenv':
                    local = dict(cfg, checkpoint_output_root=str(out/'agentenv'/mode),
                        checkpoint_after_step=boundary, expected_patch_sha256=cfg['trajectory']['expected_patch_sha256'],
                        drop_guest_caches=0, guest_sampling=True, host_monitor_light=True, generate_analysis=False)
                    local.pop('trajectory')
                    write_json(out/(label+'-config.json'), local)
                    with writer_lock():
                        bounded([sys.executable, '-B', ROOT/'scripts/run_exp1_checkpoint.py', out/(label+'-config.json')], out, label, cfg)
                    audits = list((out/'agentenv'/mode/'audit').glob('*/result.json'))
                    if len(audits) != 1: raise ValueError('expected exactly one owned run')
                    audit = audits[0].parent; raw = audit.parent.parent/'raw'/audit.name
                    item = dict(backend=backend, mode=mode, raw=str(raw), audit=str(audit))
                else:
                    argv = ['bash', ROOT/'scripts/45-replay-trenvx-exp1.sh', '--host-sample-interval', str(cfg['sample_interval_seconds']),
                        '--guest-memory-sampling', '--guest-sample-interval', str(cfg['sample_interval_seconds']),
                        '--record-phases', '--official-task', cfg['official_task']]
                    if boundary: argv += ['--checkpoint-after', boundary, '--reference', str(reference)]
                    else: argv += ['--discard-success-images']
                    bounded(argv, out, label, cfg)
                    result_paths = re.findall(r'result_dir=(\S+)', (out/(label+'.log')).read_text())
                    if len(result_paths) != 1: raise ValueError('expected exactly one owned TrEnv-X result')
                    raw = Path(result_paths[0]).resolve()
                    if raw.parent != ROOT/'.artifacts/trenvx-service-smoke': raise ValueError('unexpected result path')
                    reference = raw
                    item = dict(backend=backend, mode=mode if boundary else 'full-light', raw=str(raw))
                result['runs'].append(item)
        if hashes != {source_name(p): sha(p) for p in paths}:
            raise ValueError('workflow source changed during run')
        reports = audit_items(result['runs'], cfg, out/'reports')
        result['audited_run_count'] = len(reports)
        preflight(cfg)
        write_json(out/'resource-check.json', verify_resources(before, resource_identity(project_group), project_group))
        result['cleanup'] = 'PASS'; result['status'] = 'PASS'
    except Exception as exc:
        result['error'] = repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if project_group:
            write_json(out/'resources-after.json', resource_identity(project_group))
        write_json(out/'result.json', result)
    print('status='+result['status']+' result_dir='+str(out), flush=True)
    return 0 if result['status'] == 'PASS' else 1


def main():
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'run'):
        child = sub.add_parser(name); child.add_argument('config', nargs='?', type=Path, default=CONFIG)
        child.add_argument('--backend', choices=('agentenv', 'trenvx', 'both'))
        child.add_argument('--checkpoint-after')
        child.add_argument('--storage',choices=('shared','isolated'))
        if name == 'check': child.add_argument('--offline', action='store_true')
        if name == 'run':
            child.add_argument('--storage-diagnostics', action='store_true')
            child.add_argument('--confirmed-window',action='store_true')
            child.add_argument('--window-start-utc')
            child.add_argument('--window-end-utc')
    child = sub.add_parser('report'); child.add_argument('run_dir', type=Path); child.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'report':
            directory = args.run_dir.resolve(); result = read_json(directory/'result.json')
            if result['status'] != 'PASS': raise ValueError('cannot report failed/incomplete workflow as PASS')
            out = args.output.resolve()
            if out.exists() or out == directory or out.is_relative_to(directory) or not out.is_relative_to(ROOT/'.artifacts'):
                raise ValueError('report output must be new and separate, inside .artifacts')
            os.umask(0o077); out.mkdir(parents=True, exist_ok=False)
            reports = audit_items(result['runs'], read_json(directory/'effective-config.json'), out)
            write_json(out/'result.json', dict(status='PASS', input_run_dir=str(directory), run_count=len(reports)))
            print('status=PASS result_dir='+str(out)); return 0
        cfg = configuration(args.config, args.backend, args.checkpoint_after,args.storage)
        if args.command == 'run':
            cfg['storage_diagnostics'] = args.storage_diagnostics
            window=None
            if cfg['storage']=='isolated':
                from isolated_storage import execution_window
                window=execution_window(args.window_start_utc,args.window_end_utc,automatic=not args.confirmed_window)
            return run(cfg,window)
        state = dict(status='VALIDATED', scope='offline inputs only') if args.offline else preflight(cfg)
        print(json.dumps(dict(**state, effective_config=cfg), indent=2)); return 0
    except Exception as exc:
        print('status=FAIL reason='+str(exc), file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
