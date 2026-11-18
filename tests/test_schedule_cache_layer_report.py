"""Saved-data identity relation only; no live kernel or service queries."""
import copy
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_schedule_cache_layers as m
from report_schedule_cache_layers import ram_log_matches


class CacheLayerReportTests(unittest.TestCase):
    def test_ublk_id_is_not_linux_block_minor(self):
        obj=dict(rdev=66309,aliases=[dict(source='vmm_ram_mapping',region=dict(mapping_path='/dev/ublkb109'))])
        self.assertTrue(ram_log_matches(obj,dict(path='/dev/ublkb109',dev_id=109)))
        self.assertFalse(ram_log_matches(obj,dict(path='/dev/ublkb5',dev_id=5)))
        self.assertFalse(ram_log_matches(obj,dict(path='/dev/ublkb109',dev_id=5)))


    def fixture(self,storage):
        stamp=dict(start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=0)
        objects=[]
        for i in range(10):
            aliases=[dict(source='drive',role='backbone')]
            if i>=8:
                aliases=[dict(source='vmm_ram_mapping',role=role,region=dict(mapping_path='/dev/ublkb'+str(i)))
                         for role in ('early_1','early_2','early_3')]
            objects.append(dict(stamp,object_key=['block',i],rdev=i,mapping_length=8192,
                aliases=aliases,status='OBSERVED',resident_bytes=4096,
                residency=dict(stable=True,resident_bytes=4096,rdev=i,mapping_length=8192)))
        expanded=dict(stamp,owned_roots=[str(storage/'mount'/n) for n in ('env','run','tmp')],
            files=[],status='PARTIAL',unique_objects=0,stable_regular_resident_bytes=0,
            unresolved=[],page_size=4096)
        devices=dict(stamp,status='OBSERVED',objects=objects,errors=[],stable_resident_bytes=40960)
        layers=dict(stamp,block_devices=devices,deleted_tmpfs=dict(stamp,objects=[],missing_sources=[],
            status='NOT_REQUESTED',whole_backing_cache_bytes=None,worker_returncode=None,worker_reaped=None),
            outer_loop=dict(stamp,status='UNKNOWN',resident_bytes=None,error='synthetic failure'),
            worker_cpu_ns=None,process_errors=[],counters_before=dict(file=0),counters_after=dict(file=0))
        metrics=dict(expanded_files=expanded,cache_layers=layers,p1=dict(cpu_ns=0),
            drives={'backbone':[dict(rdev=i,block_device=True) for i in range(8)]})
        base=dict(schedule='bpo',actual_actions=84,save_calls=[{},{}],restore_calls=[{}]*6,
            mincore_p1_mib=0,mincore_wall_seconds=0,endpoint_wall_seconds=0,endpoint_cpu_seconds=0,
            action_download_window_seconds=0,tool_seconds=0,controller_seconds=0)
        return metrics,base

    def collect_fixture(self,storage,metrics,base,log_ids=(8,9),extra_log=''):
        raw=storage/'prettier6604';raw.mkdir(exist_ok=True)
        (raw/'schedule-smoke-metrics.json').write_text(json.dumps(metrics))
        (storage/'service-logs').mkdir(exist_ok=True)
        (storage/'service-logs/server.log').write_text(''.join(
            f'created or acquired shared read-only ublk device path=/dev/ublkb{i} /memory/image.json\n' for i in log_ids)+extra_log)
        (storage/'result.json').write_text(json.dumps(dict(observed_host_available_min_bytes=2**30,cleanup={})))
        source=storage/'batch.json'
        source.write_text(json.dumps(dict(status='PASS',source_returned_to_stopped=True,cache_released=True,
            runs=[dict(schedule=base['schedule'],status='PASS',storage=str(storage))])))
        with patch.object(m,'summarize',return_value=base),patch.object(m,'expanded_summary',return_value={}):
            return m.collect(source)

    def add_method(self,metrics,method):
        d=metrics['cache_layers']['block_devices'];d['method']=method
        for row in d['objects']:
            row['cache_index_bytes']=8192;row['queries']={}
            methods=('mincore','cachestat') if method=='compare' else (method,)
            for selected in methods:
                q=dict(status='OBSERVED',method=selected,resident_bytes=None,cache_index_bytes=None,
                    range=dict(offset=0,length=8192),start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=0,
                    stages=dict(core_query=dict(start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=0)))
                if selected=='mincore':q.update(resident_bytes=4096,residency=row['residency'])
                else:q.update(cache_index_bytes=8192,cache_counters=dict(nr_cache=2))
                row['queries'][selected]=q
            if method=='cachestat':row['resident_bytes']=None;row.pop('residency')
        d['stable_cache_index_bytes']=81920
        if method=='cachestat':d['stable_resident_bytes']=None
        d['method_totals']={
            'mincore':dict(status='NOT_REQUESTED' if method=='cachestat' else 'OBSERVED',
                observed_objects=0 if method=='cachestat' else 10,unknown_objects=0,
                observed_subtotal_bytes=None if method=='cachestat' else 40960,
                complete_bytes=None if method=='cachestat' else 40960),
            'cachestat':dict(status='OBSERVED',observed_objects=10,unknown_objects=0,
                observed_subtotal_bytes=81920,complete_bytes=81920)}

    def test_legacy_report_and_collect_without_method_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            value=self.collect_fixture(storage,metrics,base);text=m.report(value)
        self.assertIn('ublk块设备mincore',text)
        self.assertNotIn('块设备查询方法（独立指标）',text)
        self.assertEqual(value['layers']['block_devices']['stable_resident_bytes'],40960)

    def test_extra_transient_ram_creations_do_not_replace_active_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            value=self.collect_fixture(storage,metrics,base,log_ids=(8,10,9,11,9),
                extra_log='last reference to shared read-only ublk device dropped, scheduling release path=/dev/ublkb11 /memory/image.json\n')
            self.assertEqual([r['dev_id'] for r in value['ram_log_bindings']],[8,10,9,11,9])

    def test_grpo_has_eight_devices_without_restore_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            base.update(schedule='grpo',actual_actions=189,save_calls=[],restore_calls=[])
            devices=metrics['cache_layers']['block_devices']
            devices['objects']=devices['objects'][:8];devices['stable_resident_bytes']=8*4096
            value=self.collect_fixture(storage,metrics,base,log_ids=())
            text=m.report(value)
            self.assertIn('七沙箱GRPO：189个实际动作',text)
            self.assertIn('0次保存、0次恢复',text)
            self.assertIn('8个块设备',text)
            self.assertEqual(value['ram_log_bindings'],[])
            devices['objects'].pop()
            with self.assertRaises(AssertionError):self.collect_fixture(storage,metrics,base,log_ids=())

    def test_tvcache_requires_two_restore_ram_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage);base['schedule']='tvcache'
            value=self.collect_fixture(storage,metrics,base)
            self.assertIn('七沙箱TVCACHE：84个实际动作',m.report(value))
            metrics['cache_layers']['block_devices']['objects'].pop()
            with self.assertRaises(AssertionError):self.collect_fixture(storage,metrics,base)

    def test_missing_active_ram_creation_is_rejected_even_with_extra_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            with self.assertRaises(AssertionError):
                self.collect_fixture(storage,metrics,base,log_ids=(8,10,11),
                    extra_log='last reference to shared read-only ublk device dropped, scheduling release path=/dev/ublkb9 /memory/image.json\n')

    def test_new_reports_separate_mincore_cachestat_and_missing_method(self):
        for method in ('cachestat','compare'):
            with self.subTest(method=method),tempfile.TemporaryDirectory() as tmp:
                storage=Path(tmp);metrics,base=self.fixture(storage);self.add_method(metrics,method)
                value=self.collect_fixture(storage,metrics,base);text=m.report(value)
                self.assertIn('块设备查询方法（独立指标）',text)
                self.assertIn('| cachestat | OBSERVED | 10/0 | 0.078125 | 0.078125 |',text)
                if method=='cachestat':
                    self.assertIn('| mincore | NOT_REQUESTED | 0/0 | 未知 | 未知 |',text)
                    self.assertIn('| ublk块设备mincore（drive及RAM） | NOT_REQUESTED',text)
                else:self.assertIn('| mincore | OBSERVED | 10/0 | 0.039062 | 0.039062 |',text)
                broken=copy.deepcopy(metrics)
                broken['cache_layers']['block_devices']['objects'][0]['queries']['cachestat']['range']['length']=1
                with self.assertRaises(AssertionError):self.collect_fixture(storage,broken,base)

    def test_compare_failure_preserves_other_method_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage);self.add_method(metrics,'compare')
            d=metrics['cache_layers']['block_devices'];d['status']='PARTIAL';row=d['objects'][0]
            row.update(status='UNKNOWN',cache_index_bytes=None)
            row['queries']['cachestat'].update(status='UNKNOWN',cache_index_bytes=None,error='source mapping identity changed',stages={},
                failed_reference=dict(path='/fake/alias',role='late_3'),mapping_diagnostic=dict(reason='FIELDS_CHANGED',
                    changed_fields=dict(offset=dict(expected=4096,observed=0))))
            d['method_totals']['cachestat'].update(status='PARTIAL',observed_objects=9,unknown_objects=1,
                observed_subtotal_bytes=73728,complete_bytes=None)
            d['stable_cache_index_bytes']=73728
            value=self.collect_fixture(storage,metrics,base);text=m.report(value)
        self.assertIn('| mincore | OBSERVED | 10/0 |',text)
        self.assertIn('| cachestat | PARTIAL | 9/1 | 0.070312 | 未知 |',text)
        self.assertIn('source mapping identity changed',text);self.assertIn('未执行',text)
        self.assertIn('"failed_reference"',text);self.assertIn('/fake/alias',text)
        self.assertIn('"changed_fields"',text);self.assertIn('"offset"',text)

    def test_method_costs_keep_failed_checks_but_not_nonexistent_core_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics,_=self.fixture(Path(tmp));self.add_method(metrics,'compare')
        devices=metrics['cache_layers']['block_devices']
        failed=devices['objects'][0]['queries']['cachestat']
        failed.update(status='UNKNOWN',cache_index_bytes=None,stages=dict(
            identity_before=dict(start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1)))
        value=m.method_costs(devices)
        self.assertEqual(value['cachestat']['attempted_objects'],10)
        self.assertEqual(value['cachestat']['observed_objects'],9)
        self.assertAlmostEqual(value['cachestat']['wall_seconds'],10e-9)
        self.assertAlmostEqual(value['cachestat']['observed_wall_seconds'],9e-9)
        self.assertEqual(value['cachestat']['stages']['core_query']['objects'],9)
        self.assertEqual(value['cachestat']['stages']['identity_before']['objects'],1)
        self.assertEqual(value['cachestat']['stages']['identity_after']['objects'],0)
        self.assertEqual(m.method_costs(dict(objects=[])),{})

    def test_loop_window_is_reported_separately_without_filling_strict_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            loop=metrics['cache_layers']['outer_loop']
            before=dict(device=1,inode=2,rdev=0,size=8192,mtime_ns=100)
            loop.update(loop_before=dict(backing_device=1,backing_inode=2),loop_after=dict(backing_device=1,backing_inode=2),
                error="ValueError('loop/backing changed during query')",
                residency=dict(device=1,inode=2,rdev=0,size=8192,stable=False,identity_stable=True,size_stable=True,
                    mapping_length=8192,resident_bytes=4096,metadata_before=before,
                    metadata_after=dict(before,mtime_ns=101),metadata_changes=dict(mtime_ns=dict(before=100,after=101))),
                query_window=dict(status='OBSERVED',resident_bytes=4096,range=dict(offset=0,length=8192),
                    rejected_checks=[],strict_rejection_reason='MTIME_CHANGED'))
            value=self.collect_fixture(storage,metrics,base);text=m.report(value)
            self.assertIsNone(value['layers']['outer_loop']['resident_bytes'])
            self.assertIn('新窗口状态OBSERVED，窗口驻留0.003906 MiB',text)
            self.assertIn('原严格状态UNKNOWN，原严格驻留未知',text)
            self.assertIn('metadata_after',text)
            loop['residency']['identity_stable']=False
            with self.assertRaises(AssertionError):self.collect_fixture(storage,metrics,base)

    def test_unknown_loop_window_never_gets_a_numeric_complete_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage)
            loop=metrics['cache_layers']['outer_loop']
            loop['query_window']=dict(status='UNKNOWN',resident_bytes=None,rejected_checks=[])
            value=self.collect_fixture(storage,metrics,base)
            self.assertIn('新窗口状态UNKNOWN，窗口驻留未知',m.report(value))
            loop['query_window']['resident_bytes']=0
            with self.assertRaises(AssertionError):self.collect_fixture(storage,metrics,base)

    def test_fixed_ram_query_and_changed_sources_do_not_fill_old_strict_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage=Path(tmp);metrics,base=self.fixture(storage);self.add_method(metrics,'cachestat')
            e=metrics['expanded_files'];e['process_identities']={'101':{}}
            d=metrics['cache_layers']['block_devices'];stamp=dict(start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=0)
            for obj in d['objects'][8:]:
                for alias in obj['aliases']:alias['rdev']=obj['rdev']
                anchor=dict(source='open_block_fd',region=None,process=101,path='/proc/101/fd/13',role=None,
                    device=1,inode=2,rdev=obj['rdev'])
                obj['aliases'].insert(0,anchor)
                query=dict(obj['queries']['cachestat'],stages={k:dict(stamp) for k in ('identity_before','core_query','identity_after')})
                obj['fixed_ram_device']=dict(stamp,rdev=obj['rdev'],anchor=anchor,query=query,prepare_errors=[],
                    source_relationships=dict(status='OBSERVED',before_complete=True,after_complete=True))
            bad=d['objects'][8];bad.update(status='UNKNOWN',cache_index_bytes=None)
            bad['queries']['cachestat'].update(status='UNKNOWN',cache_index_bytes=None,stages={},error='mapping changed')
            bad['fixed_ram_device']['source_relationships'].update(status='CHANGED',before_complete=False,after_complete=False,
                mapping_diagnostic=dict(reason='EXACT_RANGE_MISSING'))
            d.update(status='PARTIAL',stable_cache_index_bytes=9*8192)
            d['method_totals']['cachestat'].update(status='PARTIAL',observed_objects=9,unknown_objects=1,
                observed_subtotal_bytes=9*8192,complete_bytes=None)
            d['fixed_ram_totals']=dict(status='OBSERVED',observed_objects=2,unknown_objects=0,
                observed_subtotal_bytes=16384,complete_bytes=16384,wall_seconds=0,cpu_seconds=0)
            value=self.collect_fixture(storage,metrics,base);text=m.report(value)
            self.assertIsNone(value['layers']['block_devices']['method_totals']['cachestat']['complete_bytes'])
            self.assertIn('新RAM设备口径状态OBSERVED',text)
            self.assertIn('CHANGED',text);self.assertIn('不是失败重试',text)
            d['fixed_ram_totals']['complete_bytes']=0
            with self.assertRaises(AssertionError):self.collect_fixture(storage,metrics,base)


if __name__=='__main__':unittest.main()
