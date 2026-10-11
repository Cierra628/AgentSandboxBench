"""Inventory visible ext4 regular files and only touch initially resident pages."""
import ctypes
from contextlib import contextmanager, ExitStack
import hashlib
import json
import mmap
import os
from pathlib import Path
import resource
import stat
import struct
import sys
import time

from guest_named_file_probe import file_identity, resident
from guest_whole_cache_probe import ram_ranges, snapshot, wait

PAGE=4096
MASK=(1<<55)-1


def catalog(root, deadline):
    mounts=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right=line.split(' - ',1);name=left.split()[4]
        assert '\\' not in name,'escaped mountpoint unsupported'
        mounts.append((Path(name),right.split()[0]))
    def filesystem(path):
        matching=[(p,kind) for p,kind in mounts if path.is_relative_to(p)]
        return max(matching,key=lambda r:len(str(r[0])))[1]
    assert root.is_absolute() and not root.is_symlink() and filesystem(root)=='ext4'
    files={};excluded={};errors=[];paths=0
    pending=[root]
    while pending:
        assert time.monotonic()<deadline,'inventory deadline reached'
        directory=pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path=Path(entry.path)
                    if filesystem(path)!='ext4':
                        kind='other-filesystem';excluded[kind]=excluded.get(kind,0)+1;continue
                    info=entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        paths+=1;assert paths<=200000,'file inventory budget exceeded'
                        key=(info.st_dev,info.st_ino)
                        item=files.setdefault(key,dict(path=str(path),aliases=[],identity=file_identity(info)))
                        assert item['identity']==file_identity(info),'hardlink identity changed'
                        item['aliases'].append(str(path))
                    else:
                        kind='symlink' if stat.S_ISLNK(info.st_mode) else 'special-file'
                        excluded[kind]=excluded.get(kind,0)+1
        except OSError as exc:errors.append(dict(path=str(directory),error=str(exc)))
    return dict(files=sorted(files.values(),key=lambda r:r['path']),regular_paths=paths,
                excluded_entry_counts=excluded,errors=errors)


def capture(item, pm, flags, selected=None):
    result=dict(item,pages=[],status='PASS')
    path=Path(item['path']);expected=item['identity']
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        assert file_identity(os.fstat(fd))==file_identity(path.stat())==expected,'file identity changed'
        size=expected['st_size'];result['total_pages']=(size+PAGE-1)//PAGE
        if not size:result['resident_indices']=[];return result
        assert size<=512*2**20,'file exceeds 512 MiB mapping budget'
        with mmap.mmap(fd,0,access=mmap.ACCESS_COPY) as mapped:
            address=ctypes.addressof(ctypes.c_char.from_buffer(mapped));initial=resident(mapped,address)
            indices=[i for i,value in enumerate(initial) if value] if selected is None else selected
            result['resident_indices']=[i for i,value in enumerate(initial) if value]
            result['selected_indices']=indices
            for index in indices:
                if not initial[index]:
                    result['pages'].append(dict(index=index,status='NONRESIDENT'));continue
                offset=index*PAGE;valid=min(PAGE,size-offset)
                digest=hashlib.sha256(mapped[offset:offset+valid]).hexdigest()
                entry=struct.unpack('<Q',os.pread(pm,8,(address//PAGE+index)*8))[0]
                assert entry&(1<<63) and entry&(1<<61) and entry&MASK,'guest resident file PFN missing/hidden/nonfile'
                pfn=entry&MASK;kflags=struct.unpack('<Q',os.pread(flags,8,pfn*8))[0]
                assert entry==struct.unpack('<Q',os.pread(pm,8,(address//PAGE+index)*8))[0],'guest mapping changed'
                result['pages'].append(dict(index=index,valid_bytes=valid,pfn=pfn,flags=kflags,sha256=digest,status='PASS'))
        assert file_identity(os.fstat(fd))==file_identity(path.stat())==expected,'file changed during capture'
    finally:os.close(fd)
    return result


def collect(items, previous=None):
    values=[];selected_pages=0;deadline=time.monotonic()+75
    with open('/proc/self/pagemap','rb',buffering=0) as pm,open('/proc/kpageflags','rb',buffering=0) as flags:
        for item in items:
            assert time.monotonic()<deadline,'file collection deadline reached'
            try:
                selected=previous[item['path']].get('selected_indices',[]) if previous is not None else None
                row=capture(item,pm.fileno(),flags.fileno(),selected)
            except (OSError,AssertionError) as exc:row=dict(item,status='UNKNOWN',error=str(exc),pages=[])
            selected_pages+=len(row['pages']);assert selected_pages<=262144,'resident-page budget exceeded'
            values.append(row)
    return values


def fixture(root):
    root.mkdir(exist_ok=False)
    for name,data in [('hot-a',b'\x11'*PAGE+b'\x22'*PAGE),('hot-tail',b'T'*123),('cold',b'C'*(3*PAGE)),('empty',b'')]:
        with (root/name).open('wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
            if name=='cold':os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED)
    with (root/'sparse').open('wb') as stream:stream.truncate(6*PAGE)
    os.link(root/'hot-a',root/'hot-alias');(root/'hot-link').symlink_to('hot-a')
    for name in ('hot-a','hot-tail'):(root/name).read_bytes()


@contextmanager
def hold_fixture_pages(root):
    """Keep only the three owned calibration pages resident until release."""
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mlock.argtypes=libc.munlock.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    def locked_bytes():
        return next(int(line.split()[1])*1024 for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmLck:'))
    initial_locked=locked_bytes()
    with ExitStack() as stack:
        for name,size in (('hot-a',2*PAGE),('hot-tail',123)):
            stream=stack.enter_context((root/name).open('r+b'))
            assert os.fstat(stream.fileno()).st_size==size
            # Shared mapping locks the file pages; a private writable mapping
            # can populate anonymous CoW pages during mlock on older kernels.
            mapped=stack.enter_context(mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_WRITE))
            address=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
            if libc.mlock(address,len(mapped)):
                raise OSError(ctypes.get_errno(),'mlock calibration file')
            stack.callback(libc.munlock,address,len(mapped))
        added=locked_bytes()-initial_locked
        assert added==3*PAGE,'calibration locked-memory accounting differs'
        yield dict(mode='mlock',bytes=3*PAGE,process_locked_bytes_delta=added,
                   scope='only the three owned hot calibration pages; task files and cache policy unchanged')


def main():
    cfg=json.loads(Path(sys.argv[1]).read_text());out=Path(sys.argv[2]);root=Path(cfg['root'])
    assert out.parent==Path('/dev/shm') and out.name.startswith('asb-file-inventory-')
    out.mkdir(exist_ok=False);started=time.monotonic_ns()
    held=ExitStack()
    try:
        if cfg['mode']=='fixture':fixture(root)
        retention=None
        if cfg.get('retain_fixture_pages',False):
            assert cfg['mode']=='fixture','retention is only for owned calibration files'
            retention=held.enter_context(hold_fixture_pages(root))
        listing=catalog(root,time.monotonic()+75)
        before=collect(listing['files'])
        if retention:
            pages=[p for f in before for p in f['pages']]
            # Unevictable page flags may be updated after mlock returns.
            # VmLck proves the lock; file-LRU/inode/content are checked paused.
            assert len(pages)==3 and all(p['status']=='PASS' for p in pages), 'locked calibration page export failed: '+json.dumps(before,separators=(',',':'))
        iomem=Path('/proc/iomem').read_text();(out/'iomem.txt').write_text(iomem)
        symbols={}
        if cfg.get('kernel_file_types',False):
            for line in Path('/proc/kallsyms').read_text().splitlines():
                fields=line.split()
                if len(fields)==3 and fields[2] in ('_text','init_top_pgt','vmemmap_base','page_offset_base','folio_mapping','pfn_to_online_page','generic_fillattr'):symbols[fields[2]]=int(fields[0],16)
        ranges=ram_ranges(iomem);snapshot(out,'inventory-before',ranges)
        value=dict(status='PASS',sandbox_id=cfg['sandbox_id'],page_size=PAGE,root=str(root),mode=cfg['mode'],
            catalog=listing,files=before,kernel_symbols=symbols,guest_elapsed_ns=time.monotonic_ns()-started,
            scope='visible ext4 regular files; initially resident pages only; hardlinks grouped by device/inode',
            exclusions=['symlinks not followed','non-ext4 mounts, deleted/unreachable files, directory metadata not attributed'])
        if retention:value['fixture_retention']=retention
        (out/'before.json').write_text(json.dumps(value,separators=(',',':'))+'\n');(out/'ready').touch()
        wait(out/'capture-after');after=collect(listing['files'],{r['path']:r for r in before})
        snapshot(out,'inventory-after',ranges)
        usage=resource.getrusage(resource.RUSAGE_SELF)
        value=dict(status='PASS',sandbox_id=cfg['sandbox_id'],files=after,guest_elapsed_ns=time.monotonic_ns()-started,
                   process_maxrss_kib=usage.ru_maxrss,cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime)
        (out/'after.json').write_text(json.dumps(value,separators=(',',':'))+'\n');(out/'after-ready').touch()
        wait(out/'release');held.close();(out/'done').touch()
    except BaseException:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());raise
    finally:held.close()


def fixture_content_check(directory,evict=False):
    """Only owned fixture hot pages; run after the physical measurement."""
    before=json.loads((directory/'before.json').read_text())
    root=Path(before['root'])
    assert before['mode']=='fixture' and str(root).startswith(('/workspace/asb-inventory-fixture','/rom/overlay/asb-kernel-fixture-'))
    checked=0;receipts=[]
    for item in before['files']:
        path=Path(item['path']);assert path.parent==root and not path.is_symlink()
        if not item['pages']:continue
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            assert file_identity(os.fstat(fd))==item['identity']
            if evict:
                os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
            else:
                for page in item['pages']:
                    assert page['status']=='PASS'
                    data=os.pread(fd,page['valid_bytes'],page['index']*PAGE)
                    assert hashlib.sha256(data).hexdigest()==page['sha256'],'fixture content changed'
                    receipts.append(dict(path=item['path'],index=page['index'],valid_bytes=page['valid_bytes'],sha256=hashlib.sha256(data).hexdigest(),identity=item['identity']))
                    checked+=1
            assert file_identity(os.fstat(fd))==item['identity']
        finally:os.close(fd)
    assert evict or checked==3
    return dict(status='PASS',checked_pages=checked,pages=receipts,operation='owned-fixture-eviction' if evict else 'post-measurement-content-check',
                scope='initial three hot fixture pages only; cold and sparse files not read')


if __name__=='__main__':
    if sys.argv[1] in ('--verify-fixture-content','--evict-fixture'):
        print(json.dumps(fixture_content_check(Path(sys.argv[2]),sys.argv[1]=='--evict-fixture')))
    else:main()
