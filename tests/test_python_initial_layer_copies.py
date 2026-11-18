"""Synthetic metadata and mocks only; no service, process, device or payload reads."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import schedule_smoke_metrics as metrics
from report_python_schedule_matrix import lower_scope


def fixture(copies=False, count=13):
    roles = ['backbone', 'early_1', 'early_2', 'early_3', 'late_1', 'late_2', 'late_3']
    expected = [dict(status='IDENTIFIED', device=1, inode=i+1, size=4096*(i+1), mtime_ns=100,
                     path='/original/'+str(i), reference=dict(digest='layer-'+str(i), size=4096*(i+1))) for i in range(count)]
    guests = {}; objects = {}
    for role in roles:
        rows = copy.deepcopy(expected)
        if copies and role != 'backbone':
            for row in rows:
                row.update(device=2, inode=row['inode']+100, path='/managed/'+str(row['inode']), mtime_ns=200)
            # Snapshot additions are outside the original thirteen image layers.
            rows.append(dict(status='IDENTIFIED', device=2, inode=999, size=1, mtime_ns=200,
                             path='/snapshot', reference=dict(digest='snapshot', size=1)))
        guests[role] = dict(status='IDENTIFIED', lowers=rows)
        for row in rows:
            key = row['device'], row['inode']
            obj = objects.setdefault(key, dict(row, users=[], aliases=[row['path']]))
            obj['users'].append(role)
    return dict(status='IDENTIFIED', guests=guests, objects=list(objects.values())), expected, roles


class InitialCopiesTests(unittest.TestCase):
    def test_c4_observe_nine_layers_and_all_eighteen_active_copies(self):
        import host_schedule_cache_layers as layers
        import host_schedule_files as files
        for copies, mode, count in ((False, 'shared_initial_inodes', 9),
                                    (False, 'all_active_image_layer_copies', 9),
                                    (True, 'all_active_image_layer_copies', 18)):
            with self.subTest(copies=copies, mode=mode), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                out = Path(tmp); roots, expected, roles = fixture(copies, count=9)
                objects = {obj['path']: obj for obj in roots['objects']}
                guests = {role: 'sid-' + role for role in roles}
                vms = {role: dict(pid=100+i) for i, role in enumerate(roles)}
                binding = Mock(); binding.cgroup = Path('/fake/cgroup')
                binding.resolve.side_effect = lambda sid: vms[sid[4:]]
                def query(path):
                    obj = objects[str(path)]
                    return dict(stable=True, resident_bytes=4096,
                                **{k: obj[k] for k in ('device', 'inode', 'size', 'mtime_ns')})
                stack.enter_context(patch.object(metrics, 'Binding', return_value=binding))
                stack.enter_context(patch.object(metrics, 'drives_for_vm', return_value=[]))
                stack.enter_context(patch.object(metrics, 'opened_regular_files', return_value={}))
                stack.enter_context(patch.object(metrics, 'rootfs_inventory', return_value=roots))
                pss = stack.enter_context(patch.object(metrics, 'pss_snapshot', return_value={'status':'OBSERVED'}))
                stack.enter_context(patch.object(metrics, 'counters', return_value={'file':123}))
                validate = stack.enter_context(patch.object(metrics, 'validate_rootfs_inventory'))
                calls = stack.enter_context(patch.object(metrics, 'mincore_file', side_effect=query))
                expanded = stack.enter_context(patch.object(files, 'observe_schedule_files', return_value={'status':'PARTIAL'}))
                cache = stack.enter_context(patch.object(layers, 'observe_cache_layers', return_value={'status':'OBSERVED'}))
                result = metrics.observe('fake', out, guests, 42, expanded=True, cache_layers=True,
                    block_cache_method='cachestat', split_ram=True, initial_files=expected,
                    initial_file_count=9, initial_reference_mode=mode)
                self.assertEqual(result['status'], 'OBSERVED')
                self.assertEqual((result['p1']['unique_files'], result['p1']['resident_bytes']), (count, count*4096))
                self.assertEqual(calls.call_count, count)
                self.assertEqual(len({str(c.args[0]) for c in calls.call_args_list}), count)
                self.assertNotIn('/snapshot', [str(c.args[0]) for c in calls.call_args_list])
                self.assertEqual(lower_scope(result, 9)['lower_physical_files'], count)
                pss.assert_called_once_with(binding, guests, vms)
                validate.assert_called_once_with(roots)
                self.assertTrue(expanded.call_args.kwargs['capture_layers'])
                self.assertTrue(cache.call_args.kwargs['split_ram'])
                self.assertEqual(cache.call_args.kwargs['block_method'], 'cachestat')
                self.assertEqual(result['expanded_files']['status'], 'PARTIAL')
                for value in (result, result['p1']):
                    self.assertGreaterEqual(value['end_monotonic_ns'], value['start_monotonic_ns'])
                    self.assertGreaterEqual(value['cpu_ns'], 0)
                self.assertEqual(json.loads((out/'schedule-smoke-metrics.json').read_text()), result)

    def test_c4_invalid_identity_stops_before_pss_and_residency(self):
        for kind in ('roles', 'order', 'digest', 'size', 'physical_alias', 'missing_object', 'duplicate_object'):
            roots, expected, roles = fixture(True, count=9)
            rows = roots['guests']['early_2']['lowers']
            if kind == 'roles':roles = roles[:-1]
            if kind == 'order':rows[2], rows[3] = rows[3], rows[2]
            if kind == 'digest':rows[2]['reference']['digest'] = 'wrong'
            if kind == 'size':rows[2]['size'] += 1
            if kind == 'physical_alias':rows[2]['inode'] = rows[1]['inode']
            if kind == 'missing_object':roots['objects'].pop(0)
            if kind == 'duplicate_object':roots['objects'].append(roots['objects'][0])
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                binding = Mock(); binding.cgroup = Path('/fake/cgroup')
                binding.resolve.return_value = dict(pid=123)
                with patch.object(metrics, 'Binding', return_value=binding), \
                     patch.object(metrics, 'drives_for_vm', return_value=[]), \
                     patch.object(metrics, 'opened_regular_files', return_value={}), \
                     patch.object(metrics, 'rootfs_inventory', return_value=roots), \
                     patch.object(metrics, 'pss_snapshot') as pss, patch.object(metrics, 'mincore_file') as query:
                    result = metrics.observe('fake', Path(tmp), dict.fromkeys(roles, 'sid'), 0,
                        initial_files=expected, initial_file_count=9, initial_reference_mode='all_active_image_layer_copies')
                self.assertEqual(result['status'], 'FAIL')
                self.assertNotIn('p1', result)
                pss.assert_not_called(); query.assert_not_called()

    def test_all_active_requires_explicit_supported_layer_count(self):
        for count, initial in ((9, None), (8, []), (19, [])):
            with self.subTest(count=count), patch.object(metrics, 'Binding') as binding:
                with self.assertRaises(ValueError):
                    metrics.observe('fake', Path('/unused'), {}, 0, initial_files=initial,
                        initial_file_count=count, initial_reference_mode='all_active_image_layer_copies')
                binding.assert_not_called()

    def test_django_ten_layers_are_explicit_and_all_twenty_copies_retained(self):
        for copies,count in ((False,10),(True,20)):
            roots,expected,roles=fixture(copies,count=10)
            self.assertEqual(len(metrics.select_initial_copies(roots,expected,roles,expected_count=10)),count)
            with self.assertRaises(ValueError):metrics.select_initial_copies(roots,expected,roles)
            roots['guests']['early_1']['lowers'][0]['reference']['digest']='wrong'
            with self.assertRaises(ValueError):metrics.select_initial_copies(roots,expected,roles,expected_count=10)

    def test_shared_thirteen_and_restored_twenty_six(self):
        for copies, count in ((False, 13), (True, 26)):
            roots, expected, roles = fixture(copies)
            selected = metrics.select_initial_copies(roots, expected, roles)
            self.assertEqual(len(selected), count)
            self.assertEqual(len({(r['device'], r['inode']) for r in selected}), count)
            self.assertNotIn(999, [r['inode'] for r in selected])
            if copies:
                self.assertEqual(sorted(len(obj['users']) for obj in selected), [1]*13 + [6]*13)
                with self.assertRaises(ValueError):metrics.select_initial(roots, expected, roles, expected_count=13)

    def test_reject_missing_reordered_unidentified_or_changed_size_layer(self):
        for kind in ('missing', 'order', 'status', 'size', 'digest', 'physical_alias'):
            roots, expected, roles = fixture(True)
            rows = roots['guests']['early_2']['lowers']
            if kind == 'missing':rows.pop(4)
            if kind == 'order':rows[2], rows[3] = rows[3], rows[2]
            if kind == 'status':rows[2]['status'] = 'UNKNOWN'
            if kind == 'size':rows[2]['size'] += 1
            if kind == 'digest':rows[2]['reference']['digest'] = 'wrong'
            if kind == 'physical_alias':rows[2]['inode'] = rows[1]['inode']
            with self.subTest(kind=kind), self.assertRaises(ValueError):metrics.select_initial_copies(roots, expected, roles)

    def test_reject_missing_duplicate_or_inconsistent_objects_and_roles(self):
        for kind in ('missing', 'duplicate', 'users', 'alias', 'size', 'mtime', 'unknown', 'roles', 'inventory', 'parent'):
            roots, expected, roles = fixture(True)
            if kind == 'missing':roots['objects'].pop(0)
            if kind == 'duplicate':roots['objects'].append(roots['objects'][0])
            if kind == 'users':roots['objects'][0]['users'].append('early_1')
            if kind == 'alias':roots['objects'][0]['aliases'] = []
            if kind == 'size':roots['objects'][0]['size'] += 1
            if kind == 'mtime':roots['objects'][0]['mtime_ns'] += 1
            if kind == 'unknown':roots['objects'][0]['status'] = 'UNKNOWN'
            if kind == 'roles':roles = roles[:-1]
            if kind == 'inventory':roots['status'] = 'INCOMPLETE'
            if kind == 'parent':expected[0]['inode'] = 9876
            with self.subTest(kind=kind), self.assertRaises(ValueError):metrics.select_initial_copies(roots, expected, roles)

    def test_identity_diagnostics_survive_selector_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); binding = Mock(); binding.cgroup = Path('/fake/cgroup')
            binding.resolve.return_value = dict(pid=123)
            roots, expected, roles = fixture(True)
            roots['status'] = 'INCOMPLETE'
            with patch.object(metrics, 'Binding', return_value=binding), \
                 patch.object(metrics, 'drives_for_vm', return_value=[dict(drive_id='user_rootfs')]), \
                 patch.object(metrics, 'opened_regular_files', return_value={}), \
                 patch.object(metrics, 'rootfs_inventory', return_value=roots), \
                 patch.object(metrics, 'pss_snapshot') as pss, patch.object(metrics, 'mincore_file') as query:
                result = metrics.observe('fake', out, dict.fromkeys(roles, 'sid'), 0,
                    initial_files=expected, initial_file_count=13, initial_reference_mode='all_active_image_layer_copies')
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['rootfs_inventory'], roots)
            self.assertEqual(len(result['bindings']), 7)
            self.assertEqual(len(result['drives']), 7)
            self.assertEqual(result['initial_reference_files'], expected)
            self.assertNotIn('p1', result)
            pss.assert_not_called(); query.assert_not_called()
            self.assertEqual(json.loads((out/'schedule-smoke-metrics.json').read_text())['status'], 'FAIL')

    def test_query_each_physical_copy_once_and_report_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); binding = Mock(); binding.cgroup = Path('/fake/cgroup')
            binding.resolve.return_value = dict(pid=123)
            roots, expected, roles = fixture(True)
            objects = {obj['path']: obj for obj in roots['objects']}
            def query(path):
                obj = objects[str(path)]
                return dict(stable=True, resident_bytes=4096, **{k: obj[k] for k in ('device', 'inode', 'size', 'mtime_ns')})
            with patch.object(metrics, 'Binding', return_value=binding), \
                 patch.object(metrics, 'drives_for_vm', return_value=[]), \
                 patch.object(metrics, 'opened_regular_files', return_value={}), \
                 patch.object(metrics, 'rootfs_inventory', return_value=roots), \
                 patch.object(metrics, 'pss_snapshot', return_value={}), \
                 patch.object(metrics, 'counters', return_value={}), \
                 patch.object(metrics, 'validate_rootfs_inventory'), \
                 patch.object(metrics, 'mincore_file', side_effect=query) as calls:
                result = metrics.observe('fake', out, dict.fromkeys(roles, 'sid'), 0,
                    initial_files=expected, initial_file_count=13, initial_reference_mode='all_active_image_layer_copies')
            self.assertEqual(result['status'], 'OBSERVED')
            self.assertEqual(calls.call_count, 26)
            self.assertEqual(result['p1']['resident_bytes'], 26*4096)
            self.assertEqual(lower_scope(result)['lower_physical_files'], 26)
            bad = copy.deepcopy(result); bad['p1']['files'][-1] = bad['p1']['files'][0]
            with self.assertRaises(AssertionError):lower_scope(bad)
            bad = copy.deepcopy(result); bad['initial_layer_selection']['physical_file_count'] = 13
            with self.assertRaises(AssertionError):lower_scope(bad)
            self.assertEqual(lower_scope(dict(p1=dict(unique_files=13)))['lower_reference_mode'], 'shared_initial_inodes')


if __name__ == '__main__':unittest.main()
