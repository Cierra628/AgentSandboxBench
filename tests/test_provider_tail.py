"""Pure synthetic records and prefix-write histories, without live operations."""
import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_provider_tail import MAGIC,compare


def frame(generation=10,reads=2,length=8,offset=100):
    row=[MAGIC,generation,4096,16,offset,length,3,reads]
    return dict(before=row,after=row.copy())


class TailTests(unittest.TestCase):
    def test_shorter_single_read_keeps_old_tail_not_new_offset(self):
        result=compare(frame(),frame(14,3,4,200))
        self.assertEqual(result['preserved_slot_range'],[4,8])
        self.assertEqual(result['source_image_range'],[104,108])

    def test_unchanged_only_proves_known_prefix(self):
        result=compare(frame(),frame())
        self.assertEqual(result['preserved_slot_range'],[0,8])
        self.assertNotIn('tail_bytes',result)

    def test_equal_or_longer_read_has_no_old_tail(self):
        for length in (8,16):
            self.assertNotIn('tail_bytes',compare(frame(),frame(14,3,length)))

    def test_two_reads_have_same_counters_but_different_tails(self):
        def payload(lengths):
            data=list(range(16))
            for n,length in enumerate(lengths):data[:length]=[100+n]*length
            return data
        self.assertNotEqual(payload([2,2])[2:8],payload([16,2])[2:8])
        self.assertEqual(compare(frame(),frame(18,4,2))['kind'],'multiple_reads')

    def test_extra_mutations_including_failed_fill_or_get_data_reject(self):
        for generation in (16,18,20):
            self.assertEqual(compare(frame(),frame(generation,3,4))['kind'],'other_mutations')

    def test_invalid_endpoints_and_changed_allocation_reject(self):
        for index,value in ((0,0),(1,11),(5,0),(5,17),(6,2),(6,4),(6,5),(6,6)):
            end=frame();end['before'][index]=end['after'][index]=value
            self.assertEqual(compare(frame(),end)['kind'],'unstable_or_invalid')
        end=frame();end['after'][1]+=2
        self.assertEqual(compare(frame(),end)['kind'],'unstable_or_invalid')
        for index in (2,3):
            end=frame();end['before'][index]+=16;end['after'][index]+=16
            self.assertEqual(compare(frame(),end)['kind'],'allocation_changed')

    def test_backwards_and_inconsistent_counters_reject(self):
        self.assertEqual(compare(frame(),frame(6,1))['kind'],'counter_backwards')
        self.assertEqual(compare(frame(),frame(offset=200))['kind'],'inconsistent_record')
        self.assertEqual(compare(frame(),frame(12,2))['kind'],'other_mutations')

    def test_small_histories_independently_confirm_all_returned_ranges(self):
        # Enumerate actual prefix writes instead of using the inference formula
        # to construct expected byte identities. Failed writes can alter bytes.
        operations=[('read',n) for n in (1,4,8,16)]+[('failed',16),('get_data',16)]
        for count in range(4):
            for history in itertools.product(operations,repeat=count):
                if history and history[-1][0]!='read':continue
                data=list(range(16));generation=10;reads=2;length=8;offset=100
                for index,(op,n) in enumerate(history):
                    data[:n]=[1000+index]*n
                    generation+=2 if op=='get_data' else 4
                    if op=='read':reads+=1;length=n;offset=200+index*100
                result=compare(frame(),frame(generation,reads,length,offset))
                if 'preserved_slot_range' in result:
                    start,end=result['preserved_slot_range']
                    self.assertEqual(data[start:end],list(range(start,end)))


if __name__=='__main__':unittest.main()
