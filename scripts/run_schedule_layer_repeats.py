"""Nine rotating Prettier runs with explicit endpoint cache layers; no retries."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from isolated_storage import available, capacity_budget, GIB
from platform_tasks import ROOT
from prettier_no_proxy import clean_environment
from report_schedule_cache_layers import collect
from report_schedule_inventory import image_objects
from run_schedule_repeats import inspect_run, run_plan
from run_schedule_smoke import read, write


def command(output, schedule):
    return [sys.executable, '-B', str(ROOT/'scripts/run_schedule_smoke.py'),
            '--output', str(output), '--schedule', schedule,
            '--expanded-files', '--cache-layers', '--block-cache-method', 'cachestat',
            '--unused-network-pools']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(); out = args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077); out.mkdir(parents=True); (out/'code').mkdir()
    for name in ('run_schedule_layer_repeats.py', 'run_schedule_repeats.py',
                 'run_schedule_smoke.py', 'report_schedule_cache_layers.py',
                 'report_schedule_inventory.py', 'host_sandbox_monitor.py',
                 'sandbox_runtime_monitor.py', 'platform_tasks.py'):
        shutil.copy2(ROOT/'scripts'/name, out/'code'/name)
    plan = run_plan()
    write(out/'plan.json', dict(runs=plan, guest_count=7, repeats_per_schedule=3,
        ordinary_interval_seconds=.1, pss_interval_seconds=1, endpoint_pss=True,
        expanded_files=True, cache_layers=True, block_cache_method='cachestat',
        initial_lower_p1_count=19, p0=False, pressure=False, cache_reset=False,
        proxy=False, old_runs_included=False, default_reclaim=True,
        source_lifecycle='existing single-run driver starts and stops source for each run'))
    if not args.execute:
        print('status=PLAN_SAVED result_dir='+str(out)); return 0
    env = clean_environment(dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    state = dict(status='RUNNING', stage='preflight', expected_runs=9, runs=[],
                 started_utc=datetime.now(timezone.utc).isoformat())
    reference = None
    try:
        for item in plan:
            label = f"r{item['round']}-{item['schedule']}"
            state['stage'] = label+'-resource-check'; write(out/'result.json', state)
            memory = int(next(l for l in Path('/proc/meminfo').read_text().splitlines()
                              if l.startswith('MemAvailable:')).split()[1])*1024
            budget = capacity_budget(available(ROOT), 32*GIB)
            write(out/(label+'-resources.json'), dict(budget=budget,
                mem_available_bytes=memory, load=os.getloadavg(),
                time_utc=datetime.now(timezone.utc).isoformat()))
            assert budget['ready'] and memory > 64*GIB, 'resource budget insufficient; no automatic cleanup'
            run_dir = out/(label+'-'+out.name)
            argv = command(run_dir, item['schedule'])
            write(out/(label+'-command.json'), argv)
            row = dict(**item, status='RUNNING', source=str(run_dir/'result.json'))
            state['runs'].append(row); state['stage'] = label; write(out/'result.json', state)
            print('status=RUNNING run='+label+' result_dir='+str(out), flush=True)
            start = time.monotonic_ns()
            with (out/(label+'.log')).open('x') as log:
                process = subprocess.run(argv, cwd=ROOT, env=env, stdout=log,
                                         stderr=subprocess.STDOUT, timeout=1800)
            row['driver_seconds'] = (time.monotonic_ns()-start)/1e9
            row['driver_returncode'] = process.returncode
            saved = read(run_dir/'result.json')
            row['driver_status'] = saved['status']
            assert process.returncode == 0 and saved['status'] == 'PASS', str(run_dir/'result.json')
            assert len(saved['runs']) == 1
            original = saved['runs'][0]; row.update(storage=original['storage'], raw=original['raw'])
            verified = inspect_run(Path(row['storage']))
            layered = collect(run_dir/'result.json')
            verified.pop('status')
            row.update(verified)
            assert row['schedule'] == item['schedule']
            metrics = read(Path(row['raw'])/'schedule-smoke-metrics.json')
            identity = image_objects(metrics)
            if reference is None: reference = identity
            assert identity == reference and len(identity) == 19
            row.update(cache_layers_status=layered['layers']['status'],
                regular_status=layered['expanded']['status'],
                regular_query_window=layered['expanded']['query_window_regular'],
                block_cachestat=layered['layers']['block_devices']['method_totals']['cachestat'],
                tmpfs_status=layered['layers']['deleted_tmpfs']['status'],
                loop_status=layered['layers']['outer_loop']['status'],
                source_stopped=saved['source_returned_to_stopped'], cache_released=saved['cache_released'], status='PASS')
            write(out/'result.json', state)
            print('status=PASS run='+label+' cache_layers='+row['cache_layers_status'], flush=True)
        assert len({r['storage'] for r in state['runs']}) == len({r['service_cgroup'] for r in state['runs']}) == 9
        state.update(status='PASS', stage='complete', initial_reference=reference)
    except BaseException as error:
        state.update(status='FAIL', error=repr(error))
        if state['runs'] and state['runs'][-1]['status'] == 'RUNNING':
            state['runs'][-1].update(status='FAIL', error=repr(error))
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        state.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                     available_after_bytes=available(ROOT))
        write(out/'result.json', state)
        print('status='+state['status']+' result_dir='+str(out), flush=True)
    return 0 if state['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
