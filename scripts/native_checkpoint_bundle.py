"""Offline archive of a stopped native POSIX snapshot repository, never a rootfs-only image."""
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import time
from validate_isolated_agentenv import IMAGE,immutable_manifest

ARCHIVE_LIMIT=5*2**29


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    with path.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')


class BoundedOutput:
    def __init__(self,stream,limit):self.stream=stream;self.limit=limit;self.count=0
    def write(self,data):
        if self.count+len(data)>self.limit:raise OSError('checkpoint archive exceeds its reserved space')
        value=self.stream.write(data);self.count+=value;return value
    def flush(self):self.stream.flush()


def export(source,output,metadata,limit=ARCHIVE_LIMIT):
    started=time.monotonic_ns();source=source.resolve();assert source.name=='snapshot-store'
    repository=source/'repository' if (source/'repository').is_dir() else source
    assert (repository/'catalog/records').is_dir()
    assert (repository/'snapshots').is_dir() and (repository/'managed-layers').is_dir()
    before=immutable_manifest(source);assert before and all(v['type']=='file' for v in before.values()),'unsupported repository link or special entry'
    assert metadata['server_image']==IMAGE and metadata['phase']=='capture' and metadata['status']=='PASS'
    output.mkdir(parents=True,exist_ok=False);archive=output/'repository.tar.gz'
    with archive.open('xb') as stream:
        bounded=BoundedOutput(stream,limit)
        with gzip.GzipFile(filename='',fileobj=bounded,mode='wb',compresslevel=1,mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w|',dereference=True) as tar:tar.add(source,arcname='snapshot-store')
    assert immutable_manifest(source)==before,'repository changed during offline export'
    result=dict(status='PASS',repository_prefix=str(repository.relative_to(source)),format='agentenv-posix-snapshot-bundle-v1',server_image=IMAGE,archive_sha256=sha(archive),archive_bytes=archive.stat().st_size,
        archive_limit_bytes=limit,repository_files=before,repository_file_bytes=sum(v['size'] for v in before.values()),
        capture=metadata,export_elapsed_ns=time.monotonic_ns()-started,
        scope='native POSIX catalog, VM state and managed memory/rootfs layers; fixed server/deps required; no original runtime cache, source-service credential directory or live-service database; VM memory may include secrets')
    write(output/'bundle.json',result);return result


def load(bundle):
    bundle=bundle.resolve();value=json.loads((bundle/'bundle.json').read_text())
    assert value['status']=='PASS' and value['format']=='agentenv-posix-snapshot-bundle-v1' and value['server_image']==IMAGE
    assert sha(bundle/'repository.tar.gz')==value['archive_sha256'],'checkpoint archive hash differs'
    return value


def restore(bundle,destination):
    started=time.monotonic_ns();value=load(bundle);destination=destination.resolve()
    assert destination.name=='snapshot-store' and not destination.exists()
    with tarfile.open(bundle/'repository.tar.gz','r:gz') as tar:
        members=tar.getmembers();names=set()
        for member in members:
            path=Path(member.name)
            assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='snapshot-store'
            assert member.isdir() or member.isfile(),'unsupported archive member'
            assert member.name not in names,'duplicate archive member';names.add(member.name)
        regular={str(Path(m.name).relative_to('snapshot-store')):m.size for m in members if m.isfile()}
        assert regular=={k:v['size'] for k,v in value['repository_files'].items()},'archive inventory differs'
        tar.extractall(destination.parent,filter='data')
    assert immutable_manifest(destination)==value['repository_files'],'imported repository hashes differ'
    return dict(status='PASS',archive_sha256=value['archive_sha256'],files_verified=len(regular),restore_elapsed_ns=time.monotonic_ns()-started)
