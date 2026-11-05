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
