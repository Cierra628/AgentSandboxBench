"""Offline reference model only: no live buffers, shared mappings, or services."""
import struct

UNIT=512
PAGE=4096
HEADER=64
UNKNOWN=(1<<64)-1
ENTRY=struct.Struct('<2Q')  # generation, source image byte offset


def layout(capacity,slots):
    assert capacity>0 and capacity%UNIT==0 and slots>0
    count=capacity//UNIT
    used=HEADER+count*ENTRY.size
    mapped=(used+PAGE-1)//PAGE*PAGE
    return dict(unit_bytes=UNIT,entry_bytes=ENTRY.size,entry_count=count,
        entry_offset=HEADER,used_bytes_per_slot=used,mapped_bytes_per_slot=mapped,
        all_slots_mapped_bytes=slots*mapped,old_all_slots_mapped_bytes=slots*PAGE,
        added_mapped_bytes=slots*(mapped-PAGE),
        added_metadata_read_bytes_per_point=4*slots*count*ENTRY.size)


class SegmentModel:
    """Single slot, single writer; request lengths/offsets are UNIT-aligned.

    `_publish` models three ordered atomic operations, not Python concurrency.
    An integration must finish every invalidation before permitting payload IO.
    Generations are monotonic, assuming no u64 wrap within one slot lifetime.
    """
    def __init__(self,capacity):
        assert capacity>0 and capacity%UNIT==0
        self.capacity=capacity
        self.entries=[[0,UNKNOWN] for _ in range(capacity//UNIT)]
        self.pending=None
        self.released=False

    def _publish(self,index,offset):
        entry=self.entries[index]
        entry[0]+=1  # odd: cannot be accepted by a reader
        entry[1]=offset
        entry[0]+=1  # even: this entry is fully published

    def invalidate(self,length):
        assert not self.released and 0<=length<=self.capacity and length%UNIT==0
        for index in range(length//UNIT):self._publish(index,UNKNOWN)

    def begin_read(self,offset,length):
        assert self.pending is None and offset>=0 and offset%UNIT==0
        self.invalidate(length)
        self.pending=(offset,length)

    def complete_read(self,result):
        assert self.pending is not None and not self.released
        offset,length=self.pending;self.pending=None
        if length>0 and result==length:
            for index in range(length//UNIT):self._publish(index,offset+index*UNIT)
        # Failure, partial result, and cancellation leave the prefix unknown.

    def before_get_data(self,length):
        assert self.pending is None
        self.invalidate(length)

    def before_fetch(self,need_get_data):
        assert self.pending is None
        if not need_get_data:self.invalidate(self.capacity)

    def release(self):
        self.invalidate(self.capacity)
        self.pending=None
        self.released=True

    def snapshot(self):
        return dict(released=self.released,entries=[entry.copy() for entry in self.entries])

    def encode_entries(self):
        return b''.join(ENTRY.pack(*entry) for entry in self.entries)


def stable_sources(*snapshots):
    """Four reads: before/after pagemap at each of two paired observations.

    Process/allocation identity and pagemap/guest checks belong to the caller.
    Unrelated prefix updates need not invalidate an untouched tail entry.
    """
    assert len(snapshots)==4
    count=len(snapshots[0]['entries'])
    assert all(len(s['entries'])==count for s in snapshots)
    if any(s['released'] for s in snapshots):return [None]*count
    result=[]
    for entries in zip(*(s['entries'] for s in snapshots),strict=True):
        generation,offset=entries[0]
        result.append(offset if generation%2==0 and offset!=UNKNOWN
            and all(entry==entries[0] for entry in entries) else None)
    return result
