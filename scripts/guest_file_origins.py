"""Resolve declared regular files through an attested OverlayFS layer order.

This is a file-source check, not a physical-memory measurement. Unsupported
redirects, metacopy, symlinks and changing files remain UNKNOWN.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


def overlay_layout(mountinfo, mountpoint='/', pivot_prefix=None):
    matches = []
    for line in mountinfo.splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split(); fs = right.split()
        if fields[4] == str(mountpoint) and fs[0] == 'overlay':
            matches.append((line, fs[2]))
    if len(matches) != 1:
        raise ValueError('exactly one overlay mount at the declared root required')
    line, raw = matches[0]
    if '\\' in raw:
        raise ValueError('escaped overlay options are unsupported')
    options = dict(part.split('=', 1) for part in raw.split(',') if '=' in part)
    lower = options.get('lowerdir', '').split(':')
    if not options.get('upperdir') or not all(lower):
        raise ValueError('upperdir and ordinary ordered lowerdir required; no data-only layers')
    paths = [options['upperdir'], *lower]
    if len(set(paths)) != len(paths):
        raise ValueError('duplicate layer directories')
    layers = []
    for index, name in enumerate(paths):
        path = Path(name)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid layer path')
        if pivot_prefix is not None:
            # Pinned TrEnv-X overlay-init mounted these before pivot_root.
            if index == 0 and name != '/overlay/root':
                raise ValueError('unexpected pinned upperdir')
            if index > 0 and name != '/' and not re.fullmatch(r'/overlay/dax-lowers/[1-9][0-9]*/delta', name):
                raise ValueError('unexpected pinned lowerdir')
            path = Path(pivot_prefix) / name.lstrip('/')
        if not path.is_dir() or path.is_symlink():
            raise ValueError('layer root unavailable or symlink')
        if any(parent.is_symlink() for parent in path.parents):
            raise ValueError('layer root has a symlink ancestor')
        origin = 'upper' if index == 0 else f'lower:{index-1}'
        if pivot_prefix is not None and index:
            origin = 'base' if name == '/' else 'checkpoint:' + name.split('/')[-2]
        layers.append(dict(origin=origin, root=str(path)))
    if pivot_prefix is not None and lower[-1] != '/':
        raise ValueError('pinned base must be last lower layer')
    nested = []
    for entry in mountinfo.splitlines():
        fields = entry.split(' - ', 1)[0].split()
        target = Path(fields[4])
        if target != Path(mountpoint) and target.is_relative_to(mountpoint):
            nested.append('/' + str(target.relative_to(mountpoint)))
    return dict(mountpoint=str(mountpoint), layers=layers, mountinfo=line, nested_mountpoints=nested,
        pivot_prefix=str(pivot_prefix) if pivot_prefix is not None else None)


def identity(path):
    value = path.lstat()
    return {key: getattr(value, key) for key in
        ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def layer_lookup(root, relative):
    """Inspect all ancestors; opaque directories only stop lower fallback."""
    candidate = root
    opaque = False
    trace = []
    for index, part in enumerate(['', *relative.parts]):
        if part: candidate = candidate / part
        leaf = index == len(relative.parts)
        try: info = identity(candidate)
        except FileNotFoundError:
            return dict(kind='hidden' if opaque else 'absent', reason='opaque' if opaque else 'absent', trace=trace)
        mode = info['st_mode']
        attrs = {name: os.getxattr(candidate, name, follow_symlinks=False).hex()
            for name in os.listxattr(candidate, follow_symlinks=False)}
        trace.append(dict(path=str(candidate), identity=info, xattrs=attrs))
        if stat.S_ISLNK(mode):
            raise ValueError('symlink path component unsupported')
        whiteout = stat.S_ISCHR(mode) and candidate.lstat().st_rdev == os.makedev(0, 0)
        for name, value in attrs.items():
            if not name.startswith(('trusted.overlay.', 'user.overlay.')): continue
            key = name.split('.overlay.', 1)[1]
            if key == 'origin': continue
            # Kernel OverlayFS uuid=on stores a 16-byte fs identity on the upper root.
            if key == 'uuid' and index == 0 and stat.S_ISDIR(mode) and len(value) == 32: continue
            if key == 'impure' and stat.S_ISDIR(mode) and value == b'y'.hex(): continue
            if key == 'opaque' and stat.S_ISDIR(mode) and value in (b'y'.hex(), b'x'.hex()):
                opaque = opaque or value == b'y'.hex(); continue
            if key == 'whiteout' and stat.S_ISREG(mode) and info['st_size'] == 0:
                whiteout = True; continue
            raise ValueError('unsupported overlay attribute: ' + name)
        if whiteout:
            return dict(kind='hidden', reason='whiteout', trace=trace)
        if not leaf and not stat.S_ISDIR(mode):
            return dict(kind='hidden', reason='non-directory ancestor', trace=trace)
        if leaf:
            if not stat.S_ISREG(mode): raise ValueError('declared target is not a regular file')
            return dict(kind='file', candidate=str(candidate), identity=info, trace=trace)
    raise AssertionError('empty relative file path')


def resolve_one(path, layout, visible_root, content=False):
    relative = Path(path.lstrip('/'))
    visible = Path(visible_root) / relative
    proof = []
    for layer in layout['layers']:
        found = layer_lookup(Path(layer['root']), relative)
        proof.append(dict(origin=layer['origin'], **found))
        if found['kind'] == 'absent': continue
        if found['kind'] == 'hidden':
            if os.path.lexists(visible): raise ValueError('predicted deletion disagrees with merged view')
            return dict(path=path, status='DELETED', origin=layer['origin'], reason=found['reason'], proof=proof)
        candidate = Path(found['candidate'])
        # Check every merged ancestor before stat or content reads.
        current = Path(visible_root)
        for part in relative.parts:
            current = current / part
            if current.is_symlink(): raise ValueError('merged path contains symlink')
        merged = identity(visible)
        if not stat.S_ISREG(merged['st_mode']): raise ValueError('merged view is not regular')
        if any(merged[key] != found['identity'][key] for key in ('st_size', 'st_mtime_ns')):
            raise ValueError('merged metadata differs from selected layer')
        row = dict(path=path, status='SOURCE_VERIFIED', origin=layer['origin'], candidate=str(candidate),
            visible=merged, underlying=found['identity'], proof=proof, content_verified=False)
        if content:
            if merged['st_size'] > 32 * 2**20: raise ValueError('content verification limited to 32 MiB per file')
            left = hashlib.sha256(visible.read_bytes()).hexdigest()
            right = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if left != right: raise ValueError('merged content differs from selected layer')
            if identity(visible) != merged or identity(candidate) != found['identity']:
                raise ValueError('file changed during content verification')
            row.update(content_verified=True, sha256=left)
        return row
    if os.path.lexists(visible): raise ValueError('unattributed merged file exists')
    return dict(path=path, status='NOT_FOUND', origin=None, proof=proof)


def observe(cfg, layout, visible_root='/', content=False):
    started = time.monotonic_ns()
    paths = [item if isinstance(item, str) else item['path'] for item in cfg['files']]
    if not paths or len(paths) > 256 or len(set(paths)) != len(paths):
        raise ValueError('one to 256 unique declared files required')
    if not all(str(Path(p)) == p and p.startswith('/testbed/') and '..' not in Path(p).parts for p in paths):
        raise ValueError('only normalized /testbed file paths supported')
    rows = []
    for item, path in zip(cfg['files'], paths):
        try:
            if any(Path(path).is_relative_to(target) for target in layout.get('nested_mountpoints', [])):
                raise ValueError('declared file is covered by another mount')
            before = resolve_one(path, layout, visible_root, content)
            after = resolve_one(path, layout, visible_root, False)
            if {k: v for k, v in before.items() if k not in ('sha256', 'content_verified')} != \
                    {k: v for k, v in after.items() if k != 'content_verified'}:
                raise ValueError('source or metadata changed during observation')
            if isinstance(item, dict):
                if 'expected_origin' in item and before['origin'] != item['expected_origin']:
                    raise ValueError('resolved source differs from expected origin')
                if 'expected_status' in item and before['status'] != item['expected_status']:
                    raise ValueError('resolved state differs from expected status')
            row = before
        except (OSError, ValueError) as error:
            row = dict(path=path, status='UNKNOWN', origin=None, error=str(error))
        row['physical_memory'] = dict(status='NOT_MEASURED', bytes=None)
        rows.append(row)
    return dict(status='PARTIAL' if any(r['status'] == 'UNKNOWN' for r in rows) else 'PASS',
        files=rows, layout=layout, content_reads=content, elapsed_ns=time.monotonic_ns()-started,
        scope='declared merged regular files only; source attribution, not physical cache counting',
        limitations=['Non-atomic metadata rechecks; content reads may warm caches',
            'Redirect/metacopy/symlink/data-only layers unsupported; unknown is never zero',
            'Deleted visible paths do not imply old backing-cache pages were reclaimed',
            'No physical pages, whole-task coverage or multi-VM sharing verdict'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--content', action='store_true')
    args = parser.parse_args()
    mountinfo = Path('/proc/self/mountinfo').read_text()
    layout = overlay_layout(mountinfo, pivot_prefix='/rom')
    value = observe(json.loads(args.manifest.read_text()), layout, content=args.content)
    if Path('/proc/self/mountinfo').read_text() != mountinfo:
        value['status'] = 'PARTIAL'; value['mountinfo_changed'] = True
    print(json.dumps(value, indent=2))
    raise SystemExit(0 if value['status'] == 'PASS' else 1)
