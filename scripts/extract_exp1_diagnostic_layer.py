"""Extract only a failed run's sealed layer and small template metadata, after hash verification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import time

from experiment_disk_guard import writer_lock, guarded_run

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def extract(run,out):
    checkpoint=json.loads((run/'checkpoint.json').read_text())
    template=checkpoint['template']
    archive=run/'data.tar.gz'
    expected=json.loads((run/'archive-manifest.json').read_text())['archive_sha256']
    assert sha(archive)==expected,'archive hash mismatch'
    members={f'data/templates/{template}/image/{name}':name for name in
             ('checkpoint-0001.ext4','config.json','state.json')}
    found={}
    with tarfile.open(archive,'r|gz') as tar:
        for member in tar:
            if member.name not in members:continue
            assert member.isfile(),'requested member is not a regular file'
            assert member.size <= (256*1024**2 if member.name.endswith('.ext4') else 1024**2)
            name=members[member.name]
            with tar.extractfile(member) as source,(out/name).open('xb') as target:
                shutil.copyfileobj(source,target)
            found[name]=dict(bytes=member.size,sha256=sha(out/name))
            if len(found)==len(members):break
    assert len(found)==len(members),'required template member missing'
    (out/'checkpoint-0001.ext4').chmod(0o444)
    source_hash=json.loads((run/'source-before.json').read_text())['stdout'].split()[0]
    result=dict(status='PASS',run=str(run),archive_sha256=expected,template=template,
                files=found,target='/testbed/src/language-html/utils/index.js',expected_target_sha256=source_hash)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--child':
        extract(Path(sys.argv[2]),Path(sys.argv[3]));sys.exit(0)
    if len(sys.argv)!=2:raise SystemExit('usage: extract_exp1_diagnostic_layer.py RUN_DIRECTORY')
    run=Path(sys.argv[1]).resolve(strict=True)
    assert run.parent==ROOT/'.artifacts/trenvx-service-smoke'
    os.umask(0o077)
    out=ROOT/'.artifacts/exp1-layer-extract'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    shutil.copy2(__file__,out/Path(__file__).name)
    with writer_lock(),(out/'driver.log').open('wb') as log:
        try:guarded_run([sys.executable,Path(__file__),'--child',run,out],directory=out,log=log,timeout=300)
        except Exception as exc:
            (out/'failure.txt').write_text(repr(exc)+'\n')
            print(f'status=FAIL result_dir={out}');sys.exit(1)
    print(f'status=PASS result_dir={out}')
