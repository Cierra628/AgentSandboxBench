from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from fixed_runner_git_identity import instrument, BASELINE_BLOCK, FINAL_BLOCK
from persistent_guest_sampling import instrument as persistent


class FixedIdentityTests(unittest.TestCase):
    def test_real_git_outputs_and_action_environment_are_preserved(self):
        runner=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);repo=base/'repo';repo.mkdir();actions=base/'actions';actions.mkdir()
            env=dict(os.environ,GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_SYSTEM='/dev/null',
                     EMAIL='test@localhost',GUEST_MEMORY_SAMPLING='0',DROP_GUEST_CACHES='0',
                     ACTIONS_DIR=str(actions),START_STEP='001',END_STEP='026')
            for args in (['init','-q'],['-c','user.name=test','-c','user.email=test@localhost','commit','--allow-empty','-qm','base']):
                subprocess.run(['git','-C',str(repo),*args],env=env,check=True,capture_output=True)
            for step in range(1,27):
                command='exit 127\n' if step in (8,16) else 'true\n'
                if step==1:command='printf changed > sample.txt\nenv | sort\ngit config --get user.email || true\n'
                (actions/f'{step:03d}.sh').write_text(command)
            (base/'actions.tsv').write_text('fixture\n')
            outputs=[]
            for label,source in (('original',runner.read_text()),('fixed',instrument(runner.read_text())),
                                 ('persistent-fixed',instrument(persistent(runner.read_text())))):
                subprocess.run(['git','-C',str(repo),'clean','-fdq'],env=env,check=True)
                output=base/label;env['OUTPUT_DIR']=str(output)
                script=base/(label+'.sh');script.write_text(source.replace('/testbed',str(repo)))
                subprocess.run(['bash',str(script)],env=env,check=True,capture_output=True,timeout=30)
                rows=[r.split('\t')[:2] for r in (output/'steps.tsv').read_text().splitlines()[1:]]
                self.assertEqual(rows,[[f'{i:03d}',str(127 if i in (8,16) else 0)] for i in range(1,27)])
                stdout=(output/'stdout/001.log').read_text()
                self.assertNotIn('asb-fixture',stdout)
                self.assertNotIn('GIT_CONFIG_COUNT=',stdout)
                self.assertNotIn('GIT_COMMITTER_EMAIL=',stdout)
                outputs.append(tuple((output/name).read_bytes() for name in ('patch.diff','baseline-tree.txt','original-head.txt','git-status.txt')))
            self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[0],outputs[2])

    def test_git_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);git=base/'git'
            git.write_text('#!/bin/bash\nif [[ ${*: -2} == "reset --mixed" ]]; then echo reset-failed >&2; exit 7; fi\n')
            git.chmod(0o755)
            source='set -euo pipefail\nout='+str(base)+'\n'+BASELINE_BLOCK+'\n'+FINAL_BLOCK
            result=subprocess.run(['bash','-c',instrument(source)],env=dict(os.environ,PATH=str(base)+':'+os.environ['PATH']),capture_output=True,text=True)
            self.assertEqual(result.returncode,7);self.assertIn('reset-failed',result.stderr)

    def test_unknown_runner_is_rejected(self):
        with self.assertRaises(AssertionError):instrument('unrecognized runner')


if __name__=='__main__':unittest.main()
