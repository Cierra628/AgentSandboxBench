import json,struct,subprocess,glob
from pathlib import Path
from bisect import bisect_right
# Offline only: RUN_DIR [archived memory layer]; without a layer read the ext4 file via debugfs.
import sys
p=Path(sys.argv[1]);t=p/'guest-cache/try-1'
assert json.loads((p/'result.json').read_text())['diagnostic_guest_kernel']['sha256']=='5bbe0a10aa13f41694cec1ca3b38b2f926108132219bd42990888836af34881d'
assert '01000000-01c00931 : Kernel code' in (t/'live-cache/restored-1-pages/iomem.txt').read_text()
m=t/'shared-device/lower-metadata';name=json.loads((m/'ownership.json').read_bytes())['layer']['file'].split('/')[-1];buf=Path(sys.argv[2]).read_bytes() if len(sys.argv)==3 else subprocess.check_output(['debugfs','-R','cat /env/snapshot-store/repository/managed-layers/'+name,str(p/'volume.ext4')],stderr=subprocess.DEVNULL)
if len(sys.argv)==3:assert Path(sys.argv[2]).name==name
assert len(buf)==json.loads((m/'metadata.json').read_text())['size_bytes']
assert buf[:4096]==(m/'header.bin').read_bytes() and buf[-4096:]==(m/'trailer.bin').read_bytes()
idx=json.loads((m/'metadata.json').read_text())['index_offset']
assert buf[idx:idx+(m/'index.bin').stat().st_size]==(m/'index.bin').read_bytes()
s=[]
for a,b in struct.iter_unpack('<QQ',(m/'index.bin').read_bytes()):
 if a>>50:s.append((a%2**50,a%2**50+(a>>50),b%2**55,bool(b&2**55)))
def read(o,n):
 v=bytearray(n)
 for a,b,k,zero in s:
  l=max(o,a*512);h=min(o+n,b*512)
  if h>l and not zero:v[l-o:h-o]=buf[k*512+l-a*512:k*512+h-a*512]
 return v
counts=struct.unpack('<7Q',read(0x2680c20,56));print('moves overflow missing duplicates shmem pages gen',counts)
bits=read(0x3680c60,2**21//8);print('bitmap',int.from_bytes(bits,'little').bit_count())
tags=read(0x2680c60,2**21*8)
stored={i:v for i,(v,) in enumerate(struct.iter_unpack('<Q',tags)) if v}
print('tags',len(stored),sum(t&1 for t in stored.values()),max(stored.values())>>1)
assert sum(t&1 for t in stored.values())==counts[4] and max(stored.values())>>1<=counts[6]
assert len(stored)==counts[-2]==int.from_bytes(bits,'little').bit_count() and all(bits[i//8]&(1<<(i%8)) for i in stored)
for f in sorted(t.glob('process-*/before.bin')):
 raw=f.read_bytes();current=dict(struct.iter_unpack('<QQ',raw[104:]));print(f.parent.name,len(current.items()&stored.items()), len(current))
