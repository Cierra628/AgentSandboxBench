"""Pure comparison arithmetic; no services, guests or pressure process."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_host_pressure import gap_ratio,latency_difference,counter_delta,tradeoff,normalize_pressure_release


class PressureReportTests(unittest.TestCase):
    def test_gap_compression_keeps_sign_and_undefined_denominator(self):
        value=gap_ratio(20,60,20,30)
        self.assertEqual(value,dict(no_pressure_gap=40,pressure_gap=10,compression_ratio=.25))
        self.assertEqual(gap_ratio(20,60,30,20)['compression_ratio'],-.25)
        self.assertIsNone(gap_ratio(20,20,20,30)['compression_ratio'])

    def test_latency_and_counter_differences_do_not_hide_negative_values(self):
        self.assertEqual(latency_difference(10,12),dict(seconds=2,percent=(12/10-1)*100))
        self.assertEqual(counter_delta([{'x':'12'},{'x':'7'}],'x'),-5)
        self.assertEqual(latency_difference(10,8)['seconds'],-2)

    def test_tradeoff_reports_seconds_per_recovered_gib_only_when_gap_shrinks(self):
        value=tradeoff(dict(no_pressure_gap=2048,pressure_gap=1024),dict(difference_of_differences_seconds=3))
        self.assertEqual(value['seconds_per_recovered_gib'],3)
        self.assertIsNone(tradeoff(dict(no_pressure_gap=1024,pressure_gap=2048),dict(difference_of_differences_seconds=3))['seconds_per_recovered_gib'])

    def test_release_placeholders_are_not_interpreted_as_physical_zero(self):
        source=[dict(pressure_phase='RELEASING',pressure_rss_bytes='0',pressure_anon_bytes='0',pressure_swap_bytes='0')]
        result=normalize_pressure_release(source,True)
        self.assertEqual(result[0]['pressure_anon_bytes'],'');self.assertEqual(source[0]['pressure_anon_bytes'],'0')
        self.assertEqual(normalize_pressure_release(source,False),source)


if __name__=='__main__':unittest.main()
