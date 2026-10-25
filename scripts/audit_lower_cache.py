"""Recount saved raw-layer residency independently of online array accounting."""
import ast,json
from bisect import bisect_right
from pathlib import Path
import struct
import numpy as np
from kernel_cache_metadata import decode,PRESENT,SWAPPED,PHYSICAL_MASK,MASK
from snapshot_layer_metadata import RawMemoryLayer,UNKNOWN
from audit_snapshot_cache import scalar as direct_scalar
FILE=1<<61
ROW=struct.Struct('<4Q')

def captured_layer_offsets(source):
    """Compile only the saved pure mapping method, never its I/O paths."""
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='RawMemoryLayer')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='offsets')
    namespace=dict(bisect_right=bisect_right,np=np,UNKNOWN=UNKNOWN)
    exec(compile(ast.Module(body=[method],type_ignores=[]),'<captured-raw-offsets>','exec'),namespace)
    return namespace['offsets']

def saved_offsets(directory,layer,pages):
    source=directory/'code/snapshot_layer_metadata.py'
    if source.is_file():
        return captured_layer_offsets(source.read_text())(layer,pages)
    # Existing callers without a source capture retain the old single-segment contract.
    legacy=RawMemoryLayer.__new__(RawMemoryLayer)
    legacy.segments,legacy.starts=layer.segments,layer.starts
    return legacy.offsets(pages)

def check_point(directory,saved):
    prefix=directory/f"pair-{saved['point']:04}"
    read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
    before=read('-before.bin');after=read('-after.bin')
    _,left=decode(before);right,last=decode(after)
    host=list(ROW.iter_unpack(read('-host.bin')));direct=list(ROW.iter_unpack(read('-backend.bin')))
    lower=list(ROW.iter_unpack(read('-lower.bin')))
    raw=RawMemoryLayer.from_saved(directory/'lower-metadata')
    metadata=json.loads((directory/'lower-metadata/metadata.json').read_text())
    query=json.loads((directory/'lower-query/snapshot-backend.json').read_text())
    assert (query['identity']['device'],query['identity']['inode'],query['mapping_length']) == (
        metadata['device'],metadata['inode'],metadata['size_bytes']), 'lower query identity differs from indexed layer'
    offsets=saved_offsets(directory,raw,np.array([r[1] for r in direct],dtype='<u8'))
    assert list(map(int,offsets))==[r[1] for r in lower]
    original=direct_scalar(before,after,host,direct)
    assert all(saved[k]==v for k,v in original.items())
    vm=set();device=set();layer=set();old=set();confirmed=resolved=0
    for (p,a,b,c),(q,off,x,y),(r,loff,u,v) in zip(host,direct,lower,strict=True):
        assert p==q==r
        if left[p]!=last.get(p) or ((a^b)|(b^c))&PHYSICAL_MASK:continue
        dok=x==y and bool(x&PRESENT);lok=u==v and bool(u&PRESENT)
        if a&PRESENT and a&FILE and dok and a&MASK!=x&MASK:continue
        current=not a&(PRESENT|SWAPPED) or bool(a&PRESENT and a&FILE)
        if a&PRESENT:vm.add(a&MASK)
        if current and dok:device.add(x&MASK)
        if current and lok:layer.add(u&MASK)
        if a&PRESENT and not a&FILE and lok:old.add(u&MASK)
        if a&PRESENT or current and (dok or lok):confirmed+=1
        if not a&PRESENT and current and lok and not dok:resolved+=1
    result=dict(host_lower_current_bytes=len(layer)*4096,host_three_current_union_bytes=len(vm|device|layer)*4096,
        host_lower_intersection_with_vmm_device_bytes=len(layer&(vm|device))*4096,
        host_lower_old_snapshot_bytes=len(old)*4096,lower_resolved_guest_pages=resolved,
        host_three_unconfirmed_guest_capacity_bytes=(right['records']-confirmed)*4096)
    assert all(saved[k]==v for k,v in result.items()), 'three-level recount differs'
    return result,vm|device|layer
