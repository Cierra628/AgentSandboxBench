"""Two additional sampled runs of seven tasks; reuse the four saved C4 runs."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from platform_tasks import ROOT, configuration, commands
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE, SOURCE_ID
from validate_isolated_agentenv import IMAGE
from report_eight_task_repeats import collect, inventory

TASKS = ('prettier6604', 'django16819', 'sphinx7590', 'xarray6992',
         'matplotlib__matplotlib-24177', 'large-scale-text-editing', 'cython')
CONFIGS = dict(django16819='external-django16819-light.json', cython='external-cython-light.json',
               **{'matplotlib__matplotlib-24177': 'external-matplotlib24177-light.json',
                  'large-scale-text-editing': 'external-text-editing-light.json'})


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute', action='store_true', help='Otherwise only audit saved input records and save the plan')
    p.add_argument('--cython-relay', help='Only with explicit permission for the two new original Git/pip runs')
    p.add_argument('--resume-from', type=Path, help='Retain completed runs, execute only unfinished planned entries in a new directory')
    p.add_argument('--retry-failed', action='store_true', help='Explicitly include a diagnosed failed preparation; retain the old record')
    a = p.parse_args(); out = a.output.resolve()
    assert out.is_relative_to(ROOT / '.artifacts') and not out.exists()
    os.umask(0o077); out.mkdir(parents=True)
    tasks = TASKS if a.cython_relay else TASKS[:-1]
    plan = [dict(round=n, task=task) for n, order in ((2, tasks), (3, tuple(reversed(tasks)))) for task in order]
    previous = None
    if a.resume_from:
        previous = json.loads((a.resume_from.resolve()/'result.json').read_text())
        assert previous['status']=='FAIL' and previous.get('source_stopped') and previous.get('cache_released')
        saved = []; prior = a.resume_from.resolve()
        while prior:
            saved.extend(json.loads((prior/'result.json').read_text())['runs'])
            parent = json.loads((prior/'plan.json').read_text()).get('resume_from')
            prior = (ROOT / parent).resolve() if parent else None
        done = {(r['round'],r['task']) for r in saved if r['status']=='PASS'}
        failed = {(r['round'],r['task']) for r in saved if r['status']=='FAIL'}
        plan = [r for r in plan if (r['round'],r['task']) not in done
                and (a.retry_failed or (r['round'],r['task']) not in failed)]
    write(out / 'inventory.json', inventory())
    write(out / 'plan.json', dict(runs=plan, ordinary_interval_seconds=.1, pss_interval_seconds=1,
                                 c4='Reuse four complete same-profile single-guest records',
                                 cython_proxy_authorized=bool(a.cython_relay), cache_reset=False,
                                 unused_network_pools=True, resume_from=str(a.resume_from) if a.resume_from else None,
                                 retry_failed=a.retry_failed))
    if not a.execute:
        print('status=PLAN_SAVED result_dir='+str(out)); return 0
    env = clean_environment(dict(os.environ, ASB_AGENTENV_SOURCE_SERVICE=SOURCE, PYTHONDONTWRITEBYTECODE='1'))
    # Another workspace owns the historical fixed pools; retain its workload.
    env['ASB_ISOLATED_UNUSED_NETWORK_POOLS'] = 'on'
    env.pop('ASB_LOCAL_NO_PROXY', None); env.pop('ASB_LOCAL_NO_PROXY_CHECK', None)
    cache = ROOT / '.artifacts/prettier6604' / ('eight-task-repeats-'+out.name)
    cfg = configuration(ROOT / 'configs/platform-prettier6604-grpo.json')
    flags = ['--schedule-smoke-metrics', '--schedule-sampling-control']
    state = dict(status='RUNNING', stage='preflight', runs=[], expected_new_runs=len(plan))
    started = False
    code = out / 'code'; code.mkdir()
    for name in ('run_eight_task_repeats.py', 'report_eight_task_repeats.py',
                 'run_prettier6604_grpo.py', 'run_external_workload.py', 'run_host_external_workload.py',
                 'platform_tasks.py', 'external_task_metrics.py', 'host_sandbox_monitor.py',
                 'sandbox_runtime_monitor.py', 'validate_isolated_agentenv.py'):
        shutil.copy2(ROOT / 'scripts' / name, code / name)
    def inspect():
        return json.loads(subprocess.check_output(['docker', 'inspect', SOURCE], timeout=15))[0]
    def call(command, label, timeout):
        write(out / (label+'-command.json'), list(map(str, command)))
        with (out / (label+'.log')).open('x') as log:
            result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(label+' exit '+str(result.returncode)+'; '+str(out / (label+'.log')))
        return (out / (label+'.log')).read_text()
    try:
        source = inspect()
        assert source['Id'] == SOURCE_ID and source['Image'] == IMAGE and not source['State']['Running']
        assert source['HostConfig']['RestartPolicy']['Name'] == 'no'
        from isolated_storage import rootfs_network_pools
        routes = subprocess.check_output(['ip', '-j', 'route', 'show', 'table', 'all'], text=True, timeout=10)
        write(out / 'network-preflight.json', dict(unused_pools=rootfs_network_pools(routes), routes=json.loads(routes)))
        assert shutil.disk_usage(ROOT).free > 82*2**30
        check, _ = commands(cfg, cache); call(check+flags, 'prettier-offline', 60)
        call([sys.executable, '-B', ROOT / 'scripts/prepare_prettier6604_cache.py', cache], 'cache-prepare', 300)
        subprocess.run(['docker', 'start', SOURCE], check=True, capture_output=True, timeout=30); started = True
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health', timeout=2) as response:
                    if response.status in (200, 204): break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise TimeoutError('source health')
        for item in plan:
            task = item['task']; label = f"r{item['round']}-{task}"
            disk = shutil.disk_usage(ROOT).free
            mem = int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
            assert disk > 82*2**30 and mem > 64*2**30, 'resource budget insufficient; no cleanup of old files'
            state.update(stage=label, available_before_bytes=disk, mem_available_bytes=mem, load_average=os.getloadavg())
            write(out / 'result.json', state)
            if task == 'prettier6604':
                _, command = commands(cfg, cache); command += flags
                parent = ROOT / '.artifacts/storage-isolated-service'
            elif task in ('sphinx7590', 'xarray6992'):
                command = ['bash', ROOT / 'scripts/run-host-file-monitor.sh',
                           ROOT / 'configs' / f'platform-{task}-endpoint-metrics.json',
                           '--persistent-input', '--local-no-proxy']
                parent = ROOT / '.artifacts/platform-tasks'
            else:
                path = ROOT / 'configs' / CONFIGS[task]; c = json.loads(path.read_text())
                base = '.artifacts/external-oci-20261105' if task in ('matplotlib__matplotlib-24177', 'large-scale-text-editing') else '.artifacts/task-metrics-upgrade-20261105/oci'
                command = ['bash', ROOT / 'scripts/run-external-workload-light.sh', path,
                           ROOT / '.artifacts/workload-and-upper-20261030/upstream',
                           '--endpoint-metrics', '--oci-input', ROOT / base / c['task_id']]
                if task == 'cython': command += ['--guest-relay-proxy', a.cython_relay]
                parent = ROOT / '.artifacts/host-external-workload'
            print('status=RUNNING run='+label+' result_dir='+str(out), flush=True)
            row = dict(**item, status='RUNNING', log=str(out / (label+'.log')))
            state['runs'].append(row); write(out / 'result.json', state)
            text = call(command, label, 2700)
            paths = {Path(x) for x in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)', text) if Path(x).parent == parent}
            assert len(paths) == 1; workflow = paths.pop(); w = json.loads((workflow / 'result.json').read_text())
            assert w['status'] == 'PASS'
            if task == 'prettier6604':
                raw = workflow / 'prettier6604'; report_dir = out / label; report_dir.mkdir()
                (report_dir / 'REPORT.md').write_text('# Prettier七角色重复\n\n原始结果：`'+str(raw)+'`。\n189动作、每实例0通过2失败，完整检查见原result.json。\n')
            elif task in ('sphinx7590', 'xarray6992'):
                raw = Path(w['storage_result_dir']) / 'python-backbone'
                report_dir = ROOT / 'results' / ('task-repeat-'+label+'-'+workflow.name)
                shutil.copytree(w['report_dir'], report_dir)
            else:
                raw = Path(w['raw']); report_dir = Path(w['published_report'])
                if task == 'cython':
                    assert w['owned_guest_proxy_stopped']
                    for name in ('guest-proxy-address.json', 'guest-proxy-rule.json'):
                        assert json.loads((workflow / name).read_text())['status'] == 'REMOVED'
            collect(task, raw, report_dir)
            row.update(status='PASS', workflow=str(workflow), raw=str(raw), report=str(report_dir))
            write(out / 'result.json', state)
            print('status=PASS run='+label, flush=True)
        state.update(status='PASS', stage='complete')
    except BaseException as error:
        state.update(status='FAIL', error=repr(error))
        if state['runs'] and state['runs'][-1]['status'] == 'RUNNING':
            state['runs'][-1].update(status='FAIL', error=repr(error))
        (out / 'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                call(['docker', 'stop', '-t', '30', SOURCE], 'source-stop', 60)
                state['source_stopped'] = not inspect()['State']['Running']
                assert state['source_stopped']
            except Exception as error:
                state.update(status='FAIL', source_cleanup_error=repr(error))
        if (cache / 'resources.json').exists():
            try:
                call([sys.executable, '-B', ROOT / 'scripts/prepare_prettier6604_cache.py', cache, '--release'], 'cache-release', 90)
                state['cache_released'] = json.loads((cache / 'resources.json').read_text())['released']
            except Exception as error:
                state.update(status='FAIL', cache_cleanup_error=repr(error))
        state['available_after_bytes'] = shutil.disk_usage(ROOT).free
        write(out / 'result.json', state)
        print('status='+state['status']+' result_dir='+str(out), flush=True)
    return 0 if state['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
