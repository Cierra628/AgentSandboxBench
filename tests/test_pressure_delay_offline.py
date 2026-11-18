"""Integer timing arithmetic and synthetic counter intervals; no live calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_pressure_delay import critical_parts,difference,refault_intervals


class PressureDelayTests(unittest.TestCase):
    def test_critical_parts_recover_window_including_offset_and_gaps(self):
        calls=[];start=5
        for i in range(10):
            duration=10 if i%2==0 else 2
            calls.append(dict(stage=f'controller-call-{i:04}',start_monotonic_ns=start,duration_ns=duration))
            start+=duration+3
        value=critical_parts(calls,25,0)
        self.assertEqual(value,dict(start_offset_ns=5,tools_ns=25,execution_residual_ns=25,download_ns=10,call_gaps_ns=27))
        self.assertEqual(sum(value.values()),92)
        calls[1]['start_monotonic_ns']=1
        with self.assertRaises(AssertionError):critical_parts(calls,25,0)

    def test_overlapping_segments_do_not_duplicate_counter_increment(self):
        host=[dict(monotonic_ns=t,workingset_refault_file=v,current=1) for t,v in ((0,10),(10,13),(20,17))]
        segments=[dict(segment='A',start_monotonic_ns=0,end_monotonic_ns=8),dict(segment='B',start_monotonic_ns=5,end_monotonic_ns=15)]
        result=refault_intervals(host,0,20,segments)
        self.assertEqual([r['active_segments'] for r in result],['A+B','B'])
        self.assertEqual(sum(r['refault_delta'] for r in result),7)

    def test_negative_differences_are_preserved(self):
        self.assertEqual(difference(dict(A=10,B=11,C=10,D=9)),-2)
        host=[dict(monotonic_ns=t,workingset_refault_file=v,current=1) for t,v in ((0,10),(10,7))]
        self.assertEqual(refault_intervals(host,0,10,[])[0]['refault_delta'],-3)


if __name__=='__main__':unittest.main()
