"""Three new Prettier runs per schedule, rotating order; stop on first failure."""
import argparse
import csv
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

from isolated_storage import available, capacity_budget, GIB, rootfs_network_pools
from platform_tasks import ROOT, configuration, commands
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE, SOURCE_ID
from run_schedule_smoke import SCHEDULES, read, summarize, write
from validate_isolated_agentenv import IMAGE


def run_plan():
    return [dict(round=n+1, schedule=s) for n in range(3)
            for s in SCHEDULES[n:]+SCHEDULES[:n]]


def inspect_run(storage):
    row = summarize(storage)
    raw = storage/'prettier6604'; task = read(raw/'result.json'); outer = read(storage/'result.json')
    source_key = ('original_source_firecracker_identities_unchanged'
                  if 'original_source_firecracker_identities_unchanged' in outer['cleanup']
                  else 'original_firecracker_identities_unchanged')
    assert outer['cleanup'][source_key] and outer['cleanup']['original_service_unchanged']
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
    assert all(v == 'PASS' for v in task['checks'].values())
    row.update(schedule_run_seconds=(task['schedule_smoke_metrics']['original_paths_end_monotonic_ns']-task['rollout_start_monotonic_ns'])/1e9,
               minimum_disk_gib=outer['observed_host_available_min_bytes']/GIB,
               service_cgroup=task['schedule_smoke_metrics']['cgroup'],
               current_peak_mib=max(int(r['cgroup_current_bytes']) for r in host_rows(raw))/2**20)
    return row


def host_rows(raw):
    with (raw/'host/memory-samples.tsv').open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(); out = args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077); out.mkdir(parents=True); code = out/'code'; code.mkdir()
    for name in ('run_schedule_repeats.py', 'run_schedule_smoke.py', 'schedule_smoke_metrics.py',
                 'report_schedule_metrics_offline.py', 'run_prettier6604_grpo.py',
                 'host_sandbox_monitor.py', 'sandbox_runtime_monitor.py', 'platform_tasks.py',
                 'validate_isolated_agentenv.py', 'prettier_no_proxy.py'):
        shutil.copy2(ROOT/'scripts'/name, code/name)
    plan = run_plan()
    configs = {s: configuration(ROOT/f'configs/platform-prettier6604-{s}.json') for s in SCHEDULES}
    write(out/'plan.json', dict(runs=plan, configs=configs, guest_count=7, repeats_per_schedule=3,
                              ordinary_interval_seconds=.1, pss_interval_seconds=1,
                              endpoint_pss=True, initial_lower_p1_count=19, p0=False,
                              pressure=False, cache_reset=False, proxy=False,
                              unused_network_pools=True, old_smoke_included=False))
    if not args.execute:
        print('status=PLAN_SAVED result_dir='+str(out)); return 0
    env = clean_environment(dict(os.environ, ASB_AGENTENV_SOURCE_SERVICE=SOURCE, PYTHONDONTWRITEBYTECODE='1'))
    env['ASB_ISOLATED_UNUSED_NETWORK_POOLS'] = 'on'
    env.pop('ASB_LOCAL_NO_PROXY', None); env.pop('ASB_LOCAL_NO_PROXY_CHECK', None)
    cache = ROOT/'.artifacts/prettier6604'/('schedule-repeats-'+out.name)
    state = dict(status='RUNNING', stage='preflight', runs=[], expected_runs=9)
    started = False

    def source_state():
        return json.loads(subprocess.check_output(['docker', 'inspect', SOURCE], timeout=15))[0]

    def call(command, label, timeout):
        write(out/(label+'-command.json'), list(map(str, command)))
        with (out/(label+'.log')).open('x') as log:
            result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(label+' exit '+str(result.returncode)+'; '+str(out/(label+'.log')))
        return (out/(label+'.log')).read_text()

    try:
        source = source_state()
        assert source['Id'] == SOURCE_ID and source['Image'] == IMAGE and not source['State']['Running']
        assert source['HostConfig']['RestartPolicy']['Name'] == 'no'
        write(out/'source-before.json', source['State'])
        routes = subprocess.check_output(['ip', '-j', 'route', 'show', 'table', 'all'], text=True, timeout=10)
        write(out/'network-preflight.json', dict(pools=rootfs_network_pools(routes), routes=json.loads(routes)))
        assert capacity_budget(available(ROOT), 32*GIB)['ready'], 'preparation disk budget insufficient'
        for schedule, cfg in configs.items():
            check, _ = commands(cfg, cache)
            call(check+['--schedule-smoke-metrics'], schedule+'-offline', 60)
        state['stage'] = 'cache-prepare'; write(out/'result.json', state)
        call([sys.executable, '-B', ROOT/'scripts/prepare_prettier6604_cache.py', cache], 'cache-prepare', 300)
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
            label = f"r{item['round']}-{item['schedule']}"
            state['stage'] = label+'-resource-check'; write(out/'result.json', state)
            budget = capacity_budget(available(ROOT), (16 if item['schedule']=='grpo' else 32)*GIB)
            mem = int(next(l for l in Path('/proc/meminfo').read_text().splitlines()
                           if l.startswith('MemAvailable:')).split()[1])*1024
            write(out/(label+'-resources.json'), dict(budget=budget, mem_available_bytes=mem, load=os.getloadavg()))
            assert budget['ready'] and mem > 64*GIB, 'resource budget insufficient; no automatic cleanup'
            state['stage'] = label
            row = dict(**item, status='RUNNING', log=str(out/(label+'.log')))
            state['runs'].append(row); write(out/'result.json', state)
            print('status=RUNNING run='+label+' result_dir='+str(out), flush=True)
            _, command = commands(configs[item['schedule']], cache)
            start = time.monotonic_ns()
            text = call(command+['--schedule-smoke-metrics'], label, 1100)
            returned = time.monotonic_ns()
            paths = {Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)', text)
                     if Path(p).parent == ROOT/'.artifacts/storage-isolated-service'}
            assert len(paths) == 1
            storage = paths.pop(); row.update(storage=str(storage), raw=str(storage/'prettier6604'))
            row.update(inspect_run(storage))
            assert row['schedule'] == item['schedule']
            row.update(private_service_round_seconds=(time.monotonic_ns()-start)/1e9,
                       outer_command_seconds=(returned-start)/1e9)
            write(out/'result.json', state)
            print('status=PASS run='+label, flush=True)
        assert len({r['storage'] for r in state['runs']}) == len({r['service_cgroup'] for r in state['runs']}) == 9
        state.update(status='PASS', stage='complete')
    except BaseException as error:
        state.update(status='FAIL', error=repr(error))
        if state['runs'] and state['runs'][-1]['status']=='RUNNING':
            row = state['runs'][-1]; row.update(status='FAIL', error=repr(error))
            text = Path(row['log']).read_text() if Path(row['log']).exists() else ''
            paths = {Path(p) for p in re.findall(r'result_dir=(\S+)', text)
                     if Path(p).parent == ROOT/'.artifacts/storage-isolated-service'}
            if len(paths) == 1:
                storage = paths.pop(); row.update(storage=str(storage), raw=str(storage/'prettier6604'))
                if (storage/'result.json').exists():
                    saved = read(storage/'result.json')
                    row.update(original_error=saved.get('error'), failed_stage=saved.get('failed_stage'))
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                call(['docker', 'stop', '-t', '30', SOURCE], 'source-stop', 60)
                source = source_state(); write(out/'source-after.json', source['State'])
                assert not source['State']['Running']; state['source_stopped'] = True
            except Exception as error:
                state.update(status='FAIL', source_cleanup_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call([sys.executable, '-B', ROOT/'scripts/prepare_prettier6604_cache.py', cache, '--release'], 'cache-release', 90)
                assert read(cache/'resources.json')['released']; state['cache_released'] = True
            except Exception as error:
                state.update(status='FAIL', cache_cleanup_error=repr(error))
        state['available_after_bytes'] = available(ROOT)
        write(out/'result.json', state)
        print('status='+state['status']+' result_dir='+str(out), flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
