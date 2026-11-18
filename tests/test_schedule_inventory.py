"""Pure saved-result formatting tests; no processes, cgroups or device queries."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from report_schedule_inventory import block_metric, number, tables, record


class ScheduleInventoryTests(unittest.TestCase):
    def test_uncollected_values_stay_none(self):
        for method in ('mincore', 'cachestat'):
            result=block_metric({}, method)
            self.assertEqual(result['status'], 'NOT_COLLECTED')
            self.assertIsNone(result['observed_subtotal_bytes'])
            self.assertIsNone(result['complete_bytes'])

    def test_not_requested_and_measured_zero_are_distinct(self):
        missing=dict(status='NOT_REQUESTED', observed_subtotal_bytes=None, complete_bytes=None)
        measured=dict(status='OBSERVED', observed_subtotal_bytes=0, complete_bytes=0)
        layers=dict(block_devices=dict(method_totals=dict(mincore=missing, cachestat=measured)))
        self.assertEqual(block_metric(layers, 'mincore'), missing)
        self.assertEqual(block_metric(layers, 'cachestat'), measured)
        self.assertEqual(number(0), '0.000')
        self.assertNotEqual(number(None), number(0))

    def test_partial_keeps_subtotal_without_inventing_complete_value(self):
        partial=dict(status='PARTIAL', observed_objects=9, unknown_objects=1,
                     observed_subtotal_bytes=4096, complete_bytes=None)
        layers=dict(block_devices=dict(method_totals=dict(cachestat=partial)))
        self.assertEqual(block_metric(layers, 'cachestat'), partial)
        self.assertIsNot(block_metric(layers, 'cachestat'), partial)

    def test_legacy_mincore_is_not_cachestat(self):
        layers=dict(block_devices=dict(status='PARTIAL', stable_resident_bytes=8192))
        self.assertEqual(block_metric(layers, 'mincore')['observed_subtotal_bytes'], 8192)
        self.assertIsNone(block_metric(layers, 'mincore')['complete_bytes'])
        self.assertEqual(block_metric(layers, 'cachestat')['status'], 'NOT_COLLECTED')

    def test_tables_keep_units_unknowns_and_actual_actions(self):
        row=dict(schedule='bpo', current_peak_bytes=4*2**20, current_at_initial_p1_bytes=3*2**20,
            file_at_initial_p1_bytes=2**20, pss_total_bytes=7*2**20, pss_per_guest_bytes=2**20,
            initial_lower_resident_bytes=2**20, regular_stable_subtotal_bytes=2*2**20,
            regular_status='PARTIAL', block_cachestat=block_metric({}, 'cachestat'),
            actual_actions=84, logical_actions=189, save_calls=2, restore_calls=6,
            action_download_window_seconds=3, tool_seconds=7, controller_seconds=9, endpoint_wall_seconds=.5)
        text='\n'.join(tables([row]))
        self.assertIn('current全采集窗口峰值 MiB', text)
        self.assertIn('—／NOT_COLLECTED', text)
        self.assertIn('84/189 | 2/6', text)
        self.assertIn('2.000／PARTIAL', text)
        row.update(loop_status='UNKNOWN',loop_resident_bytes=None,
            loop_query_window_status='OBSERVED',loop_query_window_bytes=0)
        self.assertIn('UNKNOWN／— | OBSERVED／0.000', '\n'.join(tables([row])))

    def test_record_passes_new_loop_fields_without_replacing_strict_unknown(self):
        run=dict(status='PASS',schedule='grpo',raw='/saved/fake',pss_total_mib=0,pss_per_sandbox_mib=0,
            mincore_p1_mib=0,actual_actions=189,logical_actions=189,save_calls=[],restore_calls=[],
            controller_seconds=0,tool_seconds=0,action_download_window_seconds=0,
            endpoint_wall_seconds=0,endpoint_cpu_seconds=0)
        task=dict(status='PASS',schedule='grpo',paths={str(i):dict(official_passed=0,official_failed=2) for i in range(7)})
        metrics=dict(cgroup='/sys/fs/cgroup/test',pss=dict(denominator=7,total_bytes=0,per_sandbox_bytes=0,
            processes=[dict(identity=dict(cgroup='/test')) for _ in range(7)]),
            p1=dict(resident_bytes=0,unique_files=19,counters_before=dict(current=0,file=0)),
            expanded_files=dict(status='OBSERVED',files=[],unique_objects=0,stable_regular_resident_bytes=0,
                unknown_objects=0,unresolved=[]),cache_layers=dict(outer_loop=dict(status='UNKNOWN',resident_bytes=None,
                    query_window=dict(status='OBSERVED',resident_bytes=0)),block_devices=dict(status='PARTIAL',stable_resident_bytes=None,
                        fixed_ram_totals=dict(status='OBSERVED',complete_bytes=8192))))
        with patch('report_schedule_inventory.read',side_effect=[metrics,task]), \
             patch('report_schedule_inventory.rows',return_value=[dict(cgroup_current_bytes='0')]):
            row=record(run,dict(stable_regular_mib=0,changed_file_readout_mib=0),'synthetic')
        self.assertIsNone(row['loop_resident_bytes'])
        self.assertEqual(row['loop_query_window_bytes'],0)
        self.assertEqual(row['loop_status'],'UNKNOWN')
        self.assertEqual(row['fixed_ram_status'],'OBSERVED')
        self.assertEqual(row['fixed_ram_cache_bytes'],8192)
        self.assertIsNone(row['block_cachestat']['complete_bytes'])
        self.assertIn('独立固定RAM设备查询状态','\n'.join(tables([row])))


if __name__=='__main__':unittest.main()
