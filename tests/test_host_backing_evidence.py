"""Saved dictionaries, fake collectors and temporary files; no live service/device I/O."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_evidence as m
import run_live_child_calibration as entry


class HostBackingEvidenceTests(unittest.TestCase):
    def raw(self):
        regular=dict(device=1,inode=5,status='OBSERVED',resident_bytes=4096,
            categories=['active_snapshot_rootfs_lower'],users=['a','b'],aliases=[],
            start_monotonic_ns=10,end_monotonic_ns=20,
            residency=dict(device=1,inode=5,stable=True,resident_bytes=4096,
                mincore_permission=dict(basis='HOST_CAP_FOWNER')))
        device=dict(rdev=33,status='OBSERVED',aliases=[dict(role='a',process=11)],
            queries=dict(cachestat=dict(status='OBSERVED',cache_index_bytes=8192,
                start_monotonic_ns=20,end_monotonic_ns=30,cache_counters=dict(nr_cache=2))))
        return dict(status='COLLECTED',guests=dict(a='sid-a',b='sid-b'),
            bindings={r:dict(pid=pid,start_ticks='3',boot_id='host-boot') for r,pid in [('a',11),('b',12)]},
            rootfs_inventory=dict(status='IDENTIFIED',guests={r:dict(lowers=[dict(status='IDENTIFIED')]) for r in ('a','b')}),
            expanded_files=dict(status='OBSERVED',files=[regular],unresolved=[],process_identities={}),
            cache_layers=dict(status='OBSERVED',block_devices=dict(objects=[device]),process_errors=[],
                deleted_tmpfs=dict(status='NOT_REQUESTED',whole_backing_cache_bytes=None)))

    def test_shared_reference_not_divided_or_assigned_as_page_owner(self):
        result=m.summarize(self.raw());obj=result['objects'][0]
        self.assertEqual(obj['references'],[dict(role='a',sandbox_id='sid-a'),dict(role='b',sandbox_id='sid-b')])
        self.assertEqual(obj['query']['bytes'],4096)
        self.assertEqual(result['coverage']['multiple_reference_objects'],1)
        self.assertIsNone(result['per_guest']['a']['exclusive_physical_bytes'])
        self.assertIsNone(result['complete_sandbox_host_memory_bytes'])
        self.assertEqual(result['status'],'OBSERVED_LISTED_OBJECTS')

    def test_service_only_objects_remain_unassigned_and_pid_alias_binds_only_its_guest(self):
        raw=self.raw();row=raw['expanded_files']['files'][0];row['users']=[]
        row['aliases']=[dict(process=99,path='/service-only')]
        v=m.summarize(raw);self.assertEqual(v['objects'][0]['reference_status'],'UNASSIGNED')
        row['aliases'].append(dict(process=12,path='/vmm-b'))
        self.assertEqual(m.summarize(raw)['objects'][0]['references'],[dict(role='b',sandbox_id='sid-b')])

    def test_permission_identity_and_change_failures_preserve_unknown(self):
        for mode in ('permission','inode','change','negative'):
            with self.subTest(mode=mode):
                raw=self.raw();row=raw['expanded_files']['files'][0]
                if mode=='permission':row['residency'].pop('mincore_permission')
                elif mode=='inode':row['residency']['inode']=999
                elif mode=='negative':row['resident_bytes']=row['residency']['resident_bytes']=-1
                else:row['status']='CHANGED_DURING_QUERY'
                v=m.summarize(raw);self.assertEqual(v['status'],'PARTIAL');self.assertIsNone(v['objects'][0]['query']['bytes'])

    def test_cachestat_failure_keeps_raw_counters_no_mincore_fallback(self):
        raw=self.raw();row=raw['cache_layers']['block_devices']['objects'][0]
        row.update(status='UNKNOWN',resident_bytes=999999)
        row['queries']['cachestat'].update(status='UNKNOWN',cache_index_bytes=None,error='ENOSYS')
        obj=m.summarize(raw)['objects'][1]
        self.assertIsNone(obj['query']['bytes']);self.assertEqual(obj['query']['cache_counters'],dict(nr_cache=2))

    def test_outer_loop_is_containment_not_all_pages_owned_by_both_guests(self):
        raw=self.raw();q=copy.deepcopy(raw['expanded_files']['files'][0]['residency'])
        raw['cache_layers']['outer_loop']=dict(status='OBSERVED',resident_bytes=4096,residency=q,image='/volume')
        obj=m.summarize(raw)['objects'][-1]
        self.assertEqual(obj['reference_status'],'CONTAINER_ONLY');self.assertEqual(obj['references'],[])
        self.assertEqual(len(obj['contains_sandboxes']),2);self.assertEqual(obj['query']['bytes'],4096)

    def test_unbound_roles_duplicate_pids_and_duplicate_objects_rejected(self):
        for mode in ('role','pid','object'):
            raw=self.raw()
            if mode=='role':raw['expanded_files']['files'][0]['users']=['other']
            elif mode=='pid':raw['bindings']['b']['pid']=11
            else:raw['expanded_files']['files']*=2
            with self.subTest(mode=mode),self.assertRaises(ValueError):m.summarize(raw)

    def test_tmpfs_query_joins_same_inode_but_swap_or_other_inode_stays_unknown(self):
        for mode in ('observed','swap','other_inode'):
            raw=self.raw();row=raw['expanded_files']['files'][0];row['status']='SEPARATE_SHMEM_OR_HUGETLB'
            raw['cache_layers']['deleted_tmpfs']=dict(status='OBSERVED',objects=[dict(device=1,
                inode=6 if mode=='other_inode' else 5,status='PARTIAL_SWAP_ENTRIES' if mode=='swap' else 'OBSERVED',
                whole_backing_cache_bytes=None if mode=='swap' else 8192,cache_counters=dict(nr_cache=2,nr_evicted=mode=='swap'))])
            q=m.summarize(raw)['objects'][0]['query']
            self.assertEqual(q['method'],'deleted_tmpfs_cachestat')
            self.assertEqual(q['bytes'],8192 if mode=='observed' else None)

    def test_missing_collections_are_not_observed_empty_zero(self):
        raw=self.raw();raw.pop('expanded_files');raw.pop('cache_layers')
        v=m.summarize(raw);self.assertEqual(v['status'],'PARTIAL')
        self.assertEqual(v['source_statuses']['expanded_files'],'NOT_COLLECTED')
        self.assertIsNone(v['complete_sandbox_host_cache_bytes'])

    def test_saved_schedule_replay_uses_bound_pss_identities_without_live_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=self.raw();guests=raw.pop('guests')
            raw['pss']=dict(processes=[dict(role=r,sandbox_id=sid,identity=raw['bindings'][r]) for r,sid in guests.items()])
            p=Path(tmp)/'raw.json';p.write_text(json.dumps(raw))
            self.assertEqual(m.replay(p)['guests'],guests)
            raw['pss']['processes'][0]['identity']=dict(pid=999);p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'binding conflict'):m.replay(p)

    def test_inside_option_parses_without_starting_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kernel=root/'.artifacts/kernel';kernel.parent.mkdir();kernel.write_bytes(b'fake')
            out=root/'.artifacts/out'
            argv=['entry','--inside','--unit','fake','--output',str(out),'--kernel',str(kernel),'--guests','2','--host-backing-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0)
            inside.assert_called_once_with(out,'fake',2,kernel=kernel,dynamic_file=False,ram_page_evidence=False,host_backing_evidence=True)

    def test_old_private_run_missing_inventory_is_unavailable_and_fail_stays_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'old.json';p.write_text(json.dumps(dict(status='PASS',guests=dict(a=dict(sandbox_id='a')))))
            v=m.replay(p);self.assertEqual(v['status'],'UNAVAILABLE');self.assertIsNone(v['complete_sandbox_host_cache_bytes'])
            p.write_text(json.dumps(dict(status='FAIL',error='original timeout')))
            v=m.replay(p);self.assertEqual(v['status'],'FAIL');self.assertEqual(v['error'],'original timeout')

    def observe_fake(self,out,*,fail=False,bad_identity=False,full=False,fail_coverage=False,charge_scope=None):
        raw=self.raw();guests=raw['guests'];vms=raw['bindings']
        if full:
            guests.pop('b');vms.pop('b');raw['rootfs_inventory']['guests'].pop('b')
            raw['expanded_files']['files'][0]['users']=['a']
        expected={r:dict(pid=vm['pid'],start_time_ticks=3,sandbox_id=guests[r],cgroup='/owned/vmm-'+r) for r,vm in vms.items()}
        if bad_identity:expected['a']['pid']=999
        binding=SimpleNamespace(cgroup=Path('/fake-slice'),resolve=lambda sid:vms[next(r for r in guests if guests[r]==sid)])
        def identity(root):
            r=next(r for r,vm in vms.items() if str(vm['pid'])==root.name)
            return dict(start_ticks='3',cgroup='0::/owned/vmm-'+r+'\n')
        with ExitStack() as s:
            if full:
                def batch(*args,**kwargs):
                    self.assertEqual(kwargs,{'charge_scope':charge_scope} if charge_scope is not None else {})
                    directory=out/'host-backing-full-pfn';directory.mkdir()
                    if fail_coverage:(directory/'coverage.json').write_text('existing')
                    return dict(status='PASS')
                s.enter_context(patch('host_backing_pfn_batch.observe_batch',side_effect=batch))
                s.enter_context(patch('host_backing_pfn_batch.replay_batch',return_value=(dict(fixture='evidence'),dict(fixture='coverage'))))
            s.enter_context(patch('host_sandbox_monitor.Binding',return_value=binding))
            s.enter_context(patch('host_prefix_compare.drives_for_vm',return_value=[]))
            s.enter_context(patch('host_rollout_shared.opened_regular_files',return_value={}))
            s.enter_context(patch('host_rootfs_layers.rootfs_inventory',return_value=raw['rootfs_inventory']))
            s.enter_context(patch('host_rootfs_layers.validate_rootfs_inventory'))
            s.enter_context(patch('host_schedule_files.process_identity',side_effect=identity))
            files=s.enter_context(patch('host_schedule_files.observe_schedule_files',return_value=raw['expanded_files'],
                side_effect=PermissionError('CAP_FOWNER denied') if fail else None))
            layers=s.enter_context(patch('host_schedule_cache_layers.observe_cache_layers',return_value=raw['cache_layers']))
            if bad_identity:
                with self.assertRaisesRegex(RuntimeError,'differs from RAM collector identity'):m.observe('fake',out,guests,expected)
                files.assert_not_called();layers.assert_not_called();return
            if fail:
                with self.assertRaisesRegex(RuntimeError,'CAP_FOWNER denied'):m.observe('fake',out,guests,expected)
                layers.assert_not_called();return
            value=m.observe('fake',out,guests,expected,**(dict(full_pfn_evidence=True) if full else {}),
                            **(dict(charge_scope=charge_scope) if charge_scope is not None else {}))
            self.assertEqual(layers.call_args.kwargs,dict(block_method='cachestat'))
            self.assertEqual(files.call_args.args[2],[])  # No historical P1 reuse.
            return value

    def test_fake_endpoint_saves_sources_costs_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);v=self.observe_fake(out);self.assertEqual(v['status'],'OBSERVED_LISTED_OBJECTS')
            raw=json.loads((out/'host-backing-raw.json').read_text())
            self.assertEqual(len(raw['stages']),5);self.assertGreaterEqual(raw['cpu_ns'],0)
            with self.assertRaises(FileExistsError):self.observe_fake(out)

    def test_explicit_charge_scope_forwarded_only_with_full_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);declared=object()
            with self.assertRaisesRegex(ValueError,'explicit full-list'):
                m.observe('fake',out,{}, {},charge_scope=declared)
            self.assertEqual(list(out.iterdir()),[])
            self.observe_fake(out,full=True,charge_scope=declared)

    def test_full_derived_writes_have_real_order_and_close_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);value=self.observe_fake(out,full=True)
            receipt=json.loads((out/'host-backing-io-timings.json').read_text());ops=receipt['operations']
            self.assertEqual(len(ops),5)
            self.assertEqual([Path(r['path']).name for r in ops if 'path' in r],
                ['host-backing-raw.json','host-backing-evidence.json','evidence.json','coverage.json'])
            for row in ops:
                self.assertLessEqual(row['start_monotonic_ns'],row['end_monotonic_ns'])
                if 'path' in row:
                    self.assertLessEqual(row['start_monotonic_ns'],row['write_start_monotonic_ns'])
                    self.assertLessEqual(row['write_start_monotonic_ns'],row['write_end_monotonic_ns'])
                    self.assertLessEqual(row['write_end_monotonic_ns'],row['end_monotonic_ns'])
                    self.assertEqual(Path(row['path']).stat().st_size,row['bytes'])
            self.assertTrue(all(a['end_monotonic_ns']<=b['start_monotonic_ns'] for a,b in zip(ops,ops[1:])))
            self.assertLessEqual(ops[-1]['end_monotonic_ns'],value['all_outputs_complete_monotonic_ns'])

    def test_failed_derived_write_keeps_timing_and_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with self.assertRaises(FileExistsError):self.observe_fake(out,full=True,fail_coverage=True)
            receipt=json.loads((out/'host-backing-io-timings.json').read_text())
            self.assertEqual(receipt['operations'][-1]['status'],'FAIL')
            self.assertIn('FileExistsError',receipt['operations'][-1]['error'])
            self.assertEqual((out/'host-backing-full-pfn/coverage.json').read_text(),'existing')
            self.assertTrue((out/'host-backing-full-pfn/evidence.json').exists())

    def test_permission_failure_is_saved_and_raised_not_accepted_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);self.observe_fake(out,fail=True)
            self.assertEqual(json.loads((out/'host-backing-raw.json').read_text())['status'],'FAIL')
            self.assertEqual(json.loads((out/'host-backing-evidence.json').read_text())['status'],'FAIL')

    def test_ram_binding_conflict_fails_before_any_backing_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);self.observe_fake(out,bad_identity=True)
            self.assertEqual(json.loads((out/'host-backing-evidence.json').read_text())['status'],'FAIL')


if __name__=='__main__':unittest.main()
