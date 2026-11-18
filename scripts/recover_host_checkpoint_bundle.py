"""Read-only export of a successful host prefix after its final inventory failed."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from host_independent_task import export_prefix, prefix_metadata, verify_repository_archive
from native_checkpoint_bundle import ARCHIVE_LIMIT, write
from validate_isolated_agentenv import ROOT
from isolated_storage import HOST_RESERVE, HOST_MARGIN, mount_identity


def recover(directory):
    directory=directory.resolve();assert directory.parent==ROOT/'.artifacts/storage-isolated-service'
    receipt=json.loads((directory/'result.json').read_text())
    assert receipt['status']=='FAIL' and receipt['cleanup']['host_file_residency_captured'] is False
    assert all(receipt['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','failed_fixture_retained'))
    assert json.loads((directory/'host-file-residency.json').read_text())['status']=='INCOMPLETE'
    prefix_metadata(directory/'prettier6604')
    image=directory/'volume.ext4';mount=directory/'checkpoint-export-mount';mount.mkdir(exist_ok=False)
    assert image.is_file() and not image.is_symlink()
    assert not subprocess.check_output(['losetup','-j',image],timeout=10).strip()
    assert not subprocess.check_output(['docker','ps','-aq','--filter','label=agentsandboxbench.run_id='+directory.name],timeout=15).strip()
    assert shutil.disk_usage(ROOT).free>HOST_RESERVE+HOST_MARGIN+ARCHIVE_LIMIT
    before=image.stat();mounted=False
    device=subprocess.check_output(['losetup','--find','--show','--read-only',image],text=True,timeout=10).strip()
    try:
        subprocess.run(['mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec',device,mount],check=True,timeout=30);mounted=True
        mount_identity(mount,device)
        value=export_prefix(mount/'env/snapshot-store',directory/'checkpoint-bundle',directory/'prettier6604')
    finally:
        if mounted:subprocess.run(['umount',mount],check=True,timeout=30)
        subprocess.run(['losetup','-d',device],check=True,timeout=15)
    after=image.stat();assert (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_size)==(after.st_dev,after.st_ino,after.st_mtime_ns,after.st_size)
    write(directory/'checkpoint-archive-verification.json',verify_repository_archive(directory/'checkpoint-bundle'))
    proof=dict(status='PASS',source_volume_unchanged=True,original_capture_reused=True,original_storage_status='FAIL',
        archive_bytes=value['archive_bytes'],archive_sha256=value['archive_sha256'],snapshot_id=value['capture']['checkpoint']['template'],
        scope='Read-only export only. Original final-inventory failure is retained; no guest or task rerun.')
    write(directory/'checkpoint-export-recovery.json',proof)
    print('status=PASS archive_bytes='+str(value['archive_bytes'])+' result_dir='+str(directory/'checkpoint-bundle'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if args.verify_only:
        directory=args.directory.resolve()
        assert directory.parent==ROOT/'.artifacts/storage-isolated-service'
        proof=verify_repository_archive(directory/'checkpoint-bundle')
        write(directory/'checkpoint-archive-verification.json',proof)
        print('status=PASS verified_files='+str(proof['verified_files'])+' result_dir='+str(directory))
    else:recover(args.directory)
