"""Pinned C4 OCI metadata and a local-only private regctl entrypoint."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil

from host_private_files import read,write
from prettier_no_proxy import dependency_version

IMAGE='docker.io/alexgshaw/reshard-c4-data@sha256:92d209e704785e17ad6b3f740e813dd72bc7ac33982ce435d197ab9d890eac2a'
DIGEST=IMAGE.split('@')[1]


def validate(directory):
    directory=Path(directory).resolve();layout=directory/'oci';store=layout/'blobs/sha256'
    raw=(store/DIGEST[7:]).read_bytes()
    if 'sha256:'+hashlib.sha256(raw).hexdigest()!=DIGEST:raise ValueError('fixed C4 manifest differs')
    manifest=json.loads(raw)
    if len(manifest['layers'])!=9 or sum(r['size'] for r in manifest['layers'])!=1432498021:raise ValueError('fixed C4 layers differ')
    if read(layout/'oci-layout')!={'imageLayoutVersion':'1.0.0'}:raise ValueError('OCI layout differs')
    refs=read(layout/'index.json')['manifests']
    if len(refs)!=1 or refs[0]['digest']!=DIGEST:raise ValueError('C4 OCI reference differs')
    receipt=directory/'validated.json'
    previous=read(receipt) if receipt.exists() else None
    identities={}
    for row in [manifest['config'],*manifest['layers']]:
        path=store/row['digest'][7:];s=path.lstat()
        if path.is_symlink() or not path.is_file() or s.st_size!=row['size']:raise ValueError('C4 blob incomplete')
        ident=dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns)
        # A saved successful digest check is reusable only for the same unchanged file.
        if previous is None or previous['files'].get(row['digest'])!=ident:
            with path.open('rb') as f:
                if 'sha256:'+hashlib.file_digest(f,'sha256').hexdigest()!=row['digest']:raise ValueError('C4 blob digest differs')
        identities[row['digest']]=ident
    cfg=read(store/manifest['config']['digest'][7:])
    if (cfg['os'],cfg['architecture'])!=('linux','amd64'):raise ValueError('C4 platform differs')
    value=dict(status='PASS',image=IMAGE,layer_count=9,blob_bytes=sum(r['size'] for r in [manifest['config'],*manifest['layers']]),files=identities)
    if previous!=value:write(receipt,value)
    return value,layout,manifest


def install(mount,directory):
    _,layout,manifest=validate(directory)
    mount=Path(mount);entry=mount/'env/deps/regctl/v0.11.5/regctl';saved=entry.with_name('regctl.before-c4-density')
    def is_elf(path):
        if not path.is_file():return False
        with path.open('rb') as stream:return stream.read(4)==b'\x7fELF'
    real=next((p for p in (entry,entry.with_name('regctl.real')) if is_elf(p)),None)
    if real is None or saved.exists():raise ValueError('private original regctl required')
    target=mount/'env/c4-local-metadata';target.mkdir();(target/'version.txt').write_text(dependency_version(real))
    shutil.copy2(layout/'blobs/sha256'/DIGEST[7:],target/'manifest.json')
    shutil.copy2(layout/'blobs/sha256'/manifest['config']['digest'][7:],target/'config.json')
    # Only the fixed manifest, config and local OCI copy are permitted; no remote fallback.
    base='/workspace/env/c4-local-metadata/'
    version='[ "$#" -eq 1 ] && [ "$1" = version ]'
    meta='[ "$#" -eq 5 ] && [ "$1" = manifest ] && [ "$2" = get ] && [ "$3" = --format ] && [ "$4" = raw-body ] && [ "$5" = '+shlex.quote(IMAGE)+' ]'
    config='[ "$#" -eq 4 ] && [ "$1" = blob ] && [ "$2" = get ] && [ "$3" = '+shlex.quote(IMAGE.split('@')[0])+' ] && [ "$4" = '+shlex.quote(manifest['config']['digest'])+' ]'
    copy='[ "$#" -eq 6 ] && [ "$1" = image ] && [ "$2" = copy ] && [ "$3" = --platform ] && [ "$4" = local ] && [ "$5" = '+shlex.quote(IMAGE)+' ]'
    body='#!/bin/sh\nset -eu\n'
    for condition,name in ((version,'version.txt'),(meta,'manifest.json'),(config,'config.json')):
        body+='if '+condition+'; then exec cat '+base+name+'; fi\n'
    real_container='/workspace/env/deps/regctl/v0.11.5/'+('regctl.before-c4-density' if real==entry else real.name)
    body+='if '+copy+'; then\n case "$6" in ocidir:///workspace/env/image-cache/staging/*/oci:latest) ;; *) exit 78;; esac\n exec '+shlex.quote(real_container)+' image copy --platform local ocidir:///asb-c4-oci:latest "$6"\nfi\necho "C4 density: remote registry fallback disabled" >&2\nexit 78\n'
    entry.rename(saved);entry.write_text(body);entry.chmod(0o755)
    return dict(status='INSTALLED',image=IMAGE,layout=str(layout),remote_fallback=False)


def converted_layers(mount):
    mount=Path(mount);cache=mount/'env/image-cache'
    config=read(cache/'configs'/('sha256-'+DIGEST[7:]+'-image.json'))
    if config.get('repoBlobUrl') or len(config['lowers'])!=9:raise ValueError('C4 native conversion incomplete')
    rows=[]
    for lower in config['lowers']:
        path=Path(lower['file']);relative=path.relative_to('/workspace/env/image-cache/commits')
        actual=cache/'commits'/relative
        if actual.stat().st_size!=lower['size']:raise ValueError('converted C4 size differs')
        rows.append(dict(relative=str(relative),source_digest=lower['digest'],size=lower['size']))
    if len({r['relative'] for r in rows})!=9:raise ValueError('C4 native lowers alias')
    return rows


def prepare_volume(config_home,service_url):
    """Use the native resolver without a guest, with a 600s HTTP conversion budget."""
    import re
    import tomllib
    import urllib.request
    from urllib.parse import urlsplit
    from agentenv_cold_start import NoRedirect
    url=urlsplit(service_url)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.path or url.query or url.fragment or url.username:
        raise ValueError('owned loopback service required')
    credentials=tomllib.loads((Path(config_home)/'aenv/credentials').read_text())
    if credentials['url']!=service_url or not credentials['api_key']:raise ValueError('owned credentials differ')
    request=urllib.request.Request(service_url+'/volumes',method='POST',
        data=json.dumps(dict(name='density-c4-input',mode='ro',sizeMB=65536,image=IMAGE)).encode(),
        headers={'Content-Type':'application/json','X-API-Key':credentials['api_key']})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=600) as response:
        if response.status!=201:raise RuntimeError('C4 preparation volume status differs')
        value=json.loads(response.read(65536))
    if not re.fullmatch('vol_[0-9a-f]{32}',value['volumeID']) or value['name']!='density-c4-input':raise ValueError('preparation volume identity differs')
    return dict(volume_id=value['volumeID'],name=value['name'],operation='Native image-backed volume creation, without a guest')
