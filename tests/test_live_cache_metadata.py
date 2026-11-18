import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from live_cache_metadata import HEADER, MAGIC, PAGE, SIZE, PRESENT, decode, summarize


class LiveCacheTests(unittest.TestCase):
    def frame(self):
        raw=bytearray(PAGE)
        HEADER.pack_into(raw,0,MAGIC,2,7,100,2,SIZE//PAGE,50,200,1000)
        struct.pack_into('<II',raw,HEADER.size,100,101)
        return raw

    def test_decode_keeps_whole_guest_cached_separate(self):
        value=decode(self.frame())
        result=summarize(value,[dict(guest_pfn=p,first=PRESENT|i,last=PRESENT|i) for p,i in [(100,900),(101,901)]])
        self.assertEqual(value['guest_cached_bytes'],50*1024)
        self.assertEqual(result['declared_file_guest_cache_bytes'],2*PAGE)
        self.assertEqual(result['host_confirmed_cache_bytes'],2*PAGE)

    def test_shared_host_pfn_counted_once(self):
        result=summarize(decode(self.frame()),[dict(guest_pfn=p,first=PRESENT|900,last=PRESENT|900) for p in [100,101]])
        self.assertEqual(result['host_confirmed_cache_bytes'],PAGE)

    def test_changed_and_nonresident_pages_are_separate(self):
        result=summarize(decode(self.frame()),[dict(guest_pfn=100,first=PRESENT|900,last=PRESENT|901),dict(guest_pfn=101,first=0,last=0)])
        self.assertEqual(result['host_confirmed_cache_bytes'],0)
        self.assertEqual(result['host_mapping_changed_pages'],1)
        self.assertEqual(result['host_nonresident_pages'],1)

    def test_odd_publication_and_hidden_pfns_rejected(self):
        raw=self.frame();struct.pack_into('<Q',raw,8,3)
        with self.assertRaises(ValueError):decode(raw)
        with self.assertRaises(ValueError):
            summarize(decode(self.frame()),[dict(guest_pfn=p,first=PRESENT,last=PRESENT) for p in [100,101]])

    def test_missing_or_duplicate_rows_rejected(self):
        with self.assertRaises(ValueError):summarize(decode(self.frame()),[])
        with self.assertRaises(ValueError):
            summarize(decode(self.frame()),[dict(guest_pfn=100,first=0,last=0)]*2)


if __name__=='__main__':unittest.main()
