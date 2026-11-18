"""Offline fixtures; no real service, cgroup, device or workload execution."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from experiment_memory import normalize_groups, FILE_CLOSURE_THRESHOLD_BYTES
from run_django_schedule_matrix import planned_runs
from report_experiment_schedule import (collect_parent, stats, periodic_closure, checked_peak,
    historical_periodic_closure, periodic_closure_v2, closure_report, MAX_CONSECUTIVE_DEVIATIONS, MAX_DEVIATION_FRACTION,
    deviation_details, publish)


class ExperimentScheduleTests(unittest.TestCase):
    def fixture(self, storage, *, delta=0, unavailable=False):
        raw=storage/'external-workload';(raw/'host-file').mkdir(parents=True)
        parent='/sys/fs/cgroup/asbhost.slice/asbhost-test.slice';service=parent+'/docker-test.scope'
        values=lambda f:dict(current=f+100,file=f,anon=70,kernel=30)
        groups={parent:values(30+delta),parent+'/controller.service':values(20),service:values(10)}
        value=normalize_groups(parent,groups)
        endpoint=dict(monotonic_ns=25,duration_ns=100,cpu_ns=20,groups=groups,experiment_memory=value)
        context=dict(status='UNAVAILABLE' if unavailable else 'AVAILABLE',parent=None if unavailable else parent)
        task=dict(experiment_memory_context=context,schedule_start_monotonic_ns=10,original_paths_end_monotonic_ns=20)
        metrics=dict(experiment_memory_p1=endpoint,private_service_cgroup=service,cgroup=service,
            p1=dict(files=[dict(mincore_permission={'basis':'HOST_INODE_OWNER'})]),
            pss=dict(processes=[dict(identity={'pid':100+i}) for i in range(7)]))
        for path,v in [(storage/'result.json',dict(host_file_creation={'cgroup':parent})),
                (raw/'result.json',task),(raw/'schedule-smoke-metrics.json',metrics),
                (raw/'calls.json',[dict(stage='cold-backbone',start_monotonic_ns=10,command_end_monotonic_ns=12),
                                   dict(stage='restore-late_1',start_monotonic_ns=15,command_end_monotonic_ns=18),
                                   dict(stage='restored-process-late_1',start_monotonic_ns=18,command_end_monotonic_ns=19)])]:
            path.write_text(json.dumps(v))
        (raw/'host-file/experiment-memory.jsonl').write_text(json.dumps(dict(endpoint,monotonic_ns=15))+'\n')

    def test_one_round_is_exactly_three_and_default_still_nine(self):
        self.assertEqual([r['schedule'] for r in planned_runs('django16819',1)],['grpo','bpo','tvcache'])
        self.assertEqual(len(planned_runs('django16819')),9)
        self.assertEqual([r['schedule'] for r in planned_runs('c4')[3:6]],['bpo','tvcache','grpo'])
        with self.assertRaises(ValueError):planned_runs('c4',4)
        self.assertEqual([r['schedule'] for r in planned_runs('django16819',1,['bpo','tvcache'])],['bpo','tvcache'])
        with self.assertRaises(ValueError):planned_runs('c4',1,['bpo','bpo'])

    def series(self, deltas, currents=None):
        return [dict(monotonic_ns=i,experiment_memory=dict(
            file_closure=dict(status='MISMATCH' if abs(d)>FILE_CLOSURE_THRESHOLD_BYTES else 'WITHIN_THRESHOLD',
                              parent_minus_children_bytes=d),
            parent_counters=dict(current=currents[i] if currents else i,file=100+d)))
            for i,d in enumerate(deltas)]

    def test_historical_recovery_remains_per_point_not_last_point_of_interval(self):
        bad=2*FILE_CLOSURE_THRESHOLD_BYTES
        for deltas,expected,unrecovered in (([0,bad,-bad,0],'PASS',[]),
                ([0,bad,bad,bad,0],'FAIL',[1]),([0,bad],'FAIL',[1]),([bad,0],'PASS',[])):
            with self.subTest(deltas=deltas):
                row=historical_periodic_closure(self.series(deltas))
                self.assertEqual(row['status'],expected)
                self.assertEqual(row['unrecovered_indices'],unrecovered)
        row=periodic_closure(self.series([0,bad,-bad,0]))
        self.assertEqual(row['max_positive_bytes'],bad)
        self.assertEqual(row['min_negative_bytes'],-bad)
        self.assertEqual(row['longest_consecutive_points'],2)

    def test_new_segment_limits_are_inclusive_and_fail_independently(self):
        bad=2*FILE_CLOSURE_THRESHOLD_BYTES
        for deltas,failed in (([0]+[bad]*3+[0]*56,None), # Exactly 3 points and 5%.
                ([0]+[bad]*4+[0]*95,'longest_segment'),
                (([bad,0]*6)+[0]*88,'deviation_fraction'),
                ([0]*99+[bad],'all_segments_recovered')):
            with self.subTest(failed=failed):
                row=periodic_closure_v2(self.series(deltas))
                self.assertEqual(row['status'],'PASS' if failed is None else 'FAIL')
                for name,value in row['requirements'].items():
                    self.assertEqual(value['status'],'FAIL' if name==failed else 'PASS')
        self.assertEqual(MAX_CONSECUTIVE_DEVIATIONS,3)
        self.assertEqual(MAX_DEVIATION_FRACTION,.05)
        self.assertEqual(periodic_closure([])['status'],'FAIL')
        missing=self.series([0]*100);missing[10]['experiment_memory']['file_closure']['status']='INCOMPLETE'
        self.assertEqual(periodic_closure(missing)['status'],'FAIL')

    def test_saved_django_three_schedules_regression(self):
        fixture=Path(__file__).parent/'fixtures/django-file-closure-20261111.json'
        for run in json.loads(fixture.read_text())['runs']:
            samples=[dict(experiment_memory=dict(file_closure=dict(status=r['status'],
                parent_minus_children_bytes=r['delta_bytes'])))
                for r in run['closure_runs'] for _ in range(r['count'])]
            row=periodic_closure(samples);expected=run['expected']
            with self.subTest(schedule=run['schedule']):
                for key in ('total_points','deviation_count','longest_consecutive_points','all_segments_recovered'):
                    self.assertEqual(row[key],expected[key])
                self.assertEqual(row['status'],expected['new_status'])
                self.assertAlmostEqual(row['deviation_fraction'],expected['deviation_count']/expected['total_points'])
                self.assertEqual(historical_periodic_closure(samples)['status'],expected['historical_status'])
                report='\n'.join(closure_report([dict(task='django16819',schedule=run['schedule'],round=1,
                    periodic_file_closure=row,experiment_scope_validation=row['status'])]))
                self.assertIn('5%',report);self.assertIn('1秒／PASS／含标称换算',report)
                self.assertEqual(periodic_closure_v2(samples)['status'],expected['new_status'])

    def test_peaks_keep_raw_values_and_only_use_actual_closed_samples(self):
        samples=self.series([0,2*FILE_CLOSURE_THRESHOLD_BYTES,0,0],[10,100,20,30])
        closure=periodic_closure(samples)
        peak=checked_peak(samples,list(range(4)),'current',closure)
        self.assertTrue(peak['affected'])
        self.assertEqual(peak['raw_bytes'],100)
        self.assertEqual(peak['replacement_bytes'],20)
        self.assertEqual(peak['value_bytes'],30)
        self.assertEqual(peak['selected_index'],3)
        self.assertEqual(checked_peak(samples,[1],'current',closure)['status'],'UNAVAILABLE')
        self.assertEqual(checked_peak(samples,[1,2,3],'current',closure)['status'],'UNAVAILABLE')
        self.assertEqual(peak['boundary_bytes'],[10,20])
        self.assertEqual(checked_peak(samples,[2,3],'current',closure)['status'],'UNCHANGED')

    def test_deviation_detail_keeps_signed_values_times_and_recovery(self):
        samples=self.series([0,2*FILE_CLOSURE_THRESHOLD_BYTES,-2*FILE_CLOSURE_THRESHOLD_BYTES,0]+[0]*56)
        details=deviation_details(samples,periodic_closure(samples),1)
        self.assertEqual([p['index'] for p in details],[1,2])
        self.assertEqual([p['recovery_index'] for p in details],[3,3])
        self.assertEqual(details[1]['seconds_since_schedule_start'],1e-9)
        self.assertEqual(details[1]['parent_minus_children_bytes'],-2*FILE_CLOSURE_THRESHOLD_BYTES)
        self.assertEqual(details[0]['recovery_monotonic_ns'],3)

    def test_report_subset_or_failure_keeps_details_and_no_failed_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);storage=root/'storage';self.fixture(storage)
            row=dict(collect_parent(storage),schedule='grpo',task='c4',round=1,status='FAIL')
            task_path=storage/'external-workload/result.json';task=json.loads(task_path.read_text())
            task['save_restore']=dict(saves=[{'latency_ns':'2000000000'}],restores=[{'latency_ns':'500000000'}])
            task_path.write_text(json.dumps(task));row.update(collect_parent(storage))
            self.assertEqual(row['save_total_seconds'],2)
            self.assertEqual(row['restore_total_seconds'],.5)
            self.assertEqual(row['pss_denominator'],7)
            state=dict(status='FAIL',runs=[row],metadata={'profile':'c4'},expected_runs=9)
            out=root/'report'
            with patch('report_experiment_schedule.read',return_value={'runs_detail':[]}):publish(state,out)
            report=(out/'REPORT.md').read_text()
            self.assertIn('三调度统计对照',report)
            self.assertIn('周期偏离点明细',report)
            self.assertNotIn('Django原检查',report)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['repeat_statistics'][0]['metrics']['slice_file_p1_mib']['count'],0)

    def test_two_recovered_periodic_points_do_not_fail_but_endpoint_still_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'external-workload/host-file/experiment-memory.jsonl'
            good=json.loads(path.read_text());bad=json.loads(path.read_text())
            group=bad['experiment_memory']['parent'];bad['groups'][group]['file']+=2*FILE_CLOSURE_THRESHOLD_BYTES
            bad['experiment_memory']=normalize_groups(group,bad['groups'])
            points=[dict(good,monotonic_ns=14+i/10) for i in range(40)]
            points[1]=dict(bad,monotonic_ns=14.1);points[2]=dict(bad,monotonic_ns=14.2)
            path.write_text(''.join(json.dumps(r)+'\n' for r in points))
            row=collect_parent(root)
            self.assertEqual(row['experiment_scope_validation'],'PASS',row['experiment_scope_errors'])
            self.assertEqual(row['periodic_file_closure']['deviation_count'],2)
            metrics_path=root/'external-workload/schedule-smoke-metrics.json'
            metrics=json.loads(metrics_path.read_text());metrics['experiment_memory_p1']=bad
            metrics_path.write_text(json.dumps(metrics))
            self.assertIn('endpoint file closure failed',collect_parent(root)['experiment_scope_errors'])

    def test_native_gate_runs_before_unlink_and_records_errors_without_skipping_unmount(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/validate_isolated_agentenv.py').read_text()
        gate=source.index("checked=collect_parent(out,creation_receipt=receipt)")
        self.assertLess(source.index("cleanup['mount_and_loop_removed']=True"),gate)
        self.assertLess(gate,source.index("image.unlink();cleanup['successful_fixture_removed']=True"))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            receipt=json.loads((root/'result.json').read_text())
            (root/'result.json').unlink()
            self.assertEqual(collect_parent(root,creation_receipt=receipt)['experiment_scope_validation'],'PASS')

    def test_parent_collection_preserves_values_timing_and_all_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp));row=collect_parent(Path(tmp))
            self.assertEqual(row['experiment_scope_validation'],'PASS')
            self.assertEqual(row['slice_file_p1_mib'],30/2**20)
            self.assertEqual(row['parent_minus_same_run_docker_file_bytes'],20)
            self.assertEqual(len(row['slice_p1']['experiment_memory']['direct_children']),2)
            self.assertEqual(row['file_closure_counts'],{'WITHIN_THRESHOLD':2})
            self.assertEqual(len(row['creation_requests']),2)
            self.assertEqual(row['creation_request_span_seconds'],8/1e9)
            self.assertNotIn('current_peak_mib',row)

    def test_mismatch_is_recorded_and_validation_stops_no_threshold_tuning(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp),delta=FILE_CLOSURE_THRESHOLD_BYTES+1)
            row=collect_parent(Path(tmp))
            self.assertEqual(row['experiment_scope_validation'],'FAIL')
            self.assertEqual(row['file_closure_counts'],{'MISMATCH':2})
            self.assertEqual(row['file_closure_max_abs_bytes'],FILE_CLOSURE_THRESHOLD_BYTES+1)
            self.assertEqual(len(row['file_closure_anomalies']),2)

    def test_unavailable_never_uses_docker_scope_as_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp),unavailable=True);row=collect_parent(Path(tmp))
            self.assertEqual(row['experiment_scope_validation'],'FAIL')
            self.assertIsNone(row['experiment_parent'])
            self.assertIsNone(row['slice_file_p1_mib'])
            self.assertIsNone(row['slice_current_peak_mib'])

    def test_single_sample_stddev_stays_unknown(self):
        self.assertEqual(stats([10]),dict(count=1,mean=10,median=10,minimum=10,maximum=10,range=0,sample_stddev=None))
        self.assertAlmostEqual(stats([10,12])['sample_stddev'],2**.5)


if __name__=='__main__':unittest.main()
