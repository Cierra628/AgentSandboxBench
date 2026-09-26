"""Known file mappings in one guest process; hold each state for host inspection."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import sys
import time


def main():
    sid, mib = sys.argv[1], int(sys.argv[2])
    assert mib in (1, 16) and os.sysconf('SC_PAGE_SIZE') == 4096
    root = Path('/workspace/asb-pfn-fixture')
    root.mkdir(parents=True)
    n = mib * 256
    data = b''.join(struct.pack('<Q', i + 1) * 512 for i in range(n))
    paths = [root / name for name in ('shared.bin', 'private-a.bin', 'private-b.bin')]
    for path in paths:
        path.write_bytes(data)
    original_hash = hashlib.sha256(data).hexdigest()
    altered = bytearray(data)
    for i in range(0, len(altered), 8192):
        altered[i] ^= 1
    cow_hash = hashlib.sha256(altered).hexdigest()
    maps = []
    try:
        for case in ('shared', 'cow', 'private'):
            if case != 'cow':
                for mapped in maps:
                    mapped.close()
                maps = []
                for path in ([paths[0], paths[0]] if case == 'shared' else paths[1:]):
                    with path.open('rb') as f:
                        maps.append(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY))
                for mapped in maps:
                    sum(mapped[i] for i in range(0, len(mapped), 4096))
            else:
                for i in range(0, len(maps[1]), 8192):
                    maps[1][i] ^= 1
            directory = root / case
            directory.mkdir()
            mappings = []
            with open('/proc/self/pagemap', 'rb', buffering=0) as pm, open('/proc/kpageflags', 'rb', buffering=0) as kf:
                for index, mapped in enumerate(maps):
                    addr = ctypes.addressof(ctypes.c_char.from_buffer(mapped))
                    pages = []
                    for page in range(n):
                        entry = struct.unpack('Q', os.pread(pm.fileno(), 8, (addr // 4096 + page) * 8))[0]
                        pfn = entry & ((1 << 55) - 1)
                        assert entry & (1 << 63) and pfn
                        flags = struct.unpack('Q', os.pread(kf.fileno(), 8, pfn * 8))[0]
                        pages.append(dict(pfn=pfn, flags=flags, pagemap_entry=entry))
                    digest = hashlib.sha256(mapped).hexdigest()
                    assert digest == (cow_hash if case == 'cow' and index == 1 else original_hash)
                    mappings.append(dict(address=addr, sha256=digest, pages=pages))
            assert all(hashlib.sha256(path.read_bytes()).hexdigest() == original_hash for path in paths)
            (directory / 'fixture.json').write_text(json.dumps(dict(case=case, pages_per_mapping=n,
                original_sha256=original_hash, cow_sha256=cow_hash, mappings=mappings)))
            # Reuse the actual senior guest enumerator, keeping its marker alive.
            with (directory / 'probe.log').open('wb') as log:
                probe = subprocess.Popen(['python3', str(Path(__file__).with_name('physical-cache-probe.py')), sid], stdout=log, stderr=log)
            try:
                guest_probe = Path('/dev/shm') / f'mixfs-exp5-{sid}'
                deadline = time.monotonic() + 15
                while not (guest_probe / 'marker.json').exists():
                    if probe.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError((directory / 'probe.log').read_text())
                    time.sleep(.05)
                for name in ('marker.json', 'file-pfns.bin'):
                    (directory / name).write_bytes((guest_probe / name).read_bytes())
                (directory / 'ready').touch()
                deadline = time.monotonic() + 45
                while not (directory / 'continue').exists():
                    assert probe.poll() is None and time.monotonic() < deadline
                    time.sleep(.05)
            finally:
                probe.terminate()
                probe.wait(timeout=5)
                (guest_probe / 'marker.json').unlink(missing_ok=True)
        (root / 'done').touch()
    finally:
        for mapped in maps:
            mapped.close()


if __name__ == '__main__':
    main()
