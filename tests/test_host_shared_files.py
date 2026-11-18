"""Offline metadata, mocked queries and tiny private cgroup text fixtures only."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from host_scaling import classify_objects,file_reference,FILE_PROFILE
from host_file_monitor import HostFileMonitor
from host_prefix_compare import observe_prefix
from report_host_shared_repeats import stats,summarize


def row(inode=2,block=False,rdev=0):
    return dict(path='/metadata/only',device=1,inode=inode,rdev=rdev,block_device=block,
        mapping_length=8192,resident_bytes=4096,stable=True,start_monotonic_ns=1,end_monotonic_ns=2)


def observation(roles=('a',)):
    return dict(regular=dict(status='OBSERVED',files=[row(3),row(3),row(4)]),
        shared_readonly_drives=[dict(drive_id='rootfs',users=list(roles),residency=row(2,True,7),
            start_monotonic_ns=1,end_monotonic_ns=2)])


class FileFocusTests(unittest.TestCase):
    def test_objects_deduplicate_aliases_not_equal_contents_and_prepare_not_reference(self):
        first=classify_objects(observation(),'cold',['a'])
        self.assertEqual(len(first),3)
        self.assertEqual(sum(o['resident_bytes'] for o in first if o['category']=='prepared_input'),8192)
        last=classify_objects(observation(tuple('abcdefg')),'cold',list('abcdefg'))
        ref=file_reference(dict(sandboxes=1,objects=first),dict(sandboxes=7,objects=last))
        self.assertEqual(len(ref),1);self.assertEqual(ref[0]['category'],'shared_readonly_backing')
        self.assertEqual(ref[0]['reduction_bytes'],24576)
        last[-1]['resident_bytes']=32768
        self.assertEqual(file_reference(dict(sandboxes=1,objects=first),dict(sandboxes=7,objects=last))[0]['reduction_bytes'],-4096)

    def test_wrong_users_changed_objects_and_incomplete_query_are_not_zero(self):
        value=observation()
        with self.assertRaises(ValueError):classify_objects(value,'cold',['a','b'])
        value['regular']['status']='INCOMPLETE'
        with self.assertRaises(ValueError):classify_objects(value,'cold',['a'])
        a=classify_objects(observation(),'cold',['a']);b=classify_objects(observation(tuple('abcdefg')),'cold',list('abcdefg'))
        b[-1]['object_key']=['block',8]
        with self.assertRaises(ValueError):file_reference(dict(sandboxes=1,objects=a),dict(sandboxes=7,objects=b))

    def test_restore_categories_do_not_mix_snapshot_with_other_writable_devices(self):
        value=dict(status='OBSERVED',files=[dict(row(1,True,7),kind='memory-device'),
            dict(row(4),kind='raw-memory-layer'),dict(row(2,True,8),kind='other-owned-block-device'),
            dict(row(3,True,9),kind='other-owned-block-device')])
        drive=dict(drive_id='rootfs',device=1,inode=2,rdev=8,is_read_only=True)
        result=classify_objects(value,'restore',['a'],{'a':[drive]})
        self.assertEqual([o['category'] for o in result],['snapshot_memory_device','snapshot_memory_layer',
            'shared_readonly_backing','other_owned_backing'])

    def test_file_focus_prefix_queries_one_cache_object_for_two_drive_aliases_and_no_pss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);roots=root/'roots.json';roots.write_text('[]')
            drive=dict(drive_id='rootfs',device=1,inode=2,rdev=0,block_device=False,is_read_only=True,
                is_root_device=True,path='/metadata/only')
            vm=dict(pid=1)
            with patch('host_file_monitor.measurement_group',return_value=(root,'slice')),\
                 patch('host_file_monitor.counters',return_value={'file':1}),\
                 patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('no PSS')),\
                 patch('host_file_monitor.residency_check',return_value={'status':'OBSERVED'}),\
                 patch('host_sandbox_monitor.Binding') as binding,\
                 patch('host_prefix_compare.drives_for_vm',return_value=[drive,dict(drive,drive_id='alias')]),\
                 patch('host_file_monitor.mincore_file',return_value=row()) as query:
                binding.return_value.resolve.return_value=vm
                result=observe_prefix(SimpleNamespace(host_prefix_roots=roots,container='fake'),root,'test',{'a':'sid'},file_focus=True)
            query.assert_called_once()
            self.assertEqual(result['shared_readonly_drives'][0]['drive_ids'],['alias','rootfs'])

    def test_periodic_file_focus_skips_pss_and_legacy_default_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=root/'group';group.mkdir()
            (group/'memory.current').write_text('8192');(group/'memory.stat').write_text('file 4096\nanon 4096\nshmem 0\n')
            with patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('no PSS')) as pss:
                monitor=HostFileMonitor(root/'new',group,include_pss=False);receipt=monitor.stop()
                self.assertEqual(receipt['status'],'PASS');self.assertIsNone(receipt['pss_interval_seconds'])
                self.assertFalse((root/'new/pss.jsonl').exists());pss.assert_not_called()
            with patch('host_file_monitor.pss_snapshot',return_value={'status':'OBSERVED'}) as pss:
                monitor=HostFileMonitor(root/'legacy',group);receipt=monitor.stop()
                self.assertEqual(receipt['pss_interval_seconds'],1);self.assertTrue((root/'legacy/pss.jsonl').exists())
                self.assertGreaterEqual(pss.call_count,1)

    def test_repeats_compute_paired_differences_sample_sd_and_keep_missing_sd(self):
        def run(i):
            summary=dict(profile=FILE_PROFILE,mode='cold',actual_actions=77,
                shared_file_reference=[dict(category='shared_readonly_backing',single_bytes=i,multiple_bytes=2*i,
                    reference_bytes=7*i,reduction_bytes=5*i,reduction_fraction=5/7)],
                stages=[dict(baseline_delta={'file':i*n}) for n in (1,2,4,7)],
                **{k:i for k in ('tool_ns','task_elapsed_ns','file_residency_query_total_ns','intermediate_query_ns','endpoint_residency_query_ns')})
            return dict(workflow=str(i),summary=summary,file_task_peak_bytes=i*10,
                file_task_peak_above_baseline_bytes=i*3,collector_thread_cpu_ns=i)
        runs=[run(i) for i in range(1,6)];result=summarize(runs)
        self.assertEqual(result['status'],'PASS_FIVE_REPEATS');self.assertEqual(result['actual_actions'],385)
        self.assertEqual(result['shared_file_reference'][0]['reduction_bytes']['mean'],15)
        self.assertEqual(result['metrics']['file_task_peak_bytes']['mean'],30)
        self.assertEqual(result['file_stage_delta'][-1]['values'],[7,14,21,28,35])
        self.assertIsNone(stats([1])['sd']);self.assertIsNone(stats([])['mean'])
        self.assertEqual(summarize(runs[:1])['status'],'PARTIAL_REPEATS')
        with self.assertRaises(AssertionError):summarize([runs[0],runs[0]])

    def test_report_keeps_common_import_residency_without_multiplying_it(self):
        from copy import deepcopy
        from host_scaling import file_plot
        common=dict(category='snapshot_memory_layer',single_bytes=4096,multiple_bytes=4096,
            reference_bytes=28672,reduction_bytes=24576)
        summary=dict(shared_file_reference=[common],tool_ns=1,task_elapsed_ns=2,
            intermediate_query_ns=3,endpoint_residency_query_ns=4,
            stages=[dict(sandboxes=n,baseline_delta={'file':4096}) for n in (1,2,4,7)])
        original=deepcopy(summary)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);file_plot(summary,out)
            line=next(line for line in (out/'REPORT.md').read_text().splitlines() if line.startswith('| snapshot_memory_layer'))
            self.assertTrue(line.endswith('—（公共导入层） |'))
            self.assertIn('只列驻留，不乘七', (out/'REPORT.md').read_text())
            self.assertNotIn('N x single', (out/'scaling.svg').read_text())
        self.assertEqual(summary,original)

    def test_repeat_entry_has_five_fixed_attempts_and_stops_on_first_failure(self):
        import run_host_shared_repeats as runner
        for failed in (None,2):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out=root/'.artifacts/repeats';calls=[]
                def command(argv,**kw):
                    if argv[0]=='bash':
                        calls.append(argv);i=len(calls)
                        kw['stdout'].write('status=PASS result_dir='+str(root/'.artifacts/host-scaling'/str(i))+'\n')
                        return SimpleNamespace(returncode=1 if i==failed else 0)
                    return SimpleNamespace(returncode=0)
                def collect_fake(path):return dict(workflow=str(path),summary=dict(baseline={'group':str(path)}))
                source=dict(Id=runner.SOURCE_ID,State={'Running':False},HostConfig={'RestartPolicy':{'Name':'no'}})
                with patch.object(runner,'ROOT',root),patch.object(sys,'argv',['entry','--output',str(out)]),\
                     patch.object(runner,'configuration',return_value={}),patch.object(runner,'preflight',return_value={}),\
                     patch.object(runner,'available',return_value=100*2**30),patch.object(runner,'source_state',return_value=source),\
                     patch.object(runner.shutil,'copy2'),patch.object(runner.subprocess,'run',side_effect=command),\
                     patch.object(runner.urllib.request,'build_opener') as opener,\
                     patch.object(runner,'collect',side_effect=collect_fake),patch.object(runner,'report',return_value={'status':'PASS_FIVE_REPEATS'}):
                    opener.return_value.open.return_value.__enter__.return_value.status=200
                    self.assertEqual(runner.main(),0 if failed is None else 1)
                result=json.loads((out/'result.json').read_text())
                self.assertEqual(len(calls),5 if failed is None else 2)
                self.assertEqual(len(result['runs']),5 if failed is None else 1)
                self.assertTrue(result['source_returned_to_stopped'])
                self.assertTrue(all(argv[-2:]==['--scaling','cold'] for argv in calls))


if __name__=='__main__':unittest.main()
