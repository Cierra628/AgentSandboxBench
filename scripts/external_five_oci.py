"""Digest-verified local OCI transport for five fixed workloads; no registry fallback."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil


def blob(store, descriptor):
    digest=descriptor['digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise ValueError('invalid OCI digest')
    path=store/digest[7:]
    if path.is_symlink() or not path.is_file():raise ValueError('missing regular OCI blob: '+str(path))
    before=path.stat()
    if 'size' in descriptor and before.st_size!=descriptor['size']:raise ValueError('OCI size differs')
    with path.open('rb') as f:actual='sha256:'+hashlib.file_digest(f,'sha256').hexdigest()
    after=path.stat()
    if actual!=digest or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('OCI digest/identity differs')
    return path


def validate(layout, origin):
    layout=Path(layout).resolve();store=layout/'blobs/sha256';expected=origin['verification']
    if json.loads((layout/'oci-layout').read_text())!={'imageLayoutVersion':'1.0.0'}:raise ValueError('OCI layout differs')
    refs=json.loads((layout/'index.json').read_text())['manifests']
    if len(refs)!=1 or refs[0]['digest']!=expected['manifest_digest'] or refs[0].get('annotations',{}).get('org.opencontainers.image.ref.name')!='latest':raise ValueError('fixed single latest OCI manifest required')
    manifest=json.loads(blob(store,refs[0]).read_text())
    if manifest['config']['digest']!=expected['config_digest'] or len(manifest['layers'])!=expected['layers']:raise ValueError('fixed config/layer count differs')
    config=json.loads(blob(store,manifest['config']).read_text())
    if (config['os'],config['architecture'])!=('linux','amd64') or config['rootfs']['diff_ids']!=expected['rootfs_diff_ids']:raise ValueError('runtime config platform/diff IDs differ')
    for layer in manifest['layers']:blob(store,layer)
    if sum(r['size'] for r in manifest['layers'])!=expected['layer_bytes']:raise ValueError('layer byte count differs')
    return dict(status='VERIFIED',layout=str(layout),manifest_digest=expected['manifest_digest'],
                config_digest=expected['config_digest'],layers=expected['layers'],
                scope=origin.get('rebuild_scope', 'Exact primary manifest/config/layer digests; native conversion unchanged'),
                primary_verification=origin.get('primary_verification')),manifest


def wrapper(real, image, digest, config_digest, base='/workspace/env/external-five-metadata'):
    repository=image.split('@')[0]
    if ':' in repository.rsplit('/',1)[-1]:repository=repository.rsplit(':',1)[0]
    aliases=(image,repository+'@'+digest)
    quote=shlex.quote
    body='#!/bin/sh\nset -eu\n'
    body+=f'if [ "$#" -eq 1 ] && [ "$1" = version ]; then exec cat {quote(base+"/version.txt")}; fi\n'
    for alias in set(aliases):
        body+=f'if [ "$#" -eq 5 ] && [ "$1" = manifest ] && [ "$2" = get ] && [ "$3" = --format ] && [ "$4" = raw-body ] && [ "$5" = {quote(alias)} ]; then exec cat {quote(base+"/manifest.json")}; fi\n'
        body+=f'if [ "$#" -eq 6 ] && [ "$1" = image ] && [ "$2" = copy ] && [ "$3" = --platform ] && [ "$4" = local ] && [ "$5" = {quote(alias)} ]; then\n case "$6" in ocidir:///workspace/env/image-cache/staging/*/oci:latest) ;; *) exit 78;; esac\n exec {quote(str(real))} image copy --platform local ocidir:///asb-external-five-oci:latest "$6"\nfi\n'
    body+=f'if [ "$#" -eq 4 ] && [ "$1" = blob ] && [ "$2" = get ] && [ "$3" = {quote(repository)} ] && [ "$4" = {quote(config_digest)} ]; then exec cat {quote(base+"/config.json")}; fi\n'
    return body+'echo "external-five: unsupported registry operation; no remote fallback" >&2\nexit 78\n'


def install(mount,layout,cfg,origin):
    from prettier_no_proxy import dependency_version
    receipt,manifest=validate(layout,origin);mount=Path(mount)
    entry=mount/'env/deps/regctl/v0.11.5/regctl';saved=entry.with_name('regctl.before-external-five')
    def elf(p):
        if not p.is_file() or p.is_symlink():return False
        with p.open('rb') as f:return f.read(4)==b'\x7fELF'
    real=next((p for p in (entry,entry.with_name('regctl.real')) if elf(p)),None)
    if real is None or saved.exists():raise ValueError('owned regctl ELF required')
    target=mount/'env/external-five-metadata';target.mkdir()
    (target/'version.txt').write_text(dependency_version(real))
    store=Path(layout)/'blobs/sha256'
    shutil.copy2(store/receipt['manifest_digest'][7:],target/'manifest.json')
    shutil.copy2(store/receipt['config_digest'][7:],target/'config.json')
    executable='/workspace/env/deps/regctl/v0.11.5/'+(saved.name if real==entry else real.name)
    from external_five import runtime_image
    image=runtime_image(cfg,origin)
    entry.rename(saved);entry.write_text(wrapper(executable,image,receipt['manifest_digest'],receipt['config_digest']));entry.chmod(0o755)
    return dict(receipt,api_image=image,source_image=cfg['image'],remote_fallback=False)


def verify_mount(info,layout):
    rows=[r for r in info['Mounts'] if r['Destination']=='/asb-external-five-oci']
    if len(rows)!=1 or rows[0]['Type']!='bind' or Path(rows[0]['Source']).resolve()!=Path(layout).resolve() or rows[0]['RW']:
        raise ValueError('verified OCI must be mounted read-only')
