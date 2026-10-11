from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_whole_analysis_controls import verify_recount
class KernelAuditTests(unittest.TestCase):
    def test_one_allocation_block_change_preserves_identical_evidence(self):
        saved=dict(retained_bytes=8192,full_run_ns=17,physical={'status':'PASS'})
        verify_recount(dict(saved,retained_bytes=12288),saved,{'sha':'abc'},{'sha':'abc'},16384)
    def test_timing_or_inputs_cannot_change(self):
        saved=dict(retained_bytes=8192,full_run_ns=17)
        with self.assertRaises(AssertionError):verify_recount(dict(saved,full_run_ns=18),saved,{}, {},16384)
        with self.assertRaises(AssertionError):verify_recount(saved,saved,{'sha':'changed'},{'sha':'abc'},16384)
    def test_storage_budget_and_old_strict_audit_remain_enforced(self):
        saved=dict(retained_bytes=8192)
        with self.assertRaises(AssertionError):verify_recount(dict(retained_bytes=16384),saved,{}, {},16384)
        with self.assertRaises(AssertionError):verify_recount(dict(retained_bytes=12288),saved,{}, {})
if __name__=='__main__':unittest.main()
