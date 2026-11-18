"""Offline checks only; no systemd, loop, mount or existing-service operations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_host_loop_accounting import checked_directory,cleanup,compare_delta,controller_state,first_shortfall


class LoopAccountingTests(unittest.TestCase):
    def test_first_detected_gap_keeps_stage_order_and_absence(self):
        points={'allocated':{'shortfall_bytes':0},'formatted':{'shortfall_bytes':4096},'attached':{'shortfall_bytes':8192}}
        self.assertEqual(first_shortfall(points),'formatted')
        self.assertIsNone(first_shortfall({'baseline':{'shortfall_bytes':0}}))

    def test_initialization_runner_creates_only_one_io_on_trial(self):
        import run_host_loop_accounting as runner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'scripts').mkdir()
            (root/'scripts/host_file_monitor.py').write_text('# saved dependency\n')
            def fake_run(argv,**kwargs):
                if 'worker' in argv:
                    out=Path(argv[argv.index('--output')+1])
                    (out/'result.json').write_text(json.dumps(dict(status='PASS',first_detected_shortfall_stage='formatted')))
                return SimpleNamespace(returncode=0)
            with patch.object(runner,'ROOT',root),patch.object(runner,'CGROOT',root/'cgroup'),\
                 patch.object(runner,'available',return_value=64*2**30),\
                 patch('experiment_disk_guard.writer_lock',return_value=nullcontext()),\
                 patch.object(runner.os,'geteuid',return_value=0),\
                 patch.object(runner.subprocess,'run',side_effect=fake_run) as execute:
                self.assertEqual(runner.run(initialization_only=True),0)
            commands=[call.args[0] for call in execute.call_args_list if 'worker' in call.args[0]]
            self.assertEqual(len(commands),1)
            self.assertIn('--initialization-only',commands[0])
            self.assertIn('--property=IOAccounting=yes',commands[0])
            self.assertEqual(commands[0][commands[0].index('--io')+1],'yes')
            result=json.loads(next((root/'.artifacts/host-loop-accounting').glob('*/result.json')).read_text())
            self.assertEqual(len(result['trials']),1)
            self.assertNotIn('signed_gap_change_bytes',result)

    def test_directory_is_one_owned_trial(self):
        p=ROOT/'.artifacts/host-loop-accounting/test-key/io-off'
        self.assertEqual(checked_directory(p),p)
        for p in [ROOT,ROOT/'.artifacts/host-loop-accounting',ROOT/'.artifacts/host-loop-accounting/test-key/other']:
            with self.assertRaises(ValueError):checked_directory(p)

    def test_delta_keeps_signed_gap_and_ancestor_only_growth(self):
        before=dict(resident_bytes=10,inner_after={'file':20},outer_after={'file':30})
        point=dict(resident_bytes=138,inner_after={'file':84},outer_after={'file':158})
        d=compare_delta(before,point)
        self.assertEqual(d['inventory_growth_bytes'],128)
        self.assertEqual(d['signed_inventory_minus_inner_growth_bytes'],64)
        self.assertEqual(d['outer_only_file_growth_bytes'],64)
        point['inner_after']['file']=160
        self.assertEqual(compare_delta(before,point)['signed_inventory_minus_inner_growth_bytes'],-12)

    def test_controller_state_records_actual_io_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'cgroup.controllers').write_text('memory io\n');(p/'cgroup.subtree_control').write_text('memory\n')
            self.assertFalse(controller_state(p)['io_stat_exists'])
            (p/'io.stat').write_text('7:0 rbytes=0 wbytes=4096\n')
            self.assertTrue(controller_state(p)['io_stat_exists'])
            self.assertIn('wbytes=4096',controller_state(p)['io_stat'])

    def test_cleanup_refuses_changed_image_without_device_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);image=p/'volume.ext4';image.write_bytes(b'original')
            s=image.stat();(p/'resources.json').write_text(json.dumps(dict(image_device=s.st_dev,image_inode=s.st_ino+1)))
            with patch('run_host_loop_accounting.checked_directory',return_value=p),patch('run_host_loop_accounting.subprocess.check_output') as call:
                with self.assertRaises(AssertionError):cleanup(p)
                call.assert_not_called()
            self.assertEqual(image.read_bytes(),b'original')

    def test_host_entry_enables_io_without_starting_a_real_unit(self):
        import run_host_file_workflow
        with patch.object(sys,'argv',['entry','unused.json']),\
             patch('platform_tasks.configuration',return_value={'host_file_monitor':True}),\
             patch('platform_tasks.preflight'),\
             patch('run_host_file_workflow.subprocess.check_output',return_value='systemd\n'),\
             patch('run_host_file_workflow.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(run_host_file_workflow.main(),0)
            command=run.call_args_list[0].args[0]
            self.assertIn('--property=MemoryAccounting=yes',command)
            self.assertIn('--property=IOAccounting=yes',command)
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertIn('io_controller_state=io_state',code)
        self.assertLess(code.index('io_controller_state=io_state'),code.index("stage='guest-cache-metadata-probe'"))


if __name__=='__main__':unittest.main()
