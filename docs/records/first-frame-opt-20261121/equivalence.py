"""Reconstruct independent pread replies and compare both production implementations."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
import struct
import subprocess
import sys
from types import FunctionType, SimpleNamespace
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import provider_slot_cache as new
from profile_build import install
from fast_cache_metadata import decode_array


def rebuild(mod, frame):
    reads=[];slots=[]
    for i,r in enumerate(frame['rows']):
        def metadata(side):
            return mod.ROW.pack(*r[side])+b''.join(mod.SEGMENT.pack(*v) for v in r['segments'][side])
        fd=lambda n: SimpleNamespace(fileno=lambda:n)
        reads.extend([(i,0,metadata('before')),
                      (-1,r['first_address']//mod.PAGE*8,struct.pack('<%dQ'%len(r['pagemap']),*r['pagemap'])),
                      (i,0,metadata('after'))])
        slots.append((Path(r['name']),fd(i),dict(count=len(r['segments']['before']))))
    stream=iter(reads)
    def pread(fd,n,offset):
        i,pos,buf=next(stream)
        assert (i,pos,len(buf))==(fd,offset,n)
        return buf
    proc=Path('/proc/self')
    obj=SimpleNamespace(proc=proc,start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19],slots=slots,
                        pm=fd(-1),_check_owner=lambda:None)
    orig=mod.ProviderCache.snapshot
    if mod is new:
        with tempfile.TemporaryDirectory() as tmp:
            obj.directory=Path(tmp)
            result=install(pread=pread)(obj)
    else:
        result=FunctionType(orig.__code__,dict(orig.__globals__,os=SimpleNamespace(pread=pread)))(obj)
    assert next(stream,None) is None and result['metadata_bytes_read']==frame['metadata_bytes_read']
    assert json.loads(json.dumps(result['rows']))==frame['rows']
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('nov21',type=Path);p.add_argument('nov18',type=Path)
    a=p.parse_args()
    old=type(new)('original')
    exec(subprocess.check_output(['git','show','483f0e7:scripts/provider_slot_cache.py'],cwd=ROOT),old.__dict__)
    inputs=sorted(a.nov21.glob('guest-cache/try-*/process-*/frames.json'))
    legacy=sorted(a.nov18.glob('guest-cache/live-cache/*/pair-*-provider.json'))
    assert len(inputs)==35 and len(legacy)==5
    with (HERE/'equivalence.csv').open('w') as f:
        w=csv.writer(f,lineterminator='\n');w.writerow(['frame_pair','slots','segments','usable','four_pages','provider_pages','different_decisions','different_four_pfns'])
        for path in inputs+legacy:
            raw=json.loads(path.read_bytes());d=path.parent
            if isinstance(raw,dict):
                pair=[raw[k] for k in ('first','last')]
                read=lambda name:(d/(path.name[:-len('provider.json')]+name+'.bin')).read_bytes()
                b,z=read('before'),read('after')
                tables=[np.frombuffer(read(k),dtype='<u8').reshape(-1,4) for k in ('host','backend','lower')]
            else:
                pair=raw;b,z=[(d/(k+'.bin')).read_bytes() for k in ('before','after')]
                with np.load(d/'evidence.npz') as data:tables=[data[k] for k in ('host','direct','lower')]
            outputs=[]
            for mod in [old,new]:
                first,last=[rebuild(mod,fr) for fr in pair]
                decisions=[mod.segment_sources(x,y,x['before'][3]) for x,y in zip(first['rows'],last['rows'],strict=True)]
                provider=set()
                info,pfns=mod.account_observed_provider(b,z,*tables,first,last,decoded=(decode_array(b),decode_array(z)),current_pfns=provider)
                outputs.append((decisions,info,pfns,provider))
            assert outputs[0]==outputs[1],path
            if path in inputs:
                with np.load(d/'sets.npz') as sets:
                    assert pfns==set(sets['four']) and provider==set(sets['provider'])
            w.writerow([path.relative_to(a.nov21 if path in inputs else a.nov18),len(decisions),sum(map(len,decisions)),
                        sum(v is not None for slot in decisions for v in slot),len(pfns),len(provider),0,0])

if __name__=='__main__':main()
