import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from online_task import load
from tool_trajectory import output_content,request
from platform_tasks import configuration,commands


class OnlineTaskTests(unittest.TestCase):
    def test_task_config_controls_prompt_without_gold_patch(self):
        from unittest.mock import patch
        value={'instance_id':'prettier__prettier-6604','base_commit':'affa24ce764bd14ac087abeef0603012f5c635d6',
               'problem_statement':'Repair the parentheses','patch':'SECRET_GOLD','test_patch':'SECRET_TEST'}
        with patch('exp1_official_task.load',return_value=value):
            task=load(ROOT/'configs/online-task-prettier6604.json',Path('/unused'))
        self.assertIn(value['problem_statement'],task['prompt'])
        self.assertNotIn('SECRET',json.dumps(task))

    def test_repository_state_includes_tracked_and_untracked_files(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);subprocess.run(['git','init','-q',p],check=True)
            (p/'tracked').write_text('initial')
            subprocess.run(['git','-C',p,'add','.'],check=True)
            subprocess.run(['git','-C',p,'-c','user.name=test','-c','user.email=test@test','commit','-qm','init'],check=True)
            (p/'tracked').write_text('patched');(p/'new').write_text('new')
            def cli(*args,**kw):return subprocess.check_output([sys.executable,ROOT/'scripts/guest_tool_capture.py',args[-1]],text=True)
            state=request(cli,'unused',dict(kind='state',files=[str(p/'tracked')],repo=str(p)))
            self.assertEqual(set(state['repository']['changed_files']),{'tracked','new'})
            self.assertIn(b'+patched',base64.b64decode(state['repository']['patch_b64']))

    def test_only_known_timing_changes_are_accepted(self):
        a=b'Tests: 2 passed, 2 total\nTime: 1.24s, estimated 3s\nDone in 2.33s.\n'
        b=a.replace(b'1.24',b'9.62').replace(b'2.33',b'10.6')
        self.assertEqual(output_content(a,'jest-timing-v1'),output_content(b,'jest-timing-v1'))
        self.assertNotEqual(output_content(a,'jest-timing-v1'),output_content(b.replace(b'2 passed',b'1 passed'),'jest-timing-v1'))
        self.assertNotEqual(output_content(a,'strict'),output_content(b,'strict'))

    def test_online_prettier_routes_task_and_model_separately(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604.json')
        check,live=commands(cfg,Path('/cache'),True,agent_config=Path('/model'),task_config=ROOT/'configs/online-task-prettier6604.json')
        self.assertIn('--task-config',check);self.assertIn('--online-task-config',live)
        self.assertNotIn('--expected-official-audit',live)
        self.assertNotIn('--prettier6604-readonly-replay',live)

    def test_known_warning_pid_only_and_arbitrary_warning_stays_strict(self):
        a=b"(node:12) [DEP0128] DeprecationWarning: Invalid 'main' field in '/testbed/node_modules/find-parent-dir/package.json' of 'find-parent-dir.js'. Please either fix that or report it to the module author\n(Use `node --trace-deprecation ...` to show where the warning was created)\n"
        b=a.replace(b'node:12',b'node:99')
        self.assertEqual(output_content(a,'prettier-runtime-v1'),output_content(b,'prettier-runtime-v1'))
        self.assertNotEqual(output_content(a,'jest-timing-v1'),output_content(b,'jest-timing-v1'))
        self.assertNotEqual(output_content(a.replace(b'find-parent-dir',b'OTHER'),'prettier-runtime-v1'),output_content(b.replace(b'find-parent-dir',b'OTHER'),'prettier-runtime-v1'))
