"""Recover only the unfinished 128 MiB condition; preserve all failure evidence."""
import gzip
import hashlib
import json
import struct


def archive_released_slots(directory,destination):
    """Only after owned guests and readers have stopped; never replace an inode."""
    paths=sorted(directory.glob('*.bin'));assert paths and not destination.exists()
    for p in paths:
        words=struct.unpack('<8Q',p.read_bytes()[:64])
        assert words[0]==0x31544f4c53425341 and words[1]%2==0 and words[5]==0 and words[6]==5
        assert p.with_suffix('.json').is_file()
    assert {p.stem for p in directory.glob('*.json')}=={p.stem for p in paths}
    destination.parent.mkdir(parents=True,exist_ok=True)
    directory.rename(destination);directory.mkdir()
    return dict(status='PASS',released_slots=len(paths),archive=str(destination))


def restore_volume(source,image):
    receipt=json.loads((source/'result.json').read_text())
    backup=json.loads((source/'backup.json').read_text())
    assert receipt['status']=='FAIL' and receipt['failed_stage']=='prettier6604-reused-capacity-128'
    assert receipt['cleanup']['owned_container_removed'] and receipt['cleanup']['mount_and_loop_removed']
    assert backup['status']=='PASS' and backup['uncompressed_bytes']==receipt['capacity_bytes']==image.stat().st_size
    started=image.stat();digest=hashlib.sha256();total=0
    with gzip.open(source/'volume.ext4.gz','rb') as reader,image.open('r+b') as writer:
        while block:=reader.read(8*2**20):
            total+=len(block);assert total<=backup['uncompressed_bytes']
            writer.write(block);digest.update(block)
    assert total==backup['uncompressed_bytes'] and digest.hexdigest()==backup['source_sha256']
    assert image.stat().st_ino==started.st_ino and image.stat().st_size==total
    return dict(status='PASS',source=str(source),restored_bytes=total,source_sha256=backup['source_sha256'])


def prepare_recovered_controller(mount):
    controller=mount/'controller';source=controller/'prettier6604-prefix-source'
    (controller/'prettier6604').rename(source)
    (controller/'prettier6604-128').rename(controller/'prettier6604-128-failed')
    (controller/'disk-samples.jsonl').rename(controller/'disk-samples-before-recovery.jsonl')
    slots=archive_released_slots(mount/'env/provider-slots',source/'released-provider-slots')
    return source,slots
