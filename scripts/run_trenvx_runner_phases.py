"""At most four serial diagnostic replays, with fixture calibration in the first VM."""
import csv
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback

from archive_trenvx_run_data import allocated_bytes, sha256
from audit_exp1_metrics import audit_run
from calibrate_trenvx_runner import audit as audit_calibration
from experiment_disk_guard import guarded_run
from guest_runner_phases import collect
from plan_trenvx_storage import successful_image_cleanup_budget
from run_trenvx_monitor_balanced import IDENTITY, SOURCES as OLD_SOURCES

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple(dict.fromkeys(OLD_SOURCES+('run_trenvx_runner_phases.py','calibrate_trenvx_runner.py',
    'guest_runner_phases.py','command_clock.c','audit_exp1_metrics.py','trenvx_diagnostic_metrics.py',
    'exp1_official_task.py','49-run-trenvx-runner-phases.sh')))


def inspect(raw, calibration):
    cfg = json.loads((raw/'config.json').read_text())
    run = json.loads((raw/'result.json').read_text())
    assert cfg['runner_phases'] and cfg['runner_phase_calibration'] == calibration
    assert cfg['checkpoint_after'] is None and cfg['discard_success_images'] and cfg['record_phases']
    assert not cfg['guest_memory_sampling'] and not cfg['host_sample_interval_seconds']
    assert not cfg['diagnostic_metrics'] and not cfg['file_physical_diagnostic']
    assert run['status'] == 'PASS' and all(v == 'PASS' for v in run['checks'].values())
    checked = audit_run(dict(backend='trenvx', mode='full', raw=str(raw)))
    assert checked['final_checks']['status'] == 'PASS' and checked['cleanup']['status'] == 'PASS'
    official = json.loads((raw/'official-task-result.json').read_text())
    assert official['status'] == 'PASS' and official['parser_results']['num_tests_passed'] == 4
    steps = list(csv.DictReader((raw/'replay/steps.tsv').read_text().splitlines(), delimiter='\t'))
    phases = collect(raw/'replay', sum(int(r['duration_ns']) for r in steps), baseline_commands=True)
    assert phases == json.loads((raw/'runner-phases.json').read_text())
    retained = allocated_bytes(raw); assert retained <= 128*2**20
    assert not (raw/'data').exists()
    row = dict(run_id=raw.name, raw_dir=str(raw), calibration_before_replay=calibration,
               phases=phases, action_ns={r['step']:int(r['duration_ns']) for r in steps},
               timing_ns=checked['timing_ns'], patch_sha256=checked['final_patch_sha256'],
               retained_allocated_bytes=retained)
    if calibration:
        row['calibration'] = audit_calibration(raw/'runner-calibration')
        assert row['calibration'] == json.loads((raw/'runner-calibration-audit.json').read_text())
    return row, {key:cfg[key] for key in IDENTITY}


def audit(out):
    saved = json.loads((out/'result.json').read_text())
    assert saved['status'] == 'PASS' and len(saved['runs']) == 4
    hashes = json.loads((out/'code/sha256.json').read_text())
    assert hashes == {name:sha256(out/'code'/name) for name in hashes}
    for index, row in enumerate(saved['runs']):
        measured, identity = inspect(Path(row['raw_dir']), index == 0)
        # Offline calculation may create tiny additional files; retained bytes are not a timing metric.
        assert {k:v for k,v in measured.items() if k!='retained_allocated_bytes'} == {
            k:v for k,v in row.items() if k!='retained_allocated_bytes'}
        assert identity == saved['identity']
    return dict(status='PASS', run_count=4, correctness='PASS', calibration=saved['runs'][0]['calibration'],
                monitoring_acceptance=False, scope='diagnostic variant; no new monitored/control comparison')


def main():
    os.umask(0o077)
    out = ROOT/'.artifacts/trenvx-runner-phases'/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    out.mkdir(parents=True); (out/'code').mkdir()
    hashes = {name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:
        shutil.copy2(ROOT/'scripts'/name, out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes, indent=2)+'\n')
    protocol = dict(replays=4, calibration_before_first=True, fixture_files=256, calibration_iterations=32,
                    performance_threshold=None, cpu_cache_policy='original; no drops, affinity or CPU policy changes',
                    sampling='off in all runs', checkpoint_restore=False,
                    stop_policy='first failure, source change, oversized raw result or disk guard; no retries',
                    scope='diagnostic phase timing; first run includes prior fixture activity; not formal overhead comparison')
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    result = dict(status='FAIL', protocol=protocol, runs=[])
    print(f'status=RUNNING result_dir={out}', flush=True)
    try:
        for index in range(4):
            assert hashes == {name:sha256(ROOT/'scripts'/name) for name in SOURCES}, 'source changed'
            directory = out/f'run-{index+1}'; directory.mkdir()
            plan = successful_image_cleanup_budget(shutil.disk_usage(out).free, 4-index)
            (directory/'storage-plan.json').write_text(json.dumps(plan, indent=2)+'\n')
            assert plan['status'] == 'READY', plan
            argv = ['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images',
                    '--record-phases','--runner-phases', '--reference',
                    str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                    '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
            if index == 0:
                argv += ['--runner-phase-calibration']
            with (directory/'driver.log').open('w') as log:
                guarded_run(argv, directory=directory, log=log, timeout=600)
            found = re.findall(r'result_dir=(\S+)', (directory/'driver.log').read_text())
            assert len(found) == 1
            raw = Path(found[0]).resolve(strict=True)
            assert raw.parent == (ROOT/'.artifacts/trenvx-service-smoke').resolve()
            row, identity = inspect(raw, index == 0)
            if 'identity' not in result:
                result['identity'] = identity
            assert identity == result['identity'], 'environment changed'
            result['runs'].append(row)
            (out/'progress.json').write_text(json.dumps(result, indent=2)+'\n')
            print(f'status=REPLAY_PASS run={index+1}/4 result_dir={out}', flush=True)
        result['status'] = 'PASS'
    except BaseException as exc:
        result['error'] = repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    if result['status'] == 'PASS':
        (out/'offline-audit.json').write_text(json.dumps(audit(out), indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}", flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
