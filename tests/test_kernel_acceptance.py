from pathlib import Path
import sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_kernel_acceptance import acceptance
class AcceptanceTests(unittest.TestCase):
    def groups(self):
        return [dict(backend=b,status='PASS',protocol={'version':'exact-kernel-frozen-v1'},pairs=[]) for b in ('agentenv','trenvx')]
    def test_empty_formal_pairs_cannot_pass(self):
        with patch('report_kernel_acceptance.report',side_effect=self.groups()):
            with self.assertRaises(AssertionError):acceptance(*[Path('/unused')]*5)
    def test_run_failure_cannot_pass(self):
        groups=self.groups();groups[0]['status']='FAIL'
        with patch('report_kernel_acceptance.report',side_effect=groups):
            with self.assertRaises(AssertionError):acceptance(*[Path('/unused')]*5)
    def test_unknown_physical_pages_cannot_pass(self):
        groups=self.groups()
        for group in groups:
            off=dict(label='off',memory={'periodic_guest':{'status':'PASS'},'host':{'sample_count':3}},cleanup={'status':'PASS'})
            on=dict(off,label='on',final_frozen_kernel_cache=dict(cache_scope_status='PASS',frozen_cache_physical_status='PASS',classification_status='PASS',host_unknown_pages=1))
            group['pairs']=[[off,on] for _ in range(5)]
        with patch('report_kernel_acceptance.report',side_effect=groups):
            with self.assertRaises(AssertionError):acceptance(*[Path('/unused')]*5)
if __name__=='__main__':unittest.main()
