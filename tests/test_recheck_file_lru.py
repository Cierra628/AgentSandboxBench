import array
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from recheck_file_lru import report


class RecheckTests(unittest.TestCase):
    def fixture(self,root,flags_after=(32,32),same_host=False):
        (root/'sandbox-id.txt').write_text('owned\n')
        probe=root/'host/probes/mixfs-exp5-owned';probe.mkdir(parents=True)
        (probe/'file-pfns.bin').write_bytes(array.array('I',[1,2]).tobytes())
        for label,flags in [('before',(32,32)),('after',flags_after)]:
            (probe/f'flags-{label}.bin').write_bytes(b''.join(struct.pack('<Q',v) for v in flags))
            (probe/f'flags-{label}.json').write_text(json.dumps(dict(status='PASS',sandbox_id='owned',
                selected_pages=2,still_file_lru_pages=sum(v==32 for v in flags))))
        (root/'host/page-map.tsv').write_text('guest_pfn\thost_virtual_address\tpagemap_entry\n'+
            f'1\t0\t{(1<<63)|11:x}\n2\t1000\t{(1<<63)|(11 if same_host else 12):x}\n')

    def test_guest_type_change_is_excluded_not_silently_file_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,(32,4096))
            value=report(root)
            self.assertEqual(value['type_bracketed_file_lru_host_bytes'],4096)
            self.assertEqual(value['invalid_or_unknown_host_bytes'],4096)
            self.assertIsNone(value['current_file_cache_physical_bytes'])

    def test_duplicate_host_page_is_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,same_host=True)
            self.assertEqual(report(root)['type_bracketed_file_lru_host_bytes'],4096)

    def test_conflicting_guest_types_do_not_promote_host_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,(32,4096),same_host=True)
            value=report(root)
            self.assertEqual(value['type_bracketed_file_lru_host_bytes'],0)
            self.assertEqual(value['invalid_or_unknown_host_bytes'],4096)
            self.assertEqual(value['host_pfn_classification_overlap_pages'],1)
