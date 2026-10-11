"""Bounded one-VM collector calibration followed by one complete functional Exp1 replay."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback

from archive_trenvx_run_data import sha256
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from run_trenvx_fixed_identity import inspect, cpu_policies, SOURCES as OLD_SOURCES, ROOT
from calibrate_guest_collector import audit as calibration_audit

SOURCES=tuple(dict.fromkeys(OLD_SOURCES+('run_guest_collector_calibration.py','calibrate_guest_collector.py','collector_fixed_work.c')))


def audit(out):
    saved=json.loads((out/'result.json').read_text());assert saved['status']=='PASS'
    raw=Path(saved['run']['raw_dir']);row,identity=inspect(raw,'off')
    for key,value in row.items():
        if key!='retained_allocated_bytes':assert saved['run'][key]==value
    assert identity==saved['identity']
    cfg=json.loads((raw/'config.json').read_text());assert cfg['collector_calibration'] and not cfg['lifecycle_calibration']
    assert cfg['collector_calibration_sources']=={name:sha256(raw/name) for name in cfg['collector_calibration_sources']}
    checked=calibration_audit(raw/'collector-calibration',json.loads((raw/'collector-calibration-result.json').read_text()))
    assert checked==saved['calibration']
    assert json.loads((out/'cpu-before.json').read_text())==json.loads((out/'cpu-after.json').read_text())
    hashes=json.loads((out/'code/sha256.json').read_text());assert hashes=={name:sha256(out/'code'/name) for name in hashes}
    return dict(status='PASS',short_call_count=36,full_replay_count=1,calibration=checked)


def main():
    os.umask(0o077)
    out=ROOT/'.artifacts/guest-collector-calibration'/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    out.mkdir(parents=True);(out/'code').mkdir()
    hashes={name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    before=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(before,indent=2)+'\n')
    protocol=dict(smoke_calls=4,maximum_formal_calls=32,order=['off','on','on','off'],
        vm_count=1,host_periodic_sampling=False,guest_sample_interval_seconds=.1,work_iterations=1_000_000_000,
        scope='synthetic fixed CPU work; same warm VM; no task overhead or physical-memory verdict',
        stop_policy='first failure/disk guard/source or CPU policy change; no retry',
        full_exp1_replay='one functional replay after calibration, excluded from performance contrasts')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');result=dict(status='FAIL',protocol=protocol)
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        plan=successful_image_cleanup_budget(shutil.disk_usage(out).free,1)
        (out/'storage-plan.json').write_text(json.dumps(plan,indent=2)+'\n');assert plan['status']=='READY',plan
        argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images','--record-phases',
            '--fixed-git-identity','--runner-lifecycle-diagnostic','--collector-calibration',
            '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
            '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
        with (out/'driver.log').open('w') as log:guarded_run(argv,directory=out,log=log,timeout=600)
        found=re.findall(r'result_dir=(\S+)',(out/'driver.log').read_text());assert len(found)==1
        raw=Path(found[0]).resolve(strict=True);assert raw.parent==(ROOT/'.artifacts/trenvx-service-smoke').resolve()
        row,identity=inspect(raw,'off');result.update(run=row,identity=identity)
        checked=calibration_audit(raw/'collector-calibration',json.loads((raw/'collector-calibration-result.json').read_text()))
        result['calibration']=checked
        assert hashes=={name:sha256(ROOT/'scripts'/name) for name in SOURCES},'source changed'
        assert cpu_policies()==before,'CPU policy changed'
        result['status']='PASS'
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',type=Path);args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main())
