"""Temporary files only: no Docker, real cgroup writes, VM, mount or workload."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import experiment_memory as m
from host_file_monitor import HostFileMonitor


class ExperimentMemoryTests(unittest.TestCase):
    def values(self, file):return dict(current=file+10, file=file, anon=7, kernel=3)

    def group(self, path, file):
        path.mkdir(parents=True, exist_ok=True)
        (path/'memory.current').write_text(str(file+10))
        (path/'memory.stat').write_text(f'file {file}\nanon 7\nkernel 3\nshmem 0\n')
        return path

    def test_only_direct_children_are_summed_and_signed_anomalies_are_retained(self):
        root=Path('/fixture/asbhost-one.slice')
        groups={str(root):self.values(30), str(root/'control.service'):self.values(20),
                str(root/'docker.scope'):self.values(10), str(root/'docker.scope/nested'):self.values(10)}
        row=m.normalize_groups(root, groups)
        self.assertEqual(row['direct_children_sum']['file'],30)
        self.assertEqual(len(row['direct_children']),2)
        self.assertEqual(row['file_closure']['status'],'WITHIN_THRESHOLD')
        for delta in (m.FILE_CLOSURE_THRESHOLD_BYTES, m.FILE_CLOSURE_THRESHOLD_BYTES+1, -m.FILE_CLOSURE_THRESHOLD_BYTES-1):
            groups[str(root)]['file']=2*m.FILE_CLOSURE_THRESHOLD_BYTES+delta
            groups[str(root/'control.service')]['file']=2*m.FILE_CLOSURE_THRESHOLD_BYTES-10
            row=m.normalize_groups(root,groups)
            self.assertEqual(row['file_closure']['parent_minus_children_bytes'],delta)
            self.assertEqual(row['file_closure']['status'],'WITHIN_THRESHOLD' if abs(delta)<=m.FILE_CLOSURE_THRESHOLD_BYTES else 'MISMATCH')

    def test_missing_fields_and_child_churn_never_pass(self):
        groups={'/parent':self.values(30),'/parent/one':self.values(30)}
        for kwargs in (dict(inventory_before=['/parent/one','/parent/gone']),dict(inventory_after=[])):
            self.assertEqual(m.normalize_groups('/parent',groups,**kwargs)['file_closure']['status'],'INCOMPLETE')
        missing=m.normalize_groups('/parent',groups,inventory_after=['/parent/one','/parent/new'])
        self.assertIsNone(missing['direct_children']['/parent/new']['file'])
        self.assertIsNone(missing['direct_children_sum']['file'])
        del groups['/parent/one']['file']
        value=m.normalize_groups('/parent',groups)
        self.assertIsNone(value['direct_children_sum']['file'])
        self.assertIsNone(value['file_closure']['parent_minus_children_bytes'])
        self.assertEqual(value['status'],'INCOMPLETE')

    def test_scope_requires_explicit_parent_controller_and_service_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cg=base/'cg';parent=self.group(cg/'asbhost.slice/asbhost-test.slice',0)
            proc=base/'proc';(proc/'self').mkdir(parents=True)
            (proc/'self/cgroup').write_text('0::/asbhost.slice/asbhost-test.slice/controller.service\n')
            args=dict(proc_root=proc,cgroup_root=cg)
            service=parent/'docker-example.scope'
            self.assertEqual(m.resolve_scope(service,environ={},**args)['status'],'UNAVAILABLE')
            env={'ASB_HOST_FILE_SLICE':'asbhost-test.slice'}
            good=m.resolve_scope(service,environ=env,**args)
            self.assertEqual(good['parent'],str(parent));self.assertEqual(good['status'],'AVAILABLE')
            self.assertEqual(m.resolve_scope(cg/'system.slice/docker.scope',environ=env,**args)['status'],'UNAVAILABLE')
            for unit in ('system.slice','user.slice','/','asbhost-unknown.slice'):
                self.assertEqual(m.resolve_scope(service,environ={'ASB_HOST_FILE_SLICE':unit},**args)['status'],'UNAVAILABLE')
            (proc/'self/cgroup').write_text('0::/user.slice/session.scope\n')
            self.assertEqual(m.resolve_scope(service,environ=env,**args)['status'],'UNAVAILABLE')

    def test_vmm_parent_explicit_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cg=base/'cg';parent=self.group(cg/'asbvmmtest.slice',0)
            proc=base/'proc';(proc/'self').mkdir(parents=True)
            (proc/'self/cgroup').write_text('0::/asbvmmtest.slice/controller.service\n')
            context=m.resolve_scope(parent/'docker-test.scope',environ={'ASB_VMM_CGROUP_PARENT':str(parent)},proc_root=proc,cgroup_root=cg)
            self.assertEqual(context['status'],'AVAILABLE')
            self.assertEqual(context['controller_cgroup'],str(parent/'controller.service'))

    def test_live_fixture_uses_same_parser_as_replay_and_reports_missing_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.group(Path(tmp)/'parent',30)
            self.group(root/'controller.service',20);self.group(root/'docker.scope',10)
            row=m.sample_groups(root)
            self.assertEqual(row['experiment_memory'],m.normalize_groups(root,row['groups']))
            (root/'docker.scope/memory.stat').unlink()
            row=m.sample_groups(root)
            self.assertEqual(m.observation_status(row),'INCOMPLETE')
            self.assertTrue(row['experiment_memory']['errors'])

    def test_optin_cgroup_stat_is_separate_and_missing_diagnostics_do_not_fill_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.group(Path(tmp)/'parent',4*2**20)
            self.group(root/'control.service',2*2**20)
            self.assertNotIn('cgroup_diagnostics',m.sample_groups(root))
            missing=m.sample_groups(root,diagnostics=True)
            self.assertEqual(missing['cgroup_diagnostics']['status'],'PARTIAL')
            self.assertIsNone(missing['cgroup_diagnostics']['parent_stat_before']['counters'])
            (root/'cgroup.stat').write_text('nr_descendants 1\nnr_dying_descendants 1\n')
            found=m.sample_groups(root,diagnostics=True)
            self.assertEqual(found['cgroup_diagnostics']['parent_stat_after']['counters']['nr_dying_descendants'],1)
            self.assertEqual(found['experiment_memory'],missing['experiment_memory'])
            self.assertEqual(m.observation_status(found),'MISMATCH')
            self.assertEqual(found['experiment_memory']['file_closure']['parent_minus_children_bytes'],2*2**20)

    def test_diagnostic_read_windows_preserve_stat_then_current_and_parent_then_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.group(Path(tmp)/'parent',30);self.group(root/'child',30)
            plain=m.sample_groups(root);timed=m.sample_groups(root,diagnostics=True)
            reads=timed['cgroup_diagnostics']['counter_reads']
            self.assertEqual([r['path'] for r in reads],[str(p/name) for p in (root,root/'child') for name in ('memory.stat','memory.current')])
            self.assertEqual(timed['experiment_memory'],plain['experiment_memory'])
            self.assertTrue(all(r['status']=='READ' and r['start_monotonic_ns']<=r['end_monotonic_ns'] for r in reads))
            self.assertTrue(all(a['end_monotonic_ns']<=b['start_monotonic_ns'] for a,b in zip(reads,reads[1:])))
            self.assertNotIn('cgroup_diagnostics',plain)

    def test_failed_child_read_retains_its_window_and_does_not_fill_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.group(Path(tmp)/'parent',30);child=self.group(root/'child',30)
            (child/'memory.stat').unlink()
            row=m.sample_groups(root,diagnostics=True)
            failed=row['cgroup_diagnostics']['counter_reads'][-1]
            self.assertEqual(failed['path'],str(child/'memory.stat'));self.assertEqual(failed['status'],'FAIL')
            self.assertGreaterEqual(failed['end_monotonic_ns'],failed['start_monotonic_ns'])
            self.assertEqual(row['experiment_memory']['status'],'INCOMPLETE')
            self.assertIsNone(row['experiment_memory']['direct_children_sum']['file'])

    def test_collector_records_child_disappearance_without_reusing_its_last_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.group(Path(tmp)/'parent',4*2**20);child=self.group(root/'docker.scope',4*2**20)
            (root/'cgroup.stat').write_text('nr_descendants 1\nnr_dying_descendants 0\n')
            class TwoPoints:
                n=0
                def is_set(self):return self.n>=2
                def wait(self,seconds):
                    self.n+=1
                    if self.n==1:
                        for f in child.iterdir():f.unlink()
                        child.rmdir()
                        (root/'cgroup.stat').write_text('nr_descendants 0\nnr_dying_descendants 1\n')
            collector=object.__new__(m.GroupSampler)
            collector.root=root;collector.out=Path(tmp)/'out';collector.out.mkdir()
            collector.stop_event=TwoPoints();collector.error=None;collector.count=0;collector.status_counts={};collector.diagnostics=True
            collector.collect()
            self.assertIsNone(collector.error)
            rows=[json.loads(s) for s in (collector.out/'memory.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['cgroup_diagnostics']['membership_since_previous']['removed'],[str(child)])
            self.assertEqual(rows[1]['experiment_memory']['direct_children'],{})
            self.assertEqual(rows[1]['experiment_memory']['file_closure']['parent_minus_children_bytes'],4*2**20)
            self.assertEqual(rows[1]['experiment_memory']['file_closure']['status'],'MISMATCH')

    def test_monitor_preserves_legacy_values_and_separates_new_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);parent=self.group(base/'parent',30)
            self.group(parent/'controller.service',20);service=self.group(parent/'docker.scope',10)
            context=dict(status='AVAILABLE',parent=str(parent),host_scope=m.HOST_SCOPE)
            collector=HostFileMonitor(base/'out',service,include_pss=False,experiment_context=context)
            receipt=collector.stop()
            self.assertEqual(receipt['cgroup'],str(service))
            self.assertEqual(receipt['host_scope'],m.HOST_SCOPE)
            import csv
            with (base/'out/memory.tsv').open() as stream:legacy=list(csv.DictReader(stream,delimiter='\t'))
            self.assertEqual(int(legacy[0]['file']),10)
            row=json.loads((base/'out/experiment-memory.jsonl').read_text().splitlines()[0])
            self.assertEqual(row['experiment_memory']['parent_counters']['file'],30)
            self.assertEqual(receipt['experiment_memory_status'],'OBSERVED')

    def test_no_parent_produces_no_false_totals_and_mismatch_is_in_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);parent=self.group(base/'parent',2*m.FILE_CLOSURE_THRESHOLD_BYTES)
            service=self.group(parent/'docker.scope',10)
            context=dict(status='UNAVAILABLE',parent=None,reason='no dedicated parent')
            self.assertIsNone(m.snapshot(context)['experiment_memory']['parent_counters'])
            collector=HostFileMonitor(base/'old',service,include_pss=False,experiment_context=context)
            self.assertEqual(collector.stop()['experiment_memory_status'],'UNAVAILABLE')
            self.assertEqual((base/'old/experiment-memory.jsonl').read_text(),'')
            collector=HostFileMonitor(base/'new',service,include_pss=False,experiment_context=dict(status='AVAILABLE',parent=str(parent)))
            receipt=collector.stop()
            self.assertEqual(receipt['experiment_memory_status'],'ANOMALY')
            self.assertGreater(receipt['experiment_observation_status_counts']['MISMATCH'],0)

    def test_endpoint_wires_new_parent_without_relabeling_old_p1(self):
        from contextlib import ExitStack
        import schedule_smoke_metrics as endpoint
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            base=Path(tmp);parent=self.group(base/'parent',30)
            self.group(parent/'controller.service',20);service=self.group(parent/'docker.scope',10)
            out=base/'run/mount/controller/task';out.mkdir(parents=True)
            binding=Mock();binding.cgroup=service;binding.private_service_cgroup=service
            binding.resolve.return_value={'pid':123}
            context=dict(status='AVAILABLE',parent=str(parent))
            for name,result in [('Binding',binding),('resolve_scope',context),
                    ('drives_for_vm',[]),('check_cache',{'local_files':[]}),('opened_regular_files',[]),
                    ('rootfs_inventory',{}),('select_initial',[]),('pss_snapshot',{}),('validate_rootfs_inventory',None)]:
                stack.enter_context(patch.object(endpoint,name,return_value=result))
            value=endpoint.observe('fixture',out,{'backbone':'sid'},0)
            self.assertEqual(value['status'],'OBSERVED')
            self.assertEqual(value['host_scope'],m.HOST_SCOPE)
            self.assertEqual(value['legacy_host_scope'],'private_service_cgroup')
            self.assertEqual(value['p1']['counters_before']['file'],10)
            self.assertEqual(value['experiment_memory_p1']['experiment_memory']['parent_counters']['file'],30)
            # Special Binding historically reads the parent already. Even if
            # the new controller membership check fails, do not relabel it.
            binding.cgroup=parent
            context.update(status='UNAVAILABLE',parent=None,reason='controller outside declared parent')
            special=endpoint.observe('fixture',out,{'backbone':'sid'},0)
            self.assertEqual(special['legacy_host_scope'],'experiment_slice_cgroup')
            self.assertEqual(special['p1']['counters_before']['file'],30)
            self.assertIsNone(special['experiment_memory_p1']['experiment_memory']['parent_counters'])


if __name__=='__main__':unittest.main()
