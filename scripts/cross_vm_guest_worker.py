"""Persistent guest file mapping and command protocol for snapshot siblings."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import time

PAGE = 4096


def main():
    mib = int(sys.argv[1])
    assert mib in (1, 16) and os.sysconf('SC_PAGE_SIZE') == PAGE
    root = Path('/workspace/asb-cross-vm')
    root.mkdir(parents=True)
    count = mib * 256
    data = b''.join(struct.pack('<Q', i + 1) * (PAGE // 8) for i in range(count))
    backing = root / 'known-file.bin'
    backing.write_bytes(data)
    with backing.open('rb') as handle:
        mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_COPY)
    sum(mapped[i] for i in range(0, len(mapped), PAGE))
    address = ctypes.addressof(ctypes.c_char.from_buffer(mapped))
    original_sha = hashlib.sha256(data).hexdigest()
    altered = bytearray(data)
    for i in range(0, len(altered), 2 * PAGE):
        altered[i] ^= 1
    altered_sha = hashlib.sha256(altered).hexdigest()
    pid = os.getpid()
    start_ticks = int(Path(f'/proc/{pid}/stat').read_text().split()[21])
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    last_seq = -1

    def publish(seq, operation):
        pages = []
        with open('/proc/self/pagemap', 'rb', buffering=0) as pm, open('/proc/kpageflags', 'rb', buffering=0) as kf:
            for i in range(count):
                entry = struct.unpack('Q', os.pread(pm.fileno(), 8, (address // PAGE + i) * 8))[0]
                pfn = entry & ((1 << 55) - 1)
                assert entry & (1 << 63) and pfn
                flags = struct.unpack('Q', os.pread(kf.fileno(), 8, pfn * 8))[0]
                pages.append(dict(pfn=pfn, flags=flags, pagemap_entry=entry))
        digest = hashlib.sha256(mapped).hexdigest()
        assert digest == (altered_sha if operation == 'write-half' else original_sha)
        assert hashlib.sha256(backing.read_bytes()).hexdigest() == original_sha
        state = dict(seq=seq, operation=operation, pid=pid, start_ticks=start_ticks, boot_id=boot_id,
                     address=address, page_count=count, mapped_sha256=digest,
                     backing_sha256=original_sha, altered_sha256=altered_sha, pages=pages)
        tmp = root / 'state.tmp'
        tmp.write_text(json.dumps(state))
        tmp.replace(root / 'state.json')

    publish(0, 'inspect')
    last_seq = 0
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        path = root / 'command.json'
        if path.exists():
            command = json.loads(path.read_text())
            seq = command['seq']
            if seq > last_seq:
                operation = command['operation']
                assert operation in ('inspect', 'write-half')
                if operation == 'write-half':
                    for i in range(0, len(mapped), 2 * PAGE):
                        mapped[i] ^= 1
                publish(seq, operation)
                last_seq = seq
        time.sleep(.05)


if __name__ == '__main__':
    main()
