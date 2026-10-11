"""Bounded owned 16 MiB backing-cache calibration; no cache drop or VM."""
import hashlib
import json
import os
from pathlib import Path
import time

from trenvx_whole_cache import PAGE,PFN_MASK,records,scan_backing


def main():
    os.umask(0o077)
    root=Path(__file__).resolve().parents[1]
    out=root/'.artifacts/whole-cache-calibration'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True);data=out/'fixture.bin';alias=out/'alias.bin'
    value=b''.join(hashlib.sha256(str(i).encode()).digest() for i in range(PAGE//32))
    with data.open('xb') as stream:
        for _ in range(4096):stream.write(value)
        stream.flush();os.fsync(stream.fileno())
    os.link(data,alias)
    result=dict(status='FAIL',scope='own freshly written 16 MiB file, two hard-link mappings; no VM or global cache controls')
    try:
        scans=[scan_backing(path,out,index) for index,path in enumerate((data,alias))]
        pfnsets=[]
        for index in range(2):
            rows=records(out/f'backing-{index}-pages.bin')
            assert len(rows)==4096 and all(a==b for _,a,b in rows)
            pfnsets.append({a&PFN_MASK for _,a,b in rows})
        assert pfnsets[0]==pfnsets[1] and len(pfnsets[0])==4096
        result.update(status='PASS',expected_unique_bytes=16*2**20,
            observed_unique_bytes=len(pfnsets[0]|pfnsets[1])*PAGE,
            duplicate_pages_removed=4096,scans=scans)
    finally:
        if result['status']=='PASS':
            data.unlink();alias.unlink();result['owned_fixture_cleanup']='PASS'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
