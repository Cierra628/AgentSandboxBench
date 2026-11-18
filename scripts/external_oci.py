"""Fixed external images: verified OCI input and local-only private transport."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil

from external_workload import NEW_TASKS,C4,CYTHON


def validate(directory,cfg):
    if cfg['task_id'] not in (*NEW_TASKS,C4,CYTHON,'django__django-16819'):raise ValueError('local OCI requires a fixed external task')
    directory=Path(directory).resolve();layout=directory/'oci';store=layout/'blobs/sha256'
    request=json.loads((directory/'request.json').read_text())
    if request['image']!=cfg['image'] or request['metadata_exit_code']!=0:raise ValueError('OCI request identity differs')
    digest=cfg['image'].split('@')[1];raw=(directory/'requested-manifest.json').read_bytes()
    if 'sha256:'+hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('requested manifest digest differs')
    original=json.loads(raw);index=json.loads((layout/'index.json').read_text())
    if len(index['manifests'])!=1:raise ValueError('one selected platform required')
    selected=index['manifests'][0];manifest_path=store/selected['digest'].split(':')[1]
    manifest_raw=manifest_path.read_bytes()
    if 'sha256:'+hashlib.sha256(manifest_raw).hexdigest()!=selected['digest']:raise ValueError('selected manifest digest differs')
    if original.get('manifests'):
        matches=[m for m in original['manifests'] if m['digest']==selected['digest'] and m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')=='amd64']
        if len(matches)!=1:raise ValueError('platform not bound to requested index')
    elif selected['digest']!=digest:raise ValueError('copy changed pinned manifest')
    manifest=json.loads(manifest_raw)
    if json.loads((layout/'oci-layout').read_text())!={'imageLayoutVersion':'1.0.0'}:raise ValueError('OCI layout differs')
    identities={};old=directory/'validated.json';previous=json.loads(old.read_text()) if old.exists() else {}
    for row in [manifest['config'],*manifest['layers']]:
        path=store/row['digest'].split(':')[1];s=path.lstat()
        if path.is_symlink() or not path.is_file() or s.st_size!=row['size']:raise ValueError('OCI blob incomplete')
        identity=dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns)
        if previous.get('files',{}).get(row['digest'])!=identity:
            with path.open('rb') as f:
                if 'sha256:'+hashlib.file_digest(f,'sha256').hexdigest()!=row['digest']:raise ValueError('OCI blob digest differs')
        identities[row['digest']]=identity
    config=json.loads((store/manifest['config']['digest'].split(':')[1]).read_text())
    if (config['os'],config['architecture'])!=('linux','amd64'):raise ValueError('OCI platform differs')
    value=dict(status='PASS',image=cfg['image'],platform_image=cfg['image'].split('@')[0]+'@'+selected['digest'],layer_count=len(manifest['layers']),blob_bytes=sum(x['size'] for x in [manifest['config'],*manifest['layers']]),files=identities)
    old.write_text(json.dumps(value,indent=2)+'\n')
    return value,layout,manifest


def install(mount,directory,cfg):
    value,layout,manifest=validate(directory,cfg);mount=Path(mount)
    entry=mount/'env/deps/regctl/v0.11.5/regctl';real=entry.with_name('regctl.real')
    with real.open('rb') as f:
        if f.read(4)!=b'\x7fELF':raise ValueError('private regctl.real executable required')
    saved=entry.with_name('regctl.before-external-oci')
    if saved.exists():raise ValueError('local transport already installed')
    meta=mount/'env/external-local-metadata';meta.mkdir()
    for image in {cfg['image'],value['platform_image']}:
        digest=image.split('@')[1];source=directory/'requested-manifest.json' if image==cfg['image'] else layout/'blobs/sha256'/digest.split(':')[1]
        shutil.copy2(source,meta/(digest.replace(':','-')+'.json'))
    shutil.copy2(layout/'blobs/sha256'/manifest['config']['digest'].split(':')[1],meta/'config.json')
    body='#!/bin/sh\nset -eu\n'
    for image in sorted({cfg['image'],value['platform_image']}):
        q=shlex.quote(image);filename=image.split('@')[1].replace(':','-')+'.json'
        body+='if [ "$#" -eq 5 ] && [ "$1" = manifest ] && [ "$2" = get ] && [ "$3" = --format ] && [ "$4" = raw-body ] && [ "$5" = '+q+' ]; then exec cat /workspace/env/external-local-metadata/'+filename+'; fi\n'
        body+='if [ "$#" -eq 6 ] && [ "$1" = image ] && [ "$2" = copy ] && [ "$3" = --platform ] && [ "$4" = local ] && [ "$5" = '+q+' ]; then\n case "$6" in ocidir:///workspace/env/image-cache/staging/*/oci:latest) ;; *) exit 78;; esac\n exec /workspace/env/deps/regctl/v0.11.5/regctl.real image copy --platform local ocidir:///asb-external-oci:latest "$6"\nfi\n'
    body+='if [ "$#" -eq 4 ] && [ "$1" = blob ] && [ "$2" = get ] && [ "$3" = '+shlex.quote(cfg['image'].split('@')[0])+' ] && [ "$4" = '+shlex.quote(manifest['config']['digest'])+' ]; then exec cat /workspace/env/external-local-metadata/config.json; fi\n'
    body+='if [ "$#" -eq 1 ] && [ "$1" = version ]; then exec /workspace/env/deps/regctl/v0.11.5/regctl.real version; fi\necho "external OCI: unsupported request, remote fallback disabled" >&2\nexit 78\n'
    entry.rename(saved);entry.write_text(body);entry.chmod(0o755)
    return dict(status='INSTALLED',image=cfg['image'],platform_image=value['platform_image'],remote_fallback=False)
