"""Pure saved rows only; no service or proc access."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_light_memory_timing import extract,stats
class TimingTests(unittest.TestCase):
    def row(self):
        return dict(monotonic_ns=100,duration_ns=100,groups={'/fake':dict(current=8192,file=4096,anon=2048,kernel=2048,shmem=1024)},experiment_memory=dict(parent='/fake',file_closure=dict(status='MISMATCH')),cgroup_diagnostics=dict(counter_reads=[dict(path='/fake/memory.stat',status='READ',start_monotonic_ns=110,end_monotonic_ns=120),dict(path='/fake/memory.current',status='READ',start_monotonic_ns=130,end_monotonic_ns=140)]))
    def test_preserve_windows_and_diagnostic(self):
        x=extract(self.row(),'/fake',100)
        self.assertEqual(x['parent_read_start_ns'],110);self.assertEqual(x['parent_read_end_ns'],140)
        self.assertEqual(x['file_minus_shmem_mib'],3072/2**20);self.assertEqual(x['closure_diagnostic'],'MISMATCH')
    def test_missing_time_no_inferred_window(self):
        x=self.row();x['cgroup_diagnostics']['counter_reads'].pop()
        with self.assertRaises(ValueError):extract(x,'/fake',100)
    def test_wrong_parent_or_outside_time_fails(self):
        with self.assertRaises(ValueError):extract(self.row(),'/other',100)
        x=self.row();x['cgroup_diagnostics']['counter_reads'][0]['end_monotonic_ns']=201
        with self.assertRaises(ValueError):extract(x,'/fake',100)
    def test_percentiles(self):
        self.assertEqual(stats(list(range(1,101))),dict(n=100,median=50.5,p95=95,maximum=100))
if __name__=='__main__':unittest.main()
