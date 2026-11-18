"""Synthetic saved rows only; no process, service or device operations."""
import copy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_light_rollout_alignment import extract_rounds,nearest_rounds

class AlignmentTests(unittest.TestCase):
    def row(self):
        return dict(round=0,parent='/fake',start_monotonic_ns=100,end_monotonic_ns=200,
            parent_reads=[dict(path='/fake/memory.'+k,status='READ',start_monotonic_ns=a,end_monotonic_ns=b) for k,a,b in [('stat',110,120),('current',125,130)]],
            parent_counters=dict(current=8192,file=4096,anon=2048,kernel=1024,shmem=0),
            vmms={},transition_roles={'a':dict(state='PENDING')},expected_roles=['a'],denominator=0,pss_sum_kib=0,all_roles_pss_sum_kib=None)
    def test_partial_total_remains_missing_and_times_unchanged(self):
        r=self.row();original=copy.deepcopy(r);origin,m,rounds=extract_rounds([r]);self.assertEqual(r,original)
        self.assertIsNone(rounds[0]['full_pss_mib']);self.assertEqual(rounds[0]['active_count'],0)
        self.assertEqual(next(x for x in m if x['metric']=='current')['start_ns'],125)
        self.assertEqual(next(x for x in m if x['metric']=='file')['start_ns'],110)
    def test_reject_fabricated_zero_for_missing_total(self):
        r=self.row();r['all_roles_pss_sum_kib']=0
        with self.assertRaisesRegex(ValueError,'incomplete'):extract_rounds([r])
    def test_reject_missing_and_outside_read_window(self):
        for fault in ('missing','outside'):
            r=self.row()
            if fault=='missing':r['parent_reads'].pop()
            else:r['parent_reads'][0]['end_monotonic_ns']=201
            with self.assertRaises(ValueError):extract_rounds([r])
    def test_brackets_do_not_claim_event_measurement(self):
        event=dict(kind='save',role='a',start_ns=150,end_ns=250)
        result=nearest_rounds([event],[dict(round=0,start_ns=100,end_ns=200),dict(round=1,start_ns=300,end_ns=350)])
        self.assertIsNone(result[0]['preceding_round']);self.assertEqual(result[0]['overlapping_rounds'],'0')
        self.assertEqual(result[1]['preceding_round'],0);self.assertEqual(result[1]['following_round'],1)

if __name__=='__main__':unittest.main()
