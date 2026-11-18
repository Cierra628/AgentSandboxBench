"""Six-observation statistics retain signs and variation; no live resources."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_python_cache_repeats import spread,source_session,paired_summary,low_high_summary


class RepeatReportTests(unittest.TestCase):
    def test_six_signed_values_remain_in_original_round_order(self):
        values=[10,-5,0,30,-20,3]
        result=spread(values)
        self.assertEqual(result['values'],values)
        self.assertEqual(result['positive'],3); self.assertEqual(result['negative'],2)
        self.assertEqual(result['minimum'],-20); self.assertEqual(result['maximum'],30)
        self.assertEqual(result['median'],1.5)
        self.assertAlmostEqual(result['mean'],3)
        self.assertGreater(result['sample_stdev'],0)

    def test_incomplete_observations_are_not_reported_as_six_rounds(self):
        with self.assertRaises(AssertionError): spread([1,2,3,4,5])

    def test_only_recorded_source_restart_is_accepted(self):
        binding=dict(service='owned',identity=dict(Pid=1,StartedAt='old',Running=True))
        pause=dict(completed_runs=31);resume=dict(identity=dict(Pid=2,StartedAt='new',Running=True))
        old=dict(source_service_name='owned',original_service=binding['identity'])
        new=dict(source_service_name='owned',original_service=resume['identity'])
        self.assertEqual(source_session(dict(index=31),old,binding,pause,resume),'before_pause')
        self.assertEqual(source_session(dict(index=32),new,binding,pause,resume),'after_pause')
        with self.assertRaises(AssertionError): source_session(dict(index=31),new,binding,pause,resume)
        with self.assertRaises(AssertionError): source_session(dict(index=32),old,binding,pause,resume)

    def test_cross_pause_pair_is_preserved_but_not_pooled_with_continuous_pairs(self):
        group=[dict(round=n,cross_pause_pair=n==6,
                    comparison={k:dict(delta_percent=v) for k in ['tool_time','replay_with_checks','guest_read']})
               for n,v in enumerate([1,2,3,4,5,100],1)]
        result=paired_summary(group)
        self.assertEqual(result['all_six']['tool_time']['count'],6)
        self.assertEqual(result['continuous']['tool_time']['count'],5)
        self.assertEqual(result['continuous']['tool_time']['mean'],3)
        self.assertEqual(result['cross_pause'][0]['comparison']['tool_time']['delta_percent'],100)

    def test_low_high_uses_high_denominator_and_requires_same_session(self):
        rows=[dict(task='task',round=n,condition=c,source_session='after' if n==6 else 'before',
                   task_tool_sum_ns=v,replay_with_checks_ns=v,guest_device_read_bytes=v)
              for n in range(1,7) for c,v in [('L',120),('H',100)]]
        self.assertEqual(low_high_summary(rows,'task')['task_tool_sum_ns']['values'],[20]*6)
        rows[-1]['source_session']='unexpected'
        with self.assertRaises(AssertionError):low_high_summary(rows,'task')


if __name__=='__main__': unittest.main()
