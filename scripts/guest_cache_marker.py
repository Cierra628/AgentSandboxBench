"""Hold an attestation marker while the shared two-boundary cache probe runs."""
import ctypes
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import threading

import guest_whole_cache_probe

out=Path(sys.argv[1]);sid=sys.argv[2]
assert len(sys.argv) in (3,4)
heartbeat=len(sys.argv)==4
if heartbeat:assert sys.argv[3]=='--heartbeat'
marker=('asb-whole-cache:'+sid).encode()
with mmap.mmap(-1,4096) as page:
    page[:len(marker)]=marker
    address=ctypes.addressof(ctypes.c_char.from_buffer(page))
    with open('/proc/self/pagemap','rb',buffering=0) as stream:
        entry=struct.unpack('<Q',os.pread(stream.fileno(),8,address//4096*8))[0]
    assert entry&(1<<63) and entry&((1<<55)-1)
    stop=threading.Event();thread=None
    if heartbeat:
        def advance():
            counter=0
            while not stop.is_set():
                counter+=1;struct.pack_into('<Q',page,128,counter);stop.wait(.02)
        thread=threading.Thread(target=advance);thread.start()
    # Publish only after main created its private directory and first snapshot.
    original=guest_whole_cache_probe.wait
    def wait(path):
        if path.name=='capture-after':
            metadata=dict(sandbox_id=sid,pfn=entry&((1<<55)-1),text=marker.decode())
            if heartbeat:metadata['heartbeat_offset']=128
            (out/'marker.json').write_text(json.dumps(metadata)+'\n')
        original(path)
    guest_whole_cache_probe.wait=wait
    sys.argv=[sys.argv[0],str(out)]
    try:guest_whole_cache_probe.main()
    finally:
        stop.set()
        if thread:thread.join(timeout=2)
