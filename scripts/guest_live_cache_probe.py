"""Publish one owned file's cache PFNs while the guest continues to run.

The metadata page and the populated fixture range are mlocked for this known-size
calibration. This is not a whole-guest event collector or a performance workload.
"""
import ctypes
import json
import mmap
import os
from pathlib import Path
import resource
import struct
import sys
import time

from guest_named_file_probe import resident
from live_cache_metadata import HEADER, MAGIC, PAGE, PFN_MASK, SIZE


def main():
    sid, directory = sys.argv[1], Path(sys.argv[2])
    assert directory.parent == Path('/dev/shm') and directory.name.startswith('asb-live-')
    assert os.sysconf('SC_PAGE_SIZE') == PAGE
    directory.mkdir(exist_ok=False)
    fixture = Path('/workspace/asb-live-cache-fixture.bin')
    libc = ctypes.CDLL(None, use_errno=True)
    for name in ('mlock', 'munlock'):
        getattr(libc, name).argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if soft < SIZE + PAGE:
        resource.setrlimit(resource.RLIMIT_MEMLOCK, (SIZE + PAGE, max(hard, SIZE + PAGE)))
    fd = os.open(fixture, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    mapped = metadata = None
    locked = 0
    seq = tick = 0
    phase = 0
    try:
        os.posix_fallocate(fd, 0, SIZE)
        mapped = mmap.mmap(fd, SIZE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        mapped.madvise(mmap.MADV_RANDOM)
        address = ctypes.addressof(ctypes.c_char.from_buffer(mapped))
        metadata = mmap.mmap(-1, PAGE)
        metadata[:] = bytes(PAGE)
        meta_address = ctypes.addressof(ctypes.c_char.from_buffer(metadata))
        assert libc.mlock(meta_address, PAGE) == 0, 'cannot lock metadata page'
        with open('/proc/self/pagemap', 'rb', buffering=0) as pm:
            def page_entry(address):
                return struct.unpack('<Q', os.pread(pm.fileno(), 8, address // PAGE * 8))[0]

            marker_entry = page_entry(meta_address)
            assert marker_entry & (1 << 63) and marker_entry & PFN_MASK

            def publish(elapsed):
                nonlocal seq, tick
                seq += 2
                # Readers must skip the page while the source metadata is sampled.
                struct.pack_into('<Q', metadata, 8, seq - 1)
                bitmap = resident(mapped, address)
                pfns = []
                for i, hot in enumerate(bitmap):
                    if not hot:
                        pfns.append(0)
                        continue
                    unused = mapped[i * PAGE]
                    entry = page_entry(address + i * PAGE)
                    assert entry & (1 << 63) and entry & (1 << 61) and entry & PFN_MASK, 'fixture page is not mapped file cache'
                    pfns.append(entry & PFN_MASK)
                values = {}
                for line in Path('/proc/meminfo').read_text().splitlines():
                    fields = line.split()
                    if fields[0] in ('Cached:', 'MemTotal:', 'MemFree:'):
                        values[fields[0].rstrip(':')] = int(fields[1])
                tick += 1
                HEADER.pack_into(metadata, 0, MAGIC, seq - 1, tick, elapsed, phase, len(pfns),
                                 values['Cached'], values['MemTotal'] - values['MemFree'], time.time_ns())
                struct.pack_into('<' + 'I' * len(pfns), metadata, HEADER.size, *pfns)
                struct.pack_into('<Q', metadata, 8, seq)

            publish(0)
            bootstrap = dict(sandbox_id=sid, pfn=marker_entry & PFN_MASK, text=MAGIC.decode(),
                             iomem=Path('/proc/iomem').read_text(), kernel=os.uname().release,
                             boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                             pid=os.getpid(), fixture_bytes=SIZE, metadata_locked_bytes=PAGE,
                             file_population='mlock populated range; no guest LRU requirement')
            (directory / 'bootstrap.json').write_text(json.dumps(bootstrap) + '\n')
            deadline = time.monotonic() + 25
            while not (directory / 'start').exists():
                assert time.monotonic() < deadline, 'host start deadline'
                publish(0)
                time.sleep(.1)
            started = time.monotonic_ns()
            for phase in range(6):
                # Invalidate the old PFN list before populating or releasing pages.
                struct.pack_into('<Q', metadata, 8, seq + 1)
                if phase in (1, 2):
                    locked = SIZE // 2 if phase == 1 else SIZE
                    assert libc.mlock(address, locked) == 0, 'cannot populate/lock fixture range'
                elif phase == 3:
                    assert sum(mapped[i] for i in range(0, SIZE, PAGE)) == 0
                elif phase == 4:
                    assert libc.munlock(address, SIZE) == 0
                    locked = 0
                    mapped.close()
                    os.posix_fadvise(fd, 0, SIZE, os.POSIX_FADV_DONTNEED)
                    mapped = mmap.mmap(fd, SIZE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE)
                    mapped.madvise(mmap.MADV_RANDOM)
                    address = ctypes.addressof(ctypes.c_char.from_buffer(mapped))
                until = time.monotonic() + (2 if phase < 5 else 1)
                while time.monotonic() < until:
                    publish(time.monotonic_ns() - started)
                    time.sleep(.1)
            (directory / 'result.json').write_text(json.dumps(dict(status='PASS',publications=tick,
                vm_pause=False, elapsed_ns=time.monotonic_ns()-started))+'\n')
            # Keep only the metadata page available for the final host read.
            deadline = time.monotonic() + 10
            while not (directory / 'release').exists():
                assert time.monotonic() < deadline, 'host release deadline'
                publish(time.monotonic_ns()-started)
                time.sleep(.1)
    except BaseException:
        import traceback
        (directory / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        if mapped is not None:
            if locked:
                libc.munlock(address, locked)
            mapped.close()
        if metadata is not None:
            libc.munlock(meta_address, PAGE)
            metadata.close()
        os.close(fd)
        fixture.unlink(missing_ok=True)
    (directory / 'done').touch()


if __name__ == '__main__':
    main()
