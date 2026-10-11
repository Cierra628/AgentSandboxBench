"""Diagnostic boundary measurement; anonymous verification can warm host cache."""
import hashlib
import json
from pathlib import Path
import re

from measure_file_physical import collect
from trenvx_diagnostic_metrics import vmm_snapshot

SOURCE = '/testbed/src/language-html/utils/index.js'


def checkpoint_file(out, template):
    if not template or Path(template).name != template or template in ('.', '..'):
        raise ValueError('invalid checkpoint template name')
    directory = out/'data/templates'/template/'image'
    layers = sorted(directory.glob('checkpoint-*.ext4'))
    if len(layers) != 1:
        raise RuntimeError('this diagnostic requires exactly one sealed checkpoint layer')
    layer = layers[0].resolve(strict=True)
    if not layer.is_relative_to(out.resolve()) or layer.stat().st_mode & 0o222:
        raise RuntimeError('checkpoint layer must be run-owned and read-only')
    return layer


def guest_observation(raw):
    if raw.strip().splitlines()[-1] != '4096':
        raise RuntimeError('guest page size is not verified as 4096')
    values = {key:int(value)*1024 for key,value in
              re.findall(r'^(\w+):\s+(\d+) kB$', raw, re.M)}
    if not {'MemTotal','MemAvailable','Cached','AnonPages','Slab'} <= values.keys():
        raise RuntimeError('guest memory observation is incomplete')
    return dict(status='PASS', page_size=4096, meminfo_bytes=values, raw=raw,
                scope='guest meminfo/iomem/page size; not host physical memory')


def snapshot(out, label, sandbox_id, template, group, executable, guest_memory, named_files=None):
    directory = out/'file-physical'/label
    directory.mkdir(parents=True, exist_ok=False)
    layer = checkpoint_file(out, template)
    identity = vmm_snapshot(sandbox_id, group, executable)
    mapped=[dict(owner=SOURCE,backing=str(layer),guest_path='/delta'+SOURCE,
                 immutable_image=True,verify_anonymous_content=True)]
    if named_files is not None:
        mapped=[]
        for item in named_files:
            if item['origin']=='checkpoint':backing=layer;guest_path='/delta'+item['path']
            elif item['origin']=='base':
                backing=(out/'data/templates'/template/'image/rootfs.ext4').resolve(strict=True)
                if not backing.is_relative_to(out.resolve()):raise RuntimeError('base image outside run-owned directory')
                # Only the run-owned completed template image; do not change the seed.
                backing.chmod(backing.stat().st_mode & ~0o222)
                guest_path=item['path']
            else:raise ValueError('unsupported declared file origin')
            mapped.append(dict(owner=item['path'],backing=str(backing),guest_path=guest_path,
                               immutable_image=True,verify_anonymous_content=True))
    manifest = dict(page_size=4096, owned_root=str(out), vms=[dict(
        id=sandbox_id, pid=identity['pid'], start_time_ticks=identity['start_time_ticks'],
        executable=str(executable), cgroup=identity['cgroup'],
        api_socket=f'/tmp/vmm-{sandbox_id}.socket', guest_memory=guest_memory,
        files=mapped)])
    raw = (json.dumps(manifest, indent=2)+'\n').encode()
    (directory/'manifest.json').write_bytes(raw)
    result, pages = collect(manifest)
    result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    result['measurement_scope'] = ('explicit named files in sealed checkpoint and base; upper shadows checked separately' if named_files
        else 'one named file in sealed checkpoint; not merged upper or all task files')
    result['upper_file_content'] = dict(status='NOT_MEASURED', bytes=None)
    result['protocol'] = 'metadata scan plus byte comparison of resident anonymous copies; may warm backing cache; no cache drop'
    for name, value in [('result.json', result), ('pages.json', pages)]:
        (directory/name).write_text(json.dumps(value, indent=2)+'\n')
    if result['status'] != 'PASS':
        raise RuntimeError(f"file measurement failed: {result['first_failure']}")
    return result
