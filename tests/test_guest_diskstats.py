from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from guest_diskstats import parse,delta


class GuestDiskstatsTests(unittest.TestCase):
    def test_delta_keeps_devices_separate(self):
        before=parse('8 0 vda 10 0 20 3 4 0 8 2 0 5 6\n8 1 vda1 7 0 14 2 4 0 8 2 0 5 6\n')
        after=parse('8 0 vda 12 0 36 5 4 0 8 2 0 7 8\n8 1 vda1 9 0 30 4 4 0 8 2 0 7 8\n')
        rows=delta(before,after)
        self.assertEqual(rows['vda']['reads_completed'],2)
        self.assertEqual(rows['vda1']['reads_completed'],2)
        self.assertEqual(rows['vda']['sectors_read'],16)
        self.assertEqual(rows['vda']['read_ms'],2)

    def test_missing_device_or_decreased_counter_rejected(self):
        rows=parse('8 0 vda 10 0 20 3 4 0 8 2 0 5 6\n')
        with self.assertRaises(AssertionError):delta(rows,{})
        changed=parse('8 0 vda 9 0 20 3 4 0 8 2 0 5 6\n')
        with self.assertRaises(AssertionError):delta(rows,changed)
        with self.assertRaises(AssertionError):parse('')


if __name__=='__main__':unittest.main()
