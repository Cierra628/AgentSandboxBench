"""Export an already successful native checkpoint from its detached failed-export volume."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from native_checkpoint_bundle import export,ARCHIVE_LIMIT,write
from validate_isolated_agentenv import ROOT
from isolated_storage import HOST_RESERVE,HOST_MARGIN,mount_identity


def recover(directory):
    directory=directory.resolve();assert directory.parent==ROOT/'.artifacts/storage-isolated-service'
    receipt=json.loads((directory/'result.json').read_text())
    assert receipt['status']=='FAIL' and receipt['failed_stage']=='checkpoint-export'
    assert all(receipt['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','failed_fixture_retained'))
    captured=json.loads((directory/'python-backbone/result.json').read_text());assert captured['status']=='PASS' and captured['phase']=='capture'
    source=directory/'volume.ext4';mount=directory/'bundle-export-mount';mount.mkdir(exist_ok=False)
    assert source.is_file() and not source.is_symlink() and not subprocess.check_output(['losetup','-j',source],timeout=10).strip()
    assert not subprocess.check_output(['docker','ps','-aq','--filter','label=agentsandboxbench.run_id='+directory.name],timeout=15).strip()
    assert shutil.disk_usage(ROOT).free>HOST_RESERVE+HOST_MARGIN+ARCHIVE_LIMIT
    identity=source.stat();mounted=False;device=subprocess.check_output(['losetup','--find','--show','--read-only',source],text=True,timeout=10).strip()
    try:
        subprocess.run(['mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec',device,mount],check=True,timeout=30);mounted=True
        mount_identity(mount,device)
        value=export(mount/'env/snapshot-store',directory/'checkpoint-bundle',captured)
    finally:
        if mounted:subprocess.run(['umount',mount],check=True,timeout=30)
        subprocess.run(['losetup','-d',device],check=True,timeout=15)
    after=source.stat();assert (after.st_ino,after.st_mtime_ns,after.st_size)==(identity.st_ino,identity.st_mtime_ns,identity.st_size)
    proof=dict(status='PASS',archive_sha256=value['archive_sha256'],archive_bytes=value['archive_bytes'],repository_prefix=value['repository_prefix'],
        source_volume_unchanged=True,original_capture_reused=True,source_controller_status=captured['status'],original_storage_status=receipt['status'])
    write(directory/'checkpoint-export-recovery.json',proof)
    print('status=PASS archive_bytes='+str(value['archive_bytes'])+' result_dir='+str(directory/'checkpoint-bundle'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);recover(parser.parse_args().directory)
