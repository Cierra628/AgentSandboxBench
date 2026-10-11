import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from exp1_physical_workload import file_lru_accounting


class FileLruAccountingTests(unittest.TestCase):
    def fixture(self,root):
        (root/'host').mkdir()
        (root/'host/physical-memory.json').write_text(json.dumps(dict(
            guest_file_cache_logical_pages=3,dax_file_host_physical_pss_mib=0,
            guest_file_cache_host_physical_mib=8192/2**20)))
        (root/'coverage.json').write_text(json.dumps(dict(unmapped_pages=0,
            nonresident_mappings=1,logical_pages=3,independent_unique_host_pages=2,scan_ns=80)))
        for when,value in [('before',100),('after',70)]:
            (root/f'exp1-memory-{when}-probe.stdout').write_text(f'Cached: {value} kB\nBuffers: 10 kB\nShmem: 1 kB\n')
        (root/'guest-probe-duration-ns.txt').write_text('400')

    def test_successful_mapping_does_not_prove_current_page_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            result=file_lru_accounting(root)
            self.assertEqual(result['status'],'PARTIAL')
            self.assertIsNone(result['current_file_cache_physical_bytes'])
            self.assertEqual(result['selected_guest_file_lru_host_bytes'],8192)
            self.assertEqual(result['guest_cache_and_buffers_delta_bytes'],-30*1024)
            self.assertEqual(result['selected_nonresident_mappings'],1)

    def test_contradictory_unique_page_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'coverage.json';value=json.loads(path.read_text())
            value['independent_unique_host_pages']=3;path.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):file_lru_accounting(root)

    def test_type_recheck_is_reported_without_promoting_complete_cache_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            value=dict(status='PARTIAL',selected_guest_pages=3,current_file_cache_physical_bytes=None)
            (root/'file-type-recheck.json').write_text(json.dumps(value))
            result=file_lru_accounting(root)
            self.assertEqual(result['type_recheck'],value)
            self.assertIsNone(result['current_file_cache_physical_bytes'])
            self.assertIn('non-atomic',result['reason'])
