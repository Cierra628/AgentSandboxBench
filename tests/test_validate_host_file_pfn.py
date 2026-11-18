"""Pure comparison tests only; no mapping, mincore, BPF or services."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import validate_host_file_pfn as v


class PositiveFilePfnTests(unittest.TestCase):
    def fixture(self):
        pfns=np.arange(100,100+v.PAGES,dtype='<u8')
        ref=pfns|v.PRESENT|np.uint64(1<<61)
        rows=np.column_stack((np.arange(v.PAGES,dtype='<u8'),np.ones(v.PAGES,dtype='<u8'),
                              np.ones(v.PAGES,dtype='<u8'),pfns|v.FOLIO))
        return ref,rows

    def test_exact_offset_matches_and_aliases_are_deduplicated(self):
        ref,rows=self.fixture();ref[1]=ref[0];rows[1,3]=rows[0,3]
        result=v.compare(ref,ref.copy(),rows)
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['matched_pages'],16)
        self.assertEqual(result['unique_physical_pages'],15)
        self.assertEqual(result['query_physical_bytes'],15*4096)

    def test_changed_reference_or_wrong_pfn_cannot_pass(self):
        ref,rows=self.fixture();after=ref.copy();after[0]^=np.uint64(1)
        with self.assertRaisesRegex(ValueError,'changed'):v.compare(ref,after,rows)
        rows[0,3]^=np.uint64(1)
        with self.assertRaisesRegex(ValueError,'differs'):v.compare(ref,ref,rows)

    def test_hidden_nonresident_anonymous_and_swap_references_fail(self):
        ref,rows=self.fixture()
        for value in (v.PRESENT|(1<<61),100,v.PRESENT|100,v.SWAPPED|100):
            changed=ref.copy();changed[0]=value
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'reference'):
                v.compare(changed,changed,rows)

    def test_missing_returns_null_and_wrong_offsets_fail(self):
        ref,rows=self.fixture()
        for column,value in ((2,0),(3,0),(0,1)):
            changed=rows.copy();changed[0,column]=value
            with self.subTest(column=column),self.assertRaises(ValueError):v.compare(ref,ref,changed)

    def test_soft_dirty_is_not_physical_change_and_clear_mincore_does_not_drop_pfn(self):
        ref,rows=self.fixture();after=ref.copy();after[0]|=np.uint64(1<<55);rows[0,1]=0
        result=v.compare(ref,after,rows)
        self.assertEqual(result['mincore_set_pages'],15)
        self.assertEqual(result['query_physical_bytes'],16*4096)

    def test_dimensions_and_explicit_live_gate(self):
        ref,rows=self.fixture()
        with self.assertRaises(ValueError):v.compare(ref[:-1],ref,rows)
        with patch.object(sys,'argv',['calibrate','--output','/not-created']),\
                patch.object(v,'calibrate') as collect,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):v.main()
            collect.assert_not_called()


if __name__=='__main__':unittest.main()
