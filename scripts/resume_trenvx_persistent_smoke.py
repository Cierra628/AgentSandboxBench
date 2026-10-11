"""Recover completed smoke runs without overwriting the interrupted batch."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import time
from archive_trenvx_run_data import sha256
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from run_trenvx_persistent_comparison import ROOT, inspect_run, summarize, METRICS, audit


def main(previous):
    previous=previous.resolve(strict=True)
    assert not (previous/'result.json').exists(), 'only interrupted, unfinished smoke is supported'
    protocol=json.loads((previous/'protocol.json').read_text())
    assert protocol['stage']=='smoke' and protocol['group_count']==1
    hashes=json.loads((previous/'code/sha256.json').read_text())
    assert hashes=={name:sha256(ROOT/'scripts'/name) for name in hashes}, 'source changed; do not resume'
    os.umask(0o077)
    out=previous.parent/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}-recovery')
    out.mkdir();shutil.copytree(previous/'code',out/'code');shutil.copy2(Path(__file__),out/'recovery.py')
    protocol.update(recovered_from=str(previous),recovery_script_sha256=sha256(Path(__file__)),
        interrupted_batch_semantics='functional smoke only; interrupted original kept; not formal statistical observations')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    rows=[];result=dict(status='FAIL',protocol=protocol,groups=[rows])
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        for position,label in enumerate(protocol['orders'][0],1):
            assert hashes=={name:sha256(ROOT/'scripts'/name) for name in hashes}
            old=previous/f'group-1-run-{position}-{label}'
            if old.exists():
                found=re.findall(r'result_dir=(\S+)',(old/'driver.log').read_text());assert len(found)==1
                raw=Path(found[0]).resolve(strict=True)
            else:
                directory=out/f'group-1-run-{position}-{label}';directory.mkdir()
                plan=successful_image_cleanup_budget(shutil.disk_usage(out).free,len(protocol['orders'][0])-len(rows))
                (directory/'storage-plan.json').write_text(json.dumps(plan,indent=2)+'\n');assert plan['status']=='READY'
                # In this observed interruption, only the final off run is missing.
                assert position==8 and label=='off', 'unexpected missing condition; inspect first'
                argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images','--record-phases',
                    '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                    '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
                with (directory/'driver.log').open('w') as log:guarded_run(argv,directory=directory,log=log,timeout=600)
                found=re.findall(r'result_dir=(\S+)',(directory/'driver.log').read_text());assert len(found)==1
                raw=Path(found[0]).resolve(strict=True)
            assert raw.parent==(ROOT/'.artifacts/trenvx-service-smoke').resolve()
            row,identity=inspect_run(raw,label)
            if 'identity' not in result:result['identity']=identity
            assert result['identity']==identity
            row.update(group=1,position=position);rows.append(row)
            (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
            print(f'status=REPLAY_PASS position={position}/8 case={label} result_dir={out}',flush=True)
        result.update(status='PASS',analysis=summarize([rows],METRICS))
    except BaseException as exc:
        result['error']=repr(exc)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('previous',type=Path)
    raise SystemExit(main(parser.parse_args().previous))
