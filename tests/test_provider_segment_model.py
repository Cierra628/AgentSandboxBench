"""Synthetic prefix writes and saved metadata only; never opens live devices."""
import copy
import itertools
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from provider_segment_model import ENTRY,HEADER,SegmentModel,UNIT,UNKNOWN,layout,stable_sources


def read(table,offset,length,result=None):
    table.begin_read(offset,length)
    table.complete_read(length if result is None else result)


class SegmentTests(unittest.TestCase):
    def table(self):return SegmentModel(8*UNIT)

    def sources(self,table):
        saved=table.snapshot()
        return stable_sources(saved,saved,saved,saved)

    def test_initial_unknown_and_zero_length_do_not_create_ownership(self):
        table=self.table();self.assertEqual(self.sources(table),[None]*8)
        read(table,0,0);self.assertEqual(self.sources(table),[None]*8)

    def test_multiple_shorter_reads_preserve_distinct_tail_offsets(self):
        table=self.table();read(table,0,8*UNIT)
        read(table,20*UNIT,4*UNIT);read(table,40*UNIT,2*UNIT)
        self.assertEqual(self.sources(table),[i*UNIT for i in (40,41,22,23,4,5,6,7)])

    def test_invalidation_precedes_any_payload_and_success_publication(self):
        table=self.table();read(table,0,8*UNIT)
        table.begin_read(20*UNIT,4*UNIT)
        self.assertEqual(self.sources(table),[None]*4+[i*UNIT for i in range(4,8)])
        table.complete_read(4*UNIT)
        self.assertEqual(self.sources(table),[i*UNIT for i in (20,21,22,23,4,5,6,7)])

    def test_partial_failed_oversized_result_and_cancel_leave_prefix_unknown(self):
        for result in (-5,-125,0,UNIT,5*UNIT):
            table=self.table();read(table,0,8*UNIT)
            read(table,20*UNIT,4*UNIT,result)
            self.assertEqual(self.sources(table),[None]*4+[i*UNIT for i in range(4,8)])

    def test_repeated_get_data_invalidates_before_copy_and_never_restores(self):
        table=self.table();read(table,0,8*UNIT)
        for _ in range(2):
            previous=table.snapshot();table.before_get_data(3*UNIT)
            self.assertEqual(self.sources(table),[None]*3+[i*UNIT for i in range(3,8)])
            self.assertTrue(all(b[0]>a[0] for a,b in zip(previous['entries'][:3],table.entries[:3])))
        read(table,20*UNIT,UNIT)
        self.assertEqual(self.sources(table),[20*UNIT,None,None]+[i*UNIT for i in range(3,8)])

    def test_partial_get_data_uses_original_not_shortened_request_length(self):
        table=self.table();read(table,0,8*UNIT)
        # The kernel can shorten nr_sectors AFTER copying only part of the
        # request. Invalidate the original four sectors before permitting IO,
        # even if the returned descriptor later reports just one sector.
        table.before_get_data(4*UNIT)
        shortened_length=UNIT
        self.assertEqual(self.sources(table),[None]*4+[i*UNIT for i in range(4,8)])
        read(table,20*UNIT,shortened_length)
        self.assertEqual(self.sources(table),[20*UNIT]+[None]*3+[i*UNIT for i in range(4,8)])

    def test_get_data_fetch_retains_and_legacy_fetch_invalidates_all(self):
        table=self.table();read(table,0,8*UNIT);saved=table.snapshot()
        table.before_fetch(True);self.assertEqual(table.snapshot(),saved)
        table.before_fetch(False);self.assertEqual(self.sources(table),[None]*8)

    def test_same_offset_refill_is_detected_but_untouched_tail_is_admitted(self):
        table=self.table();read(table,0,8*UNIT);before=table.snapshot()
        read(table,0,2*UNIT);after=table.snapshot()
        self.assertEqual(stable_sources(before,before,after,after),[None]*2+[i*UNIT for i in range(2,8)])

    def test_four_copies_and_odd_generation_prevent_torn_or_changed_entries(self):
        table=self.table();read(table,0,8*UNIT);base=table.snapshot()
        for index in range(4):
            copies=[copy.deepcopy(base) for _ in range(4)]
            copies[index]['entries'][0][0]+=2
            self.assertEqual(stable_sources(*copies)[0],None)
        odd=copy.deepcopy(base);odd['entries'][0][0]+=1
        self.assertEqual(stable_sources(odd,odd,odd,odd)[0],None)
        changed=copy.deepcopy(base);changed['entries'][0][1]=100*UNIT
        self.assertEqual(stable_sources(base,changed,changed,changed)[0],None)

    def test_release_invalidates_before_unmap_even_during_cancel(self):
        table=self.table();read(table,0,8*UNIT);before=table.snapshot()
        table.begin_read(20*UNIT,UNIT);table.release()
        self.assertTrue(all(e[1]==UNKNOWN for e in table.entries))
        self.assertEqual(self.sources(table),[None]*8)
        self.assertEqual(stable_sources(before,before,before,table.snapshot()),[None]*8)

    def test_two_hidden_histories_are_distinguished_by_segment_records(self):
        short=self.table();long=self.table()
        for table in (short,long):read(table,0,8*UNIT)
        read(short,20*UNIT,UNIT);read(long,20*UNIT,8*UNIT)
        for table in (short,long):read(table,40*UNIT,UNIT)
        self.assertEqual(self.sources(short)[1:],[i*UNIT for i in range(1,8)])
        self.assertEqual(self.sources(long)[1:],[i*UNIT for i in range(21,28)])

    def test_layout_serialization_and_legacy_header_are_compatible(self):
        budget=layout(256*1024,16)
        self.assertEqual((budget['entry_count'],budget['used_bytes_per_slot'],budget['mapped_bytes_per_slot']),
            (512,8256,12288))
        self.assertEqual((budget['all_slots_mapped_bytes'],budget['added_mapped_bytes'],
            budget['added_metadata_read_bytes_per_point']),(192*1024,128*1024,512*1024))
        table=self.table();read(table,20*UNIT,4*UNIT)
        header=struct.pack('<8Q',*range(8));data=header+table.encode_entries()
        self.assertEqual(data[:HEADER],header)
        self.assertEqual(list(ENTRY.iter_unpack(data[HEADER:])),list(map(tuple,table.entries)))

    def test_sector_boundaries_match_actual_512_byte_request_contract(self):
        table=self.table()
        for offset,length in ((1,UNIT),(0,1),(0,table.capacity+UNIT)):
            with self.assertRaises(AssertionError):table.begin_read(offset,length)
        read(table,0,UNIT)
        self.assertEqual(self.sources(table),[0]+[None]*7)

    def test_all_small_event_histories_match_independent_sector_payload(self):
        # Concrete source identities are kept outside the model, with UNKNOWN
        # for bytes that a failed read or WRITE may have altered arbitrarily.
        operations=[(kind,n) for kind in ('read_a','read_b','failed','write') for n in (1,2,8)]
        operations+= [('cancel',2),('release',8)]
        for length in range(4):
            for history in itertools.product(operations,repeat=length):
                table=self.table();payload=[None]*8
                for kind,n in history:
                    if kind.startswith('read_'):
                        offset=(20 if kind=='read_a' else 40)*UNIT
                        table.begin_read(offset,n*UNIT)
                        self.assertEqual(self.sources(table),[None]*n+payload[n:])
                        payload[:n]=[offset+i*UNIT for i in range(n)]
                        table.complete_read(n*UNIT)
                    elif kind in ('failed','cancel'):
                        table.begin_read(60*UNIT,n*UNIT);payload[:n]=[None]*n
                        table.complete_read(-5 if kind=='failed' else -125)
                    elif kind=='write':
                        table.before_get_data(n*UNIT);payload[:n]=[None]*n
                    else:
                        table.release();payload=[None]*8
                    self.assertEqual(self.sources(table),payload)
                    if kind=='release':break

    def test_ordered_atomic_publications_against_four_interleaved_reader_copies(self):
        # One entry: invalidate (3 stores), payload overwrite, publish (3).
        # Reader: four (generation,offset) copies with two pagemap markers.
        # Enumerate all 17-choose-7 orderings, preserving each side's order.
        for writer_positions in itertools.combinations(range(17),7):
            writer_positions=set(writer_positions);entry=[4,0];payload=0
            w=r=0;copies=[[] for _ in range(4)];interval=[];started=False
            for step in range(17):
                if step in writer_positions:
                    if w in (0,2,4,6):entry[0]+=1
                    elif w==1:entry[1]=UNKNOWN
                    elif w==3:payload=20*UNIT
                    else:entry[1]=20*UNIT
                    w+=1
                else:
                    if r==2:started=True
                    elif r==7:interval.append(payload);started=False
                    else:
                        copy_index,word={0:(0,0),1:(0,1),3:(1,0),4:(1,1),
                            5:(2,0),6:(2,1),8:(3,0),9:(3,1)}[r]
                        copies[copy_index].append(entry[word])
                    r+=1
                if started:interval.append(payload)
            frames=[dict(released=False,entries=[record]) for record in copies]
            source=stable_sources(*frames)[0]
            if source is not None:self.assertEqual(set(interval),{source})


if __name__=='__main__':unittest.main()
