"""Local OCI transport for one owned service; conversion stays native."""
import os
from pathlib import Path
from prepare_xarray6992_image import validate,SPHINX_IMAGE
from xarray6992_inputs import IMAGE,sha
from isolated_storage import GIB

PARTS=['deps']+['image-cache/'+n for n in ('configs','indexes','commits','remote-blocks','convert-blocks')]


def allocated(source):
    seen=set();total=0
    for part in PARTS:
        for file in (source/part).rglob('*'):
            info=file.lstat()
            if not file.is_symlink() and file.is_file() and (info.st_dev,info.st_ino) not in seen:
                total+=info.st_blocks*512;seen.add((info.st_dev,info.st_ino))
    return total


def budget(source,prepared,capacity,image=IMAGE):
    result,layout=validate(prepared) if image==IMAGE else validate(prepared,image)
    estimate=allocated(source)+result['downloaded_bytes']+2*result['unpacked_regular_rounded_bytes']+int(5.5*GIB)
    allowed=(18,20,22,24,28,32) if image==SPHINX_IMAGE else (24,28,32)
    assert capacity in tuple(n*GIB for n in allowed) and estimate<=capacity-2*GIB,'native conversion estimate exceeds volume budget'
    return dict(capacity_bytes=capacity,source_allocated_bytes=allocated(source),oci_bytes=result['downloaded_bytes'],
        unpacked_regular_rounded_bytes=result['unpacked_regular_rounded_bytes'],estimated_peak_bytes=estimate,
        extra_private_metadata_cleanup_bytes=int(5.5*GIB),unallocated_margin_bytes=capacity-estimate,
        scope='estimate from actual compressed and tar file sizes; native conversion peak still requires observation; hard volume limits writes')


def wrapper(real,layout,image=IMAGE):
    IMAGE=image
    return '#!/bin/sh\nset -eu\nif [ "$#" -eq 6 ] && [ "$1" = image ] && [ "$2" = copy ] && [ "$3" = --platform ] && [ "$4" = local ] && [ "$5" = "'+IMAGE+'" ]; then\n  case "$6" in ocidir:///workspace/env/image-cache/staging/*/oci:latest) ;; *) echo "invalid owned OCI destination" >&2; exit 2;; esac\n  echo "asb: copying digest-verified local OCI layers; native conversion unchanged" >&2\n  exec "'+str(real)+'" image copy --platform local "ocidir://'+str(layout)+':latest" "$6"\nfi\nif [ "$#" -ge 2 ] && [ "$1" = image ] && [ "$2" = copy ]; then\n  for argument in "$@"; do\n    if [ "$argument" = "'+IMAGE+'" ]; then echo "unsupported pinned image copy arguments" >&2; exit 2; fi\n  done\nfi\nexec "'+str(real)+'" "$@"\n'


def install(mount,image=IMAGE):
    path=mount/'env/deps/regctl/v0.11.5/regctl';assert path.is_file() and not path.is_symlink()
    before=sha(path);real=path.with_name('regctl.real');assert not real.exists()
    path.rename(real);path.write_text(wrapper(Path('/workspace/env/deps/regctl/v0.11.5/regctl.real'),Path('/asb-frozen-oci'),image))
    path.chmod(0o755)
    return dict(real_binary_sha256=before,wrapper_sha256=sha(path),scope='private copied dependency only; source-service binary unchanged; intercepts one pinned image copy')
