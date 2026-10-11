"""One owned step-022 restore with persistent metrics and declared-file analysis."""
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
from report_memory_metrics import report,write_report
from run_trenvx_file_origins import ROOT,SOURCES
from run_trenvx_fixed_identity import cpu_policies


def audit(driver):
    saved=json.loads((driver/'result.json').read_text());assert saved['status']=='PASS'
    raw=Path(saved['raw_dir']);cfg=json.loads((raw/'config.json').read_text())
    assert cfg['checkpoint_after']=='022' and cfg['guest_collector']=='persistent'
    assert cfg['guest_memory_sampling'] and cfg['host_sample_interval_seconds']==.1
    assert cfg['diagnostic_metrics'] and cfg['file_physical_manifest']
    run=json.loads((raw/'result.json').read_text())
    assert run['status']=='PASS' and all(v=='PASS' for v in run['checks'].values())
    memory=report(dict(backend='trenvx',mode='checkpoint-022-diagnostic',raw=str(raw)))
    assert memory==json.loads((driver/'memory/summary.json').read_text())
    assert memory['guest_composition']['status']=='PASS'
    assert memory['final_checks']['status']==memory['cleanup']['status']=='PASS'
    assert memory['restore_semantics']=='file-state-only'
    assert json.loads((raw/'checkpoint.json').read_text())['next_step']=='023'
    from measure_file_physical import audit_run
    for path in sorted((raw/'file-physical').iterdir()):
        measured=audit_run(path)
        assert measured['audit_status']==measured['measurement_status']=='PASS'
    official=json.loads((raw/'official-task-result.json').read_text())
    assert official['status']=='PASS' and official['parser_results']['num_tests_passed']==4
    assert json.loads((driver/'cpu-before.json').read_text())==json.loads((driver/'cpu-after.json').read_text())
    hashes=json.loads((driver/'code/sha256.json').read_text())
    assert hashes=={name:sha256(driver/'code'/name) for name in hashes}
    retention=run['data_retention']
    assert retention['status']=='PASS' and retention['mode']=='verified-archive'
    assert retention['archive_sha256']==sha256(raw/retention['archive'])
    return dict(status='PASS',restore_semantics='file-state-only',boundary='022 -> 023',
                monitoring_impact='NOT_MEASURED_BY_THIS_RUN',whole_task_physical_cache='NOT_MEASURED')


def main():
    os.umask(0o077)
    out=ROOT/'.artifacts/trenvx-checkpoint-monitoring'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True);(out/'code').mkdir()
    sources=tuple(dict.fromkeys(SOURCES+('run_trenvx_checkpoint_monitoring.py',)))
    hashes={name:sha256(ROOT/'scripts'/name) for name in sources}
    for name in sources:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    before=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(before,indent=2)+'\n')
    result=dict(status='FAIL',protocol=dict(vm_count=1,checkpoint_after='022',
        restore_semantics='file-state-only',guest_sampling='persistent 100ms per runner segment',
        host_sampling='owned parent cgroup 100ms',minimum_free_gib=48,
        cache_policy='preserve; no global drops',performance_comparison='NOT_RUN',
        stop_policy='first failure; no retry',image_retention='verified archive'))
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        free=shutil.disk_usage(out).free
        (out/'storage-plan.json').write_text(json.dumps(dict(free_bytes=free,required_free_bytes=48*2**30,
            status='READY' if free>48*2**30 else 'BLOCKED'),indent=2)+'\n')
        assert free>48*2**30,'checkpoint budget not met'
        argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--checkpoint-after','022',
            '--record-phases','--diagnostic-metrics','--file-physical-diagnostic',
            '--file-physical-manifest',str(ROOT/'configs/trenvx-exp1-named-files.json'),
            '--guest-memory-sampling','--guest-collector','persistent',
            '--guest-sample-interval','0.1','--host-sample-interval','0.1',
            '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
            '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
        with (out/'driver.log').open('w') as log:guarded_run(argv,directory=out,log=log,timeout=900)
        paths=re.findall(r'result_dir=(\S+)',(out/'driver.log').read_text());assert len(paths)==1
        raw=Path(paths[0]).resolve(strict=True);assert raw.parent==ROOT/'.artifacts/trenvx-service-smoke'
        result['raw_dir']=str(raw)
        memory=report(dict(backend='trenvx',mode='checkpoint-022-diagnostic',raw=str(raw)))
        write_report(memory,out/'memory')
        assert memory['guest_composition']['status']=='PASS'
        assert hashes=={name:sha256(ROOT/'scripts'/name) for name in sources},'source changed'
        assert cpu_policies()==before,'CPU policy changed'
        result['status']='PASS'
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':
        try:(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
        except BaseException as exc:
            result.update(status='FAIL',audit_error=repr(exc))
            (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            (out/'audit-failure.txt').write_text(traceback.format_exc())
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',type=Path)
    args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main())
