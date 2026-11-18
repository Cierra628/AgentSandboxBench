"""Offline metadata/mocks and one bounded 8 MiB anonymous allocator unit check; no sandbox."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_pressure as pressure
from host_file_monitor import counters,HostFileMonitor
from run_host_density import planned_runs


class HostPressureTests(unittest.TestCase):
    def test_four_conditions_and_original_plans(self):
        self.assertEqual([(v['condition'],v['mode'],v['pressure']) for v in planned_runs(pressure_comparison=True)],
            [('A','shared',False),('B','independent',False),('C','shared',True),('D','independent',True)])
        self.assertEqual(len(planned_runs()),12);self.assertEqual(len(planned_runs(True)),2)
        with self.assertRaises(ValueError):planned_runs(True,True)

    def test_cgroup_proof_rejects_equal_and_descendant_but_accepts_sibling(self):
        for worker in ('/asbhost-test.slice','/asbhost-test.slice/worker.service'):
            with self.assertRaises(ValueError):pressure.require_outside(worker,'/asbhost-test.slice')
        pressure.require_outside('/asbpressure.slice/worker.service','/asbhost-test.slice')
        pressure.require_outside('/asbhost-test2.slice/worker.service','/asbhost-test.slice')

    def test_host_counter_units_and_swap_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 200 kB\nSwapTotal: 100 kB\nSwapFree: 50 kB\n')
            (root/'vmstat').write_text('pgscan_kswapd 7\npgscan_file 7\npgsteal_kswapd 4\npswpin 2\npswpout 1\nnr_file_pages 3\n')
            value=pressure.host_snapshot(root);self.assertEqual(value['MemAvailable'],200*1024);self.assertEqual(value['SwapFree'],50*1024)
            self.assertEqual(value['pgscan_kswapd'],7);self.assertNotIn('pgscan',value);self.assertEqual(value['pswpout'],1)

    def test_extra_cgroup_statistics_are_opt_in_and_missing_values_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'memory.current').write_text('10');base='file 5\nanon 4\nshmem 0\n'
            (root/'memory.stat').write_text(base)
            self.assertNotIn('workingset_refault_file',counters(root))
            with self.assertRaises(ValueError):counters(root,extra_keys=pressure.EXTRA_STATS)
            (root/'memory.stat').write_text(base+'workingset_refault_file 7\nworkingset_activate_file 2\n')
            monitor=HostFileMonitor(root/'samples',root,include_pss=False,extra_stat_keys=pressure.EXTRA_STATS)
            try:receipt=monitor.stop()
            finally:
                if monitor.thread.is_alive():monitor.stop()
            self.assertEqual(receipt['status'],'PASS');self.assertIn('workingset_refault_file', (root/'samples/memory.tsv').read_text().splitlines()[0])

    def test_default_does_not_create_gate_and_new_gate_precedes_guest_creation(self):
        self.assertIsNone(pressure.before_guests({},Path('/unused'),Path('/unused')))
        source=(ROOT/'scripts/run_density_guests.py').read_text()
        self.assertLess(source.index("state['pressure_before_guests']=before_guests"),source.index("state['eviction'],state['P0']=evict"))
        self.assertLess(source.index("state['eviction'],state['P0']=evict"),source.index('futures=[pool.submit'))

    def test_control_gate_keeps_prepared_counters_without_starting_allocator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'pressure';directory.mkdir();(directory/'ready.json').write_text('{"status":"READY","enabled":false}')
            with patch('host_file_monitor.counters',return_value={'file':1,'current':2}),patch.object(pressure,'process_snapshot',side_effect=AssertionError('control must not query allocator')):
                value=pressure.before_guests(dict(pressure_context=str(directory),pressure_enabled=False),root,Path('/sys/fs/cgroup/asbhost-test.slice'))
            self.assertEqual(value['before'],value['after']);self.assertTrue((directory/'start-request.json').exists())

    def test_cleanup_stops_owned_pressure_service_even_when_process_disappeared(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=pressure.PressureRun(Path(tmp),True,Path('/unused'));run.pid=123;run.unit='owned-fixture';run.phase='HOLDING'
            with patch.object(pressure,'process_snapshot',side_effect=FileNotFoundError()),patch.object(pressure.subprocess,'run') as stop:
                with self.assertRaises(FileNotFoundError):run.stop()
            self.assertEqual(stop.call_args.args[0],['systemctl','stop','owned-fixture'])
            self.assertIn('before_release_error',run.receipt);self.assertEqual(run.phase,'RELEASED')

    def test_release_phase_is_unknown_until_owned_service_has_exited(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=pressure.PressureRun(Path(tmp),True,Path('/unused'));run.phase='RELEASING'
            self.assertIsNone(run.sample()['pressure_anon_bytes'])
            run.phase='RELEASED';self.assertEqual(run.sample()['pressure_anon_bytes'],0)

    def test_allocator_holds_fixed_bounded_memory_and_releases_on_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'pressure';ready=root/'ready.json'
            subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(ROOT/'scripts/host_anon_pressure.c'),'-o',str(binary)],check=True,capture_output=True,timeout=15)
            child=subprocess.Popen([str(binary),str(ready),'0',str(8*2**20)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while not ready.exists():
                    if child.poll() is not None:self.fail(child.communicate()[1].decode())
                    if time.monotonic()>deadline:self.fail('bounded allocator did not become ready')
                    time.sleep(.01)
                value=json.loads(ready.read_text());self.assertEqual(value['status'],'HOLDING');self.assertEqual(value['allocated_bytes'],8*2**20)
                self.assertFalse(value['target_reached']);sample=pressure.process_snapshot(child.pid)
                self.assertGreater(sample['anon_bytes'],7*2**20);self.assertLess(sample['anon_bytes'],12*2**20)
                self.assertIsNone(child.poll());child.terminate();self.assertEqual(child.wait(timeout=3),0)
            finally:
                if child.poll() is None:child.kill();child.wait(timeout=3)
                child.communicate(timeout=3)


if __name__=='__main__':unittest.main()
