"""Offline checks only: no Docker, service, real sandbox, or workload reads."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import schedule_smoke_metrics as m


class ScheduleSmokeTests(unittest.TestCase):
    def test_real_observe_pinned_layers_selection_pss_and_cache_paths(self):
        import tempfile
        from contextlib import ExitStack
        import host_schedule_files as files
        import host_schedule_cache_layers as layers
        for count,mode,fault in ((count,mode,fault) for count in (7,8) for mode,fault in (('shared_initial_inodes', None),
                            ('all_active_image_layer_copies', None),
                            ('all_active_image_layer_copies', 'order'),
                            ('all_active_image_layer_copies', 'duplicate'))):
            with self.subTest(mode=mode, fault=fault), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                out=Path(tmp)/'mount/controller/text';out.mkdir(parents=True)
                guests={str(i):'sid'+str(i) for i in range(7)}
                vms={role:dict(pid=100+int(role), start_ticks=role) for role in guests}
                expected=[];objects=[]
                for index in range(2*count if mode=='all_active_image_layer_copies' else count):
                    path=Path(tmp)/str(index)
                    with path.open('wb') as file:file.truncate(4096)
                    row=dict(m.file_identity(path),path=str(path),status='IDENTIFIED',
                        reference=dict(digest='layer'+str(index%count),size=4096))
                    expected.append(row)
                    users=(list(guests) if mode=='shared_initial_inodes' else
                           list(guests)[:-1] if index<count else [list(guests)[-1]])
                    objects.append(dict(row,users=users,aliases=[str(path)]))
                roots=dict(status='IDENTIFIED',objects=objects,guests={role:dict(status='IDENTIFIED',
                    lowers=copy.deepcopy(expected[count:] if role=='6' and len(expected)==2*count else expected[:count])) for role in guests})
                if fault=='order':roots['guests']['6']['lowers'].reverse()
                if fault=='duplicate':roots['objects'].append(copy.deepcopy(objects[0]))
                binding=Mock();binding.cgroup=Path('/synthetic/cgroup')
                binding.resolve.side_effect=lambda sid:vms[sid[3:]]
                stack.enter_context(patch.object(m,'Binding',return_value=binding))
                stack.enter_context(patch.object(m,'drives_for_vm',return_value=[]))
                stack.enter_context(patch.object(m,'opened_regular_files',return_value={}))
                stack.enter_context(patch.object(m,'rootfs_inventory',return_value=roots))
                validate=stack.enter_context(patch.object(m,'validate_rootfs_inventory'))
                pss=stack.enter_context(patch.object(m,'pss_snapshot',return_value=dict(total_bytes=7168,per_sandbox_bytes=1024)))
                stack.enter_context(patch.object(m,'counters',return_value=dict(current=0,file=0)))
                expanded=stack.enter_context(patch.object(files,'observe_schedule_files',return_value=dict(status='OBSERVED')))
                cache=stack.enter_context(patch.object(layers,'observe_cache_layers',return_value=dict(status='OBSERVED')))
                result=m.observe('synthetic',out,guests,0,initial_files=expected[:count],initial_file_count=count,
                    initial_reference_mode=mode,expanded=True,cache_layers=True,block_cache_method='cachestat',split_ram=True)
                if fault:
                    self.assertEqual(result['status'],'FAIL')
                    self.assertIn('order or identity' if fault=='order' else 'missing or duplicated',result['error'])
                    pss.assert_not_called();cache.assert_not_called()
                    continue
                self.assertEqual(result['status'],'OBSERVED')
                self.assertEqual(result['p1']['unique_files'],len(objects))
                self.assertEqual(result['initial_layer_selection']['logical_layer_count'],count)
                self.assertEqual(len({(r['device'],r['inode']) for r in result['p1']['files']}),len(objects))
                self.assertEqual(result['p1']['resident_bytes'],sum(r['resident_bytes'] for r in result['p1']['files']))
                self.assertEqual(result['pss']['per_sandbox_bytes'],1024)
                pss.assert_called_once_with(binding,guests,vms);validate.assert_called_once_with(roots)
                self.assertEqual(len(expanded.call_args.args[2]),len(objects))
                self.assertEqual(cache.call_args.kwargs,dict(block_method='cachestat',split_ram=True))

    def test_expanded_files_is_explicit_and_requires_continuous_smoke(self):
        with self.assertRaises(ValueError):m.options(SimpleNamespace(schedule_expanded_files=True))
        for native in (False, True):
            args=dict(schedule_smoke_metrics=True,schedule_expanded_files=True)
            if native:args.update(prettier6604_task='task',prettier6604_export='export',immutable_cache_seed='cache')
            for schedule in ('grpo','bpo','tvcache'):
                args[('prettier6604_' if native else '')+'schedule']=schedule
                self.assertTrue(m.options(SimpleNamespace(**args),native=native))
            with self.assertRaises(ValueError):m.options(SimpleNamespace(**args,schedule_sampling_control=True),native=native)

    def test_cache_layers_explicit_dependency_and_legacy_defaults(self):
        for args in (dict(schedule_cache_layers=True),dict(schedule_smoke_metrics=True,schedule_cache_layers=True)):
            with self.assertRaises(ValueError):m.options(SimpleNamespace(**args))
        self.assertTrue(m.options(SimpleNamespace(schedule_smoke_metrics=True,schedule_expanded_files=True,
            schedule_cache_layers=True,schedule='bpo')))

    def test_explicit_block_method_dependency_native_and_ordinary(self):
        for native in (False,True):
            for method in ('mincore','cachestat','compare'):
                args=dict(schedule_block_cache_method=method)
                with self.assertRaises(ValueError):m.options(SimpleNamespace(**args),native=native)
                args.update(schedule_smoke_metrics=True,schedule_expanded_files=True,schedule_cache_layers=True)
                if native:args.update(prettier6604_task='task',prettier6604_export='export',immutable_cache_seed='cache')
                self.assertTrue(m.options(SimpleNamespace(**args),native=native))
                args['schedule_sampling_control']=True
                with self.assertRaises(ValueError):m.options(SimpleNamespace(**args),native=native)
        with self.assertRaises(ValueError):m.options(SimpleNamespace(schedule_block_cache_method='auto',schedule_cache_layers=True))

    def test_observe_passes_explicit_method_to_live_cache_layer_function(self):
        import tempfile
        from contextlib import ExitStack
        import host_schedule_cache_layers as layers
        import host_schedule_files as files
        for method in (None,'cachestat','compare'):
            with self.subTest(method=method),tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
                out=Path(tmp)/'mount/controller/prettier6604';out.mkdir(parents=True)
                binding=Mock();binding.cgroup=Path('/fake/cgroup');binding.resolve.return_value={'pid':123}
                stack.enter_context(patch.object(m,'Binding',return_value=binding))
                stack.enter_context(patch.object(m,'drives_for_vm',return_value=[]))
                stack.enter_context(patch.object(m,'check_cache',return_value={'local_files':[]}))
                stack.enter_context(patch.object(m,'opened_regular_files',return_value=[]))
                stack.enter_context(patch.object(m,'rootfs_inventory',return_value={}))
                stack.enter_context(patch.object(m,'select_initial',return_value=[]))
                stack.enter_context(patch.object(m,'pss_snapshot',return_value={}))
                stack.enter_context(patch.object(m,'counters',return_value={}))
                stack.enter_context(patch.object(m,'validate_rootfs_inventory'))
                inventory=stack.enter_context(patch.object(files,'observe_schedule_files',return_value={}))
                query=stack.enter_context(patch.object(layers,'observe_cache_layers',return_value={'status':'OBSERVED'}))
                result=m.observe('fake',out,{'role':'sandbox'},0,expanded=True,cache_layers=True,block_cache_method=method)
                self.assertEqual(result['status'],'OBSERVED')
                self.assertEqual(query.call_args.kwargs['block_method'],method or 'mincore')
                self.assertTrue(inventory.call_args.kwargs['capture_layers'])
                self.assertEqual(inventory.call_args.kwargs['owned_roots'],[out.parent.parent/'run',out.parent.parent/'tmp'])

    def test_opt_in_and_full_profile_only(self):
        self.assertFalse(m.options(SimpleNamespace()))
        for schedule in ('grpo', 'bpo', 'tvcache'):
            args = dict(schedule_smoke_metrics=True, schedule=schedule)
            self.assertTrue(m.options(SimpleNamespace(**args)))
            for variant in (dict(light_monitor=True), dict(role='early_1'), dict(host_shared_files=True), dict(monitor_mode='off')):
                with self.assertRaises(ValueError):m.options(SimpleNamespace(**dict(args, **variant)))
        args = dict(schedule_smoke_metrics=True, prettier6604_schedule='tvcache', prettier6604_task='task',
                    prettier6604_export='export', immutable_cache_seed='cache')
        self.assertTrue(m.options(SimpleNamespace(**args), native=True))
        del args['immutable_cache_seed']
        with self.assertRaises(ValueError):m.options(SimpleNamespace(**args), native=True)

    def test_explicit_grpo_sampling_control_only(self):
        for native in (False,True):
            prefix='prettier6604_' if native else ''
            args=dict(schedule_smoke_metrics=True,schedule_sampling_control=True)
            if native:args.update(prettier6604_task='task',prettier6604_export='export',immutable_cache_seed='cache')
            for mode in ('continuous','off'):
                args[prefix+'monitor_mode']=mode
                self.assertTrue(m.options(SimpleNamespace(**args),native=native))
            for schedule in ('bpo','tvcache'):
                args[prefix+'schedule']=schedule
                with self.assertRaises(ValueError):m.options(SimpleNamespace(**args),native=native)

    def test_exact_initial_files_not_snapshot_extras_or_aliases(self):
        roles = list('abcdefg'); expected = [dict(device=1, inode=i) for i in range(19)]
        rows = [dict(row, status='IDENTIFIED') for row in expected]
        objects = [dict(row, users=roles) for row in expected] + [dict(device=1, inode=99, users=roles)]
        roots = dict(status='IDENTIFIED', guests={role: dict(lowers=rows) for role in roles}, objects=objects)
        self.assertEqual(len(m.select_initial(roots, expected, roles)), 19)
        bad = copy.deepcopy(roots);bad['guests']['a']['lowers'].pop()
        with self.assertRaises(ValueError):m.select_initial(bad, expected, roles)
        bad = copy.deepcopy(roots);bad['objects'].append(bad['objects'][0])
        with self.assertRaises(ValueError):m.select_initial(bad, expected, roles)
        with self.assertRaises(ValueError):m.select_initial(roots, expected[:-1], roles)

    def test_pss_seven_root_processes_common_window_and_units(self):
        guests = {str(i): 'sid'+str(i) for i in range(7)}
        vms = {str(i): dict(pid=i+100, start_ticks=str(i)) for i in range(7)}
        binding = Mock();binding.resolve.side_effect = lambda sid: vms[sid[3:]]
        def read(pid, pss):
            self.assertTrue(pss)
            return dict(start_ticks=str(pid-100), pss_kib=100+pid)
        with patch.object(m, 'read', side_effect=read), patch.object(m.Path, 'stat', return_value=SimpleNamespace(st_uid=0)):
            value = m.pss_snapshot(binding, guests, vms)
        self.assertEqual(value['total_bytes'], sum(range(200,207))*1024)
        self.assertEqual(value['per_sandbox_bytes'], value['total_bytes']/7)
        self.assertEqual(len(value['processes']), 7)
        self.assertLessEqual(value['start_monotonic_ns'], value['end_monotonic_ns'])
        with patch.object(m, 'read', side_effect=PermissionError('smaps denied')):
            with self.assertRaises(PermissionError):m.pss_snapshot(binding, guests, vms)
        with self.assertRaises(ValueError):m.pss_snapshot(binding, dict(list(guests.items())[:6]), vms)


if __name__ == '__main__':unittest.main()
