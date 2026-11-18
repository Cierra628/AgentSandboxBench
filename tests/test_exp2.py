"""Exp2 frozen inputs, fixed routing and restored-state regression checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_exp2 import validate,verify_boundary,final_source,check_steps,initialize_progress
from platform_tasks import configuration,commands,ROOT


class Exp2Tests(unittest.TestCase):
    def test_image_without_workspace_can_initialize_progress(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'workspace'
            def cli(label,*argv):
                command=[str(x).replace('/workspace',str(directory)) for x in argv[2:]]
                subprocess.run(command,check=True,timeout=5)
            initialize_progress(cli,'test-guest')
            self.assertEqual((directory/'asb-exp2-progress').read_text(),'0')

    def test_original_four_round_inputs_and_no_tool_changes(self):
        cfg,workload=validate()
        self.assertEqual(len(cfg['file_sha256']),38)
        action=(workload/'rounds/3/actions/003.sh').read_text()
        import ast
        tree=ast.parse(action.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0])
        old=next(ast.literal_eval(x.value) for x in tree.body if isinstance(x,ast.Assign) and isinstance(x.targets[0],ast.Name) and x.targets[0].id=='old')
        self.assertNotEqual(final_source('before\n'+old+'\nafter\n',action),'before\n'+old+'\nafter\n')
        with self.assertRaises(AssertionError):final_source('wrong source',action)

    def test_snapshot_success_does_not_replace_state_and_progress_checks(self):
        before=dict(progress='2',files={'tmp':'hash'},patch='patch')
        probe=dict(pid=5,start_ticks='1',boot_id='boot',anonymous_sha256='anon',open_fd_sha256='fd',pathname_sha256='path',count=3)
        verify_boundary(before,dict(before),probe,dict(probe,count=4))
        for bad in [dict(before,progress='1'),dict(before,files={'tmp':None})]:
            with self.assertRaises(AssertionError):verify_boundary(before,bad,probe,dict(probe,count=4))
        for bad in [dict(probe,pid=6,count=4),dict(probe,anonymous_sha256='new',count=4),dict(probe)]:
            with self.assertRaises(AssertionError):verify_boundary(before,before,probe,bad)

    def test_two_cache_modes_dispatch_guest_only_exp2(self):
        for mode in ('preserve','drop'):
            cfg=configuration(ROOT/f'configs/platform-exp2-{mode}.json')
            check,live=commands(cfg,Path('/not-created'))
            self.assertIn('run_exp2.py',check[2]);self.assertEqual(live[live.index('--exp2-cache-mode')+1],mode)
            self.assertNotIn('--prettier6604-checkpoint',live)
            with self.assertRaises(ValueError):commands(cfg,Path('/not-created'),tool_capture=True)

    def test_exp2_cannot_silently_become_an_exp3_variant(self):
        original=json.loads((ROOT/'configs/platform-exp2-preserve.json').read_text())
        for changes in [dict(cache_mode='unknown'),dict(rollout='bpo'),dict(checkpoint_after_step=24),dict(physical_cache_after_segments=['read_repo'])]:
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'config.json';p.write_text(json.dumps(dict(original,**changes)))
                with self.assertRaises(ValueError):configuration(p)


if __name__=='__main__':unittest.main()
