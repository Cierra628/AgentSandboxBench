"""Synthetic stage records only; no devices or services."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_live_cache_costs import BASE,point_costs,summarize


class CostReportTests(unittest.TestCase):
    def point(self):
        stages={name:dict(cpu_ns=1,elapsed_ns=5,host_start_ns=1000+i*10,host_end_ns=1005+i*10)
            for i,name in enumerate(sorted(BASE))}
        return dict(point=0,host_stage_costs=stages,host_sample_start_ns=1000,
            host_thread_cpu_ns=11,host_elapsed_ns=100)

    def test_categories_and_residual_equal_original_total(self):
        p=self.point();value=summarize([p],dict(host_thread_total_cpu_ns=31))
        self.assertEqual(value['status'],'PASS')
        row=value['points'][0]
        self.assertEqual(sum(c['cpu_ns'] for c in row['categories'].values()),11)
        self.assertEqual(sum(c['elapsed_ns'] for c in row['categories'].values()),100)
        self.assertEqual(row['categories']['unmeasured'],dict(cpu_ns=2,elapsed_ns=55))
        self.assertEqual(value['outside_point_cpu_total_ns'],20)

    def test_old_data_not_recorded_is_not_zero_or_pass(self):
        p=self.point();p.pop('host_stage_costs')
        self.assertEqual(summarize([p],{}),dict(status='NOT_RECORDED',samples=1,points=[]))

    def test_missing_or_partial_records_fail(self):
        p=self.point();p['host_stage_costs'].pop(next(iter(p['host_stage_costs'])))
        with self.assertRaisesRegex(AssertionError,'missing stage'):point_costs(p)
        p=self.point();old=copy.deepcopy(p);old.pop('host_stage_costs')
        with self.assertRaisesRegex(AssertionError,'partial'):summarize([p,old],{})

    def test_overlapping_or_outside_intervals_fail(self):
        p=self.point();first=next(iter(p['host_stage_costs'].values()))
        first['host_start_ns']-=1
        with self.assertRaises(AssertionError):point_costs(p)
        p=self.point();p['host_elapsed_ns']=80
        with self.assertRaisesRegex(AssertionError,'outside point'):point_costs(p)

    def test_costs_cannot_exceed_total_or_be_negative(self):
        p=self.point();p['host_thread_cpu_ns']=8
        with self.assertRaisesRegex(AssertionError,'exceed point'):point_costs(p)
        p=self.point();next(iter(p['host_stage_costs'].values()))['cpu_ns']=-1
        with self.assertRaises(AssertionError):point_costs(p)


if __name__=='__main__':unittest.main()
