"""Back up a stopped, detached failed fixture, without deleting the source."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from prettier6604_inputs import ROOT,sha


def backup(directory):
    directory=directory.resolve()
    assert directory.parent==ROOT/'.artifacts/storage-isolated-service'
    receipt=json.loads((directory/'result.json').read_text())
    assert receipt['status']=='FAIL' and receipt['cleanup']['mount_and_loop_removed']
    assert receipt['cleanup']['owned_container_removed'] and receipt['cleanup']['failed_fixture_retained']
    source=directory/'volume.ext4';target=directory/'volume.ext4.gz'
    assert not subprocess.check_output(['losetup','-j',str(source)],timeout=10).strip()
    assert source.stat().st_size==receipt['capacity_bytes'] and not target.exists()
    assert shutil.disk_usage(ROOT).free>26*2**30, '20 GiB host reserve plus maximum 6 GiB backup required'
    identity=source.stat();digest=sha(source);started=time.monotonic_ns()
    with target.open('xb') as output,(directory/'backup.stderr').open('x') as log:
        producer=subprocess.Popen(['pigz','-1','-p','4','-c',str(source)],stdout=subprocess.PIPE,stderr=log)
        total=0
        try:
            while data:=producer.stdout.read(2**20):
                total+=len(data)
                assert total<=6*2**30 and shutil.disk_usage(ROOT).free>20*2**30,'backup size or host reserve exceeded'
                assert time.monotonic_ns()-started<240*10**9,'backup timeout'
                output.write(data)
            assert producer.wait(timeout=10)==0
        finally:
            if producer.poll() is None:producer.terminate();producer.wait(timeout=10)
    recovered=hashlib.sha256();count=0
    with gzip.open(target,'rb') as stream:
        while data:=stream.read(8*2**20):recovered.update(data);count+=len(data)
    after=source.stat();assert (identity.st_size,identity.st_mtime_ns,identity.st_ino)==(after.st_size,after.st_mtime_ns,after.st_ino)
    assert count==identity.st_size and recovered.hexdigest()==digest
    result=dict(status='PASS',source=str(source),backup=str(target),source_sha256=digest,backup_sha256=sha(target),
                uncompressed_bytes=count,compressed_bytes=target.stat().st_size,source_retained=True,
                elapsed_ns=time.monotonic_ns()-started,host_available_bytes=shutil.disk_usage(ROOT).free)
    (directory/'backup.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status=PASS compressed_bytes='+str(result['compressed_bytes'])+' result_dir='+str(directory))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    backup(parser.parse_args().directory)
