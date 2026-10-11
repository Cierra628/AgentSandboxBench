import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trenvx_kvm_capture import recount_capture


class CaptureTests(unittest.TestCase):
    def check(self,lines,ram='0-fff : System RAM\n',status='PASS'):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/'stop.json').write_text(json.dumps(dict(namespace_inode=17,status=status)))
            (directory/'raw.tsv').write_text('READY\n'+lines)
            return recount_capture(directory,dict(pid=3,start_time_ticks=100),ram)

    def row(self,pid=3,slot=0,guest=0,size=4096,host=8192,ret=0,ns=17,start=1_000_000_000):
        return f'{pid}\t{slot}\t{guest:x}\t{size:x}\t{host:x}\t{ret}\t700\t{ns}\t{start}\n'

    def test_ram_selected_without_dax_slot(self):
        r=self.check(self.row()+self.row(slot=1,guest=1<<40,host=16384))
        self.assertEqual(r['ram_slot_ids'],[0]);self.assertEqual(r['slots'],[dict(guest=0,size=4096,host=8192)])

    def test_wrong_identity_is_rejected(self):
        for kwargs in [dict(ns=18),dict(start=2_000_000_000)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(RuntimeError):self.check(self.row(**kwargs))

    def test_missing_failed_removed_events_are_rejected(self):
        for line in [self.row(pid=4),self.row(ret=-22),self.row(size=0)]:
            with self.subTest(line=line),self.assertRaises(RuntimeError):self.check(line)

    def test_missing_and_excess_ram_ranges_are_rejected(self):
        for ram in ['', '0-1fff : System RAM\n','1000-1fff : System RAM\n']:
            with self.subTest(ram=ram),self.assertRaises(RuntimeError):self.check(self.row(),ram)

    def test_overlap_and_unaligned_address_are_rejected(self):
        for line in [self.row()+self.row(slot=1),self.row(host=8193)]:
            with self.subTest(line=line),self.assertRaises((RuntimeError,AssertionError)):self.check(line)

    def test_failed_capture_is_rejected(self):
        with self.assertRaises(RuntimeError):self.check(self.row(),status='FAIL')

    def test_explicit_reserved_page_is_supported_but_unknown_hole_rejected(self):
        ram='0-fff : Reserved\n1000-1fff : System RAM\n'
        self.assertEqual(self.check(self.row(size=8192),ram)['slots'][0]['size'],8192)
        for label in ['PCI Bus 0000:00','unreported']:
            with self.assertRaises(RuntimeError):self.check(self.row(size=8192),ram.replace('Reserved',label))


if __name__=='__main__':unittest.main()
