"""Read a verified old backup in a private RAM filesystem; never modify it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from prettier6604_inputs import ROOT
from validate_isolated_agentenv import IMAGE, immutable_manifest


def call(args, timeout=180):
    return subprocess.check_output(list(map(str,args)),stderr=subprocess.PIPE,text=True,timeout=timeout).strip()


def release(output):
    state_path=output/'resources.json'
    if not state_path.exists():return
    state=json.loads(state_path.read_text())
    assert any(output.is_relative_to(ROOT/'.artifacts'/name) for name in ('prettier6604','python-backbone','sphinx-backbone'))
    if state.get('readonly_mounted'):
        info=json.loads(call(['findmnt','-J','-M',output/'readonly','-o','SOURCE,TARGET']))['filesystems'][0]
        assert info['source']==state['loop_device']
        call(['umount',output/'readonly'],30);state['readonly_mounted']=False
        state_path.write_text(json.dumps(state,indent=2)+'\n')
    if state.get('loop_device'):
        actual=json.loads(call(['losetup','--list','--json','--output','NAME,BACK-FILE',state['loop_device']]))['loopdevices'][0]
        assert Path(actual['back-file']).resolve()==output/'memory/volume.ext4'
        call(['losetup','-d',state['loop_device']],30);state['loop_device']=None
        state_path.write_text(json.dumps(state,indent=2)+'\n')
    if state.get('memory_mounted'):
        info=json.loads(call(['findmnt','-J','-M',output/'memory','-o','FSTYPE,SOURCE']))['filesystems'][0]
        assert info['fstype']=='tmpfs' and info['source']=='asb-prettier6604-cache'
        call(['umount',output/'memory'],30);state['memory_mounted']=False
    state['released']=True;state_path.write_text(json.dumps(state,indent=2)+'\n')
    print('status=RELEASED result_dir='+str(output))


def xarray_backup_metadata(original,task_profile='xarray6992'):
    verification=json.loads((original/'backup.json').read_text())
    # Failed runs do not copy the child result into the outer success receipt.
    child=json.loads((original/'python-backbone/result.json').read_text())
    assert task_profile in ('xarray6992','sphinx7590')
    assert child['task_id']==('sphinx-doc__sphinx-7590' if task_profile=='sphinx7590' else 'pydata__xarray-6992')
    assert verification['uncompressed_bytes']==(20 if task_profile=='sphinx7590' else 24)*2**30
    if task_profile=='sphinx7590':
        cfg=json.loads((original/'python-backbone/prepared-input/config.json').read_text())
        assert cfg==json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
        assert child['replay']['action_count']==35 and child['checks']['actions_hash_order_expected_failures']=='PASS'
    return dict(verification,archive=verification['backup'],archive_sha256=verification['backup_sha256'],source_bytes=verification['uncompressed_bytes'])


def prepare(output,source_backup_directory=None,task_profile='xarray6992'):
    assert os.geteuid()==0
    os.umask(0o077)
    output=output.resolve()
    if task_profile=='sphinx7590':assert source_backup_directory is not None
    task=('sphinx-doc__sphinx-7590' if task_profile=='sphinx7590' else 'pydata__xarray-6992') if source_backup_directory else 'prettier__prettier-6604'
    assert output.is_relative_to(ROOT/'.artifacts'/('sphinx-backbone' if task_profile=='sphinx7590' else 'python-backbone' if source_backup_directory else 'prettier6604'))
    original=source_backup_directory.resolve() if source_backup_directory else ROOT/'.artifacts/storage-isolated-service/20261008T102942Z-3642885'
    assert original.parent==ROOT/'.artifacts/storage-isolated-service'
    verification=xarray_backup_metadata(original,task_profile) if source_backup_directory else json.loads((original/'volume-backup.json').read_text())
    archive=original/'volume.ext4.gz'
    assert verification['status']=='PASS' and Path(verification['archive'])==archive
    with archive.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==verification['archive_sha256']
    available=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
    assert available>64*2**30, '64 GiB RAM headroom required for temporary cache preparation'
    output.mkdir(parents=True,exist_ok=False)
    (output/'memory').mkdir();(output/'readonly').mkdir()
    state=dict(memory_mounted=False,readonly_mounted=False,loop_device=None,released=False)
    state_path=output/'resources.json';state_path.write_text(json.dumps(state,indent=2)+'\n')
    begin=time.monotonic_ns()
    try:
        call(['mount','-t','tmpfs','-o','size='+str(verification['source_bytes']//2**30+4)+'G,mode=0700','asb-prettier6604-cache',output/'memory'],30)
        state['memory_mounted']=True;state_path.write_text(json.dumps(state,indent=2)+'\n')
        image=output/'memory/volume.ext4'
        # Sparse writes to RAM avoid writing a second 20 GiB image onto the shared disk.
        with (output/'decompress.stderr').open('x') as log:
            producer=subprocess.Popen(['pigz','-dc','-p','4',archive],stdout=subprocess.PIPE,stderr=log)
            consumer=subprocess.Popen(['dd','bs=4M','iflag=fullblock','conv=sparse','status=none','of='+str(image)],stdin=producer.stdout,stderr=log)
            producer.stdout.close()
            try:
                assert consumer.wait(timeout=180)==0 and producer.wait(timeout=10)==0
            finally:
                for process in (consumer,producer):
                    if process.poll() is None:process.kill();process.wait(timeout=10)
        assert image.stat().st_size==verification['source_bytes']
        with image.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        assert digest==verification['source_sha256']
        header=call(['dumpe2fs','-h',image],30)
        (output/'filesystem-header.txt').write_text(header+'\n')
        assert 'Filesystem state:         clean' in header
        device=call(['losetup','--find','--show','--read-only',image],30)
        state['loop_device']=device;state_path.write_text(json.dumps(state,indent=2)+'\n')
        call(['mount','-t','ext4','-o','ro,noload',device,output/'readonly'],30)
        state['readonly_mounted']=True;state_path.write_text(json.dumps(state,indent=2)+'\n')
        parts=['deps']+['image-cache/'+s for s in ('configs','indexes','commits','remote-blocks','convert-blocks')]
        env=output/'readonly/env'
        manifest={part:immutable_manifest(env/part) for part in parts}
        seed=dict(status='PASS',task_id=task,server_image=IMAGE,source_env=str(env),loop_device=device,
                  source_backup=str(archive),backup_sha256=verification['archive_sha256'],uncompressed_sha256=digest,
                  parts=manifest,elapsed_ns=time.monotonic_ns()-begin,ram_allocated_bytes=image.stat().st_blocks*512,
                  scope='immutable cache only; original VM state, RocksDB, secrets and uppers are not copied; functional warm-cache preparation')
        if task_profile=='sphinx7590':seed['task_image']=json.loads((original/'python-backbone/prepared-input/config.json').read_text())['source_image']
        (output/'seed.json').write_text(json.dumps(seed,indent=2)+'\n')
        print('status=PASS result_dir='+str(output))
    except BaseException:
        release(output);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--release',action='store_true')
    parser.add_argument('--source-backup-directory',type=Path,help='Verified Xarray or Sphinx backup; output must be under the selected task cache root')
    parser.add_argument('--task-profile',choices=['xarray6992','sphinx7590'],default='xarray6992')
    args=parser.parse_args()
    if args.release:release(args.output.resolve())
    else:prepare(args.output,args.source_backup_directory,args.task_profile)
