"""Saved/synthetic JSON and temporary report files only; no service or device calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_memory_detail_report import memory_detail, write_memory_report
from run_host_independent_pair import main, report


class MemoryReportTests(unittest.TestCase):
    def owner(self,status='OBSERVED'):
        valid=status in ('OBSERVED','INCOMPLETE')
        return dict(status=status,path='/old/repository.tar.gz',reference_group='/sys/fs/cgroup/own',reference_group_ids=[22],
            inside_bytes=0 if valid else None,outside_bytes=4096 if valid else None,
            unknown_bytes=0 if valid else None,resident_bytes=4096 if valid else None,
            start_monotonic_ns=1300,end_monotonic_ns=1500,
            observation=dict(query_start_monotonic_ns=1350,query_end_monotonic_ns=1450,query_ns=100) if valid else None,
            worker_reaped=True,worker_group_released=True)

    def residency(self):
        regions=[dict(smaps_status='OBSERVED',smaps_bytes=dict(Pss=2048,Rss=4096,Anonymous=0,Shared_Hugetlb=0,Private_Hugetlb=0)) for _ in range(2)]
        mapped=dict(start_monotonic_ns=300,end_monotonic_ns=700,
            unresolved=[dict(mapping_path='/memfd:iov_deque (deleted)',mappings=2,regions=regions,deleted_descriptors=[])],
            special_objects=[dict(command_buffer=dict(status='SOURCE_CALCULATED',allocation_bytes=4096,start_monotonic_ns=400,end_monotonic_ns=500))],
            additional_files=[{}],additional_global_resident_bytes=8192,coverage='INCOMPLETE',
            special_metadata_status='INCOMPLETE',special_metadata_errors=[],ublkc_command_allocation_bytes=4096)
        return dict(end_monotonic_ns=1200,compact_inventory=dict(status='OBSERVED',start_monotonic_ns=100,end_monotonic_ns=1000,
            before_pss=dict(pss_anon=8192),after_pss=dict(pss_anon=12288),mapped_backing=mapped))

    def summary(self,detail):
        group=dict(sandboxes=1,memory_device_bytes=16384,final_net_shortfall_bytes=4096,
            final_accounting='SHORTFALL_REQUIRES_RESOLUTION',tool_ns=1000000,task_wall_ns=2000000,
            official_results=[dict(role='N1-a',passed=0,failed=2,status='FAIL')])
        if detail is not None:group['memory_detail']=detail
        return dict(status='PASS_INDEPENDENT_WRITABLE_PAIR',actual_actions=41,new_actions=30,
            groups=[group],whole_host_physical_savings='NOT_MEASURED',guest_cache_physical_attribution='NOT_MEASURED')

    def test_mapping_pss_and_command_allocation_separate_no_mutation(self):
        raw=self.residency();before=copy.deepcopy(raw);d=memory_detail(raw)
        self.assertEqual(raw,before)
        family=d['special_mapping_families'][0]
        self.assertEqual(family['metadata_keys'],1);self.assertEqual(family['mapping_lines'],2)
        self.assertEqual(family['mapping_pss_rss_bytes']['Pss'],4096)
        self.assertIsNone(family['backing_resident_bytes'])
        self.assertEqual(d['ublkc_command_allocation_bytes'],4096)
        self.assertEqual(d['deleted_objects_without_descriptor'],1)
        self.assertEqual(d['special_metadata_status'],'INCOMPLETE')
        self.assertEqual(d['timing_ns'],dict(mapped_metadata_and_file_query=400,ublkc_queries=100,all_post_task_queries=1100))
        self.assertNotIn('physical_total_bytes',d)

    def test_legacy_detail_keeps_missing_fields_unknown_not_zero(self):
        raw=self.residency();m=raw['compact_inventory']['mapped_backing']
        for entry in m['unresolved']:
            entry.pop('regions');entry.pop('deleted_descriptors')
        for k in ('special_metadata_status','special_metadata_errors','ublkc_command_allocation_bytes'):m.pop(k)
        m['special_objects']=[]
        d=memory_detail(raw);f=d['special_mapping_families'][0]
        self.assertIsNone(f['mapping_pss_rss_bytes']['Pss']);self.assertEqual(f['smaps_status'],'NOT_MEASURED')
        self.assertIsNone(d['deleted_objects_without_descriptor']);self.assertIsNone(d['ublkc_command_allocation_bytes'])
        self.assertIsNone(d['timing_ns']['ublkc_queries']);self.assertEqual(d['special_metadata_status'],'NOT_MEASURED')
        self.assertEqual(d['timing_ns']['mapped_metadata_and_file_query'],400)
        self.assertIsNone(memory_detail({}))
        self.assertEqual(d['deleted_backing_cache']['status'],'NOT_MEASURED')
        self.assertIsNone(d['deleted_backing_cache']['whole_backing_cache_bytes'])

    def test_deleted_cache_is_a_separate_observation_and_timeout_stays_unknown(self):
        raw=self.residency();m=raw['compact_inventory']['mapped_backing']
        m['deleted_backing_cache']=dict(status='OBSERVED',objects=[dict(device=1,inode=20)],
            whole_backing_cache_bytes=8192,start_monotonic_ns=800,end_monotonic_ns=900,worker_reaped=True)
        d=memory_detail(raw)
        self.assertEqual(d['deleted_backing_cache'],dict(status='OBSERVED',object_count=1,
            whole_backing_cache_bytes=8192,query_ns=100,worker_reaped=True))
        self.assertEqual(d['special_metadata_status'],'INCOMPLETE')
        self.assertIsNone(d['special_mapping_families'][0]['backing_resident_bytes'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.md';write_memory_report(path,self.summary(d))
            self.assertIn('整后备缓存观察合计／KiB | 8.000',path.read_text())
            self.assertIn('单列，不与前面相加',path.read_text())
            m['deleted_backing_cache'].update(status='TIMEOUT',objects=[],whole_backing_cache_bytes=None)
            d=memory_detail(raw);write_memory_report(path,self.summary(d))
            self.assertIn('查询状态 | TIMEOUT',path.read_text())
            self.assertIn('整后备缓存观察合计／KiB | 未测',path.read_text())
            self.assertEqual(d['deleted_backing_cache']['status'],'TIMEOUT')

    def test_partial_smaps_and_failed_query_do_not_turn_into_partial_byte_totals(self):
        raw=self.residency();m=raw['compact_inventory']['mapped_backing']
        m['unresolved'][0]['regions'][0]['smaps_status']='PID_CHANGED'
        m['special_objects'][0]['command_buffer']=dict(status='UNKNOWN',allocation_bytes=None)
        m['ublkc_command_allocation_bytes']=None
        d=memory_detail(raw)
        self.assertEqual(d['special_mapping_families'][0]['smaps_status'],'INCOMPLETE')
        self.assertIsNone(d['special_mapping_families'][0]['mapping_pss_rss_bytes']['Pss'])
        self.assertIsNone(d['ublkc_command_allocation_bytes']);self.assertIsNone(d['timing_ns']['ublkc_queries'])
        self.assertEqual(d['ublkc_query_success_count'],0)
        m['special_metadata_errors']=[dict(stage='deleted-descriptors',error='denied')]
        self.assertIsNone(memory_detail(raw)['deleted_objects_without_descriptor'])

    def test_saved_allocation_mismatch_or_backwards_time_rejected(self):
        raw=self.residency();raw['compact_inventory']['mapped_backing']['ublkc_command_allocation_bytes']=8192
        with self.assertRaises(ValueError):memory_detail(raw)
        raw=self.residency();raw['compact_inventory']['mapped_backing']['end_monotonic_ns']=200
        with self.assertRaises(ValueError):memory_detail(raw)

    def test_old_owner_is_later_separate_window_and_original_values_stay(self):
        raw=self.residency();before=copy.deepcopy(raw);old=memory_detail(raw)
        value=memory_detail(raw,existing_cache_owner=self.owner())
        self.assertEqual(raw,before)
        for key in old:
            if key not in ('existing_cache_owner','timing_ns'):self.assertEqual(value[key],old[key])
        for key in old['timing_ns']:self.assertEqual(value['timing_ns'][key],old['timing_ns'][key])
        self.assertEqual(value['timing_ns']['including_existing_cache_owner'],1400)
        self.assertEqual(value['existing_cache_owner']['outside_bytes'],4096)
        self.assertEqual(old['existing_cache_owner']['status'],'NOT_MEASURED')
        self.assertIsNone(old['existing_cache_owner']['unknown_bytes'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.md';write_memory_report(path,self.summary(value))
            text=path.read_text();self.assertIn('组外缓存／MiB | 0.004',text)
            self.assertIn('参考统计组 | /sys/fs/cgroup/own',text)
            self.assertIn('查询开始／host单调时钟ns | 1350',text)
            self.assertIn('原文件／设备驻留查询段',text)
            self.assertIn('0通过／2失败（FAIL）',text)

    def test_old_owner_failure_and_partial_keep_unknown_and_reject_wrong_sum(self):
        for status in ('ERROR','TIMEOUT','NOT_REQUESTED'):
            d=memory_detail(self.residency(),existing_cache_owner=self.owner(status))
            self.assertEqual(d['existing_cache_owner']['status'],status)
            self.assertIsNone(d['existing_cache_owner']['outside_bytes'])
        value=self.owner('INCOMPLETE');value.update(outside_bytes=0,unknown_bytes=4096)
        self.assertEqual(memory_detail(self.residency(),existing_cache_owner=value)['existing_cache_owner']['unknown_bytes'],4096)
        for change in (dict(inside_bytes=8192),dict(start_monotonic_ns=1100)):
            value=self.owner();value.update(change)
            with self.assertRaises(ValueError):memory_detail(self.residency(),existing_cache_owner=value)

    def test_other_mapping_preserved_and_real_zero_pss_not_backing_zero(self):
        raw=self.residency();entry=raw['compact_inventory']['mapped_backing']['unresolved'][0]
        entry['mapping_path']='anon_inode:other'
        for r in entry['regions']:r['smaps_bytes']['Pss']=0
        d=memory_detail(raw);f=d['special_mapping_families'][0]
        self.assertEqual(f['family'],'other');self.assertEqual(f['mapping_pss_rss_bytes']['Pss'],0)
        self.assertIsNone(f['backing_resident_bytes'])

    def test_markdown_lists_original_failure_unknowns_scope_and_saved_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.md';s=self.summary(memory_detail(self.residency()))
            write_memory_report(path,s);text=path.read_text()
            self.assertIn('0通过／2失败（FAIL）',text);self.assertIn('源码计算',text)
            self.assertIn('已删除memfd映射PSS／KiB | 4.000',text)
            self.assertIn('整体host物理节省：NOT_MEASURED',text)
            self.assertIn('不相加',text);self.assertIn('INCOMPLETE',text)
            self.assertIn('编译准备：未测',text)
            write_memory_report(path,self.summary(None));text=path.read_text()
            self.assertIn('本轮未采逐映射PSS',text)
            self.assertIn('命令缓冲分配／KiB（源码计算） | 未测',text)

    def test_offline_cli_never_runs_workflow_and_old_run_arguments_still_work(self):
        with patch('run_host_independent_pair.report',return_value={}) as generate,patch('run_host_independent_pair.workflow',return_value=7) as run:
            self.assertEqual(main(['--report-only','/saved','--output','/new-report']),0)
            generate.assert_called_once_with(Path('/saved'),Path('/new-report'));run.assert_not_called()
            self.assertEqual(main(['/config','--resume-prefix','/prefix']),7)
            run.assert_called_once_with(Path('/config'),Path('/prefix'))
        for argv in ([],['--report-only','/saved'],['/config','--report-only','/saved','--output','/new'],['/config','--output','/new']):
            with self.subTest(argv=argv),patch('run_host_independent_pair.workflow') as run,patch('run_host_independent_pair.report') as generate:
                with self.assertRaises(SystemExit) as error:main(argv)
                self.assertEqual(error.exception.code,2);run.assert_not_called();generate.assert_not_called()

    def test_offline_output_cannot_overwrite_workflow_raw_inputs_or_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';raw=root/'raw';source.mkdir();raw.mkdir()
            value=json.dumps(dict(status='PASS',phases=[dict(storage=str(raw))]))
            (source/'result.json').write_text(value);existing=root/'existing';existing.mkdir();(existing/'keep').write_text('keep')
            for output in (source,source/'nested',raw/'nested'):
                with self.subTest(output=output),self.assertRaises(ValueError):report(source,output)
            with self.assertRaises(FileExistsError):report(source,existing)
            self.assertEqual((existing/'keep').read_text(),'keep');self.assertEqual((source/'result.json').read_text(),value)
            self.assertFalse((raw/'nested').exists())


if __name__=='__main__':unittest.main()
