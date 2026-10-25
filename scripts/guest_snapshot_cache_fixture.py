"""One 16 MiB calibration file, with explicit reads/writes and no file locking."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import time

PAGE=4096
SIZE=16*2**20

def main():
    out=Path(sys.argv[1]);out.mkdir()
    path=Path('/workspace/asb-snapshot-cache.bin')
    original=b'A'*SIZE
    with path.open('xb') as f:f.write(original);f.flush();os.fsync(f.fileno())
    with path.open('r+b') as f, mmap.mmap(f.fileno(),SIZE,access=mmap.ACCESS_WRITE) as mapping:
        assert hashlib.sha256(mapping[:]).digest()==hashlib.sha256(original).digest()
        address=ctypes.addressof(ctypes.c_char.from_buffer(mapping))
        def state(phase):
            with open('/proc/self/pagemap','rb') as pm:
                raw=os.pread(pm.fileno(),SIZE//PAGE*8,address//PAGE*8)
            entries=struct.unpack('<'+str(SIZE//PAGE)+'Q',raw)
            value=dict(phase=phase,pid=os.getpid(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                start_ticks=Path('/proc/self/stat').read_text().split()[21],pfns=[e&((1<<55)-1) if e&(1<<63) else 0 for e in entries],
                timestamp_ns=time.time_ns(),size_bytes=SIZE,locked_file_bytes=0)
            (out/(phase+'.json')).write_text(json.dumps(value)+'\n')
        def wait(name):
            deadline=time.monotonic()+180
            while not (out/name).exists():
                assert time.monotonic()<deadline,name+' deadline'
                time.sleep(.02)
        state('ready')
        wait('observe');state('waiting')
        wait('read');assert mapping[:4*2**20]==b'A'*(4*2**20);state('read-done')
        wait('write')
        branch=(out/'write-byte').read_bytes() if (out/'write-byte').exists() else b'B'
        assert branch in (b'B',b'C')
        mapping[4*2**20:8*2**20]=branch*(4*2**20);state('write-done')
        wait('finish')
        expected=b'A'*(4*2**20)+branch*(4*2**20)+b'A'*(8*2**20)
        assert mapping[:]==expected
        (out/'result.json').write_text(json.dumps(dict(status='PASS',sha256=hashlib.sha256(mapping[:]).hexdigest()))+'\n')
    (out/'done').touch()

if __name__=='__main__':main()
