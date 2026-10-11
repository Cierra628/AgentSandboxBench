"""Remove only an explicitly approved, backed-up, detached failed volume."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
from xarray6992_inputs import ROOT,sha


def remove(directory,approval):
    directory=directory.resolve();assert directory.parent==ROOT/'.artifacts/storage-isolated-service'
    assert approval.strip() and not (directory/'approved-volume-cleanup.json').exists()
    backup=json.loads((directory/'backup.json').read_text());receipt=json.loads((directory/'result.json').read_text())
    assert receipt['status']=='FAIL' and receipt['cleanup']['mount_and_loop_removed'] and receipt['cleanup']['owned_container_removed']
    source=directory/'volume.ext4';archive=directory/'volume.ext4.gz'
    assert source.is_file() and not source.is_symlink() and archive.is_file() and not archive.is_symlink()
    assert not subprocess.check_output(['losetup','-j',str(source)],timeout=10).strip()
    assert not subprocess.check_output(['docker','ps','-aq','--filter','label=agentsandboxbench.run_id='+directory.name],timeout=15).strip()
    assert backup['status']=='PASS' and backup['source']==str(source) and backup['backup']==str(archive)
    assert sha(archive)==backup['backup_sha256'] and sha(source)==backup['source_sha256']
    assert source.stat().st_size==backup['uncompressed_bytes']
    available=shutil.disk_usage(ROOT).free;allocated=source.stat().st_blocks*512
    source.unlink()
    result=dict(status='PASS',authorization=approval,deleted=str(source),allocated_bytes=allocated,backup=str(archive),
        backup_sha256=backup['backup_sha256'],source_sha256=backup['source_sha256'],host_available_before_bytes=available,
        host_available_immediate_after_bytes=shutil.disk_usage(ROOT).free,timestamp_ns=time.time_ns(),
        scope='only explicitly approved volume; complete backup and all other files retained')
    with (directory/'approved-volume-cleanup.json').open('x') as stream:stream.write(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print('status=PASS available_bytes='+str(result['host_available_immediate_after_bytes'])+' result_dir='+str(directory))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--approval-text',required=True)
    args=parser.parse_args();remove(args.directory,args.approval_text)
