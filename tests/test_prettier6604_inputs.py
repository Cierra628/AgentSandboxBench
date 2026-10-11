import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prettier6604_inputs import CONFIG,ROOT,validate

class FrozenBackbone(unittest.TestCase):
    def setUp(self):
        if not (ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/prettier-6604-rl-fork/agent-sampling.json').exists():
            self.skipTest('requires pinned research workload checkout')
    def test_original_sample_and_29_actions_match(self):
        cfg,workload,reference=validate()
        self.assertEqual(sum(map(len,cfg['expected_exit_codes'].values())),29)
        self.assertEqual(cfg['expected_exit_codes']['read_repo'][1],127)
    def changed_config_rejected(self,change):
        cfg=json.loads(CONFIG.read_text());change(cfg)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json';path.write_text(json.dumps(cfg))
            with self.assertRaises(AssertionError):validate(path)
    def test_expected_failure_cannot_be_silently_changed(self):
        self.changed_config_rejected(lambda c:c['expected_exit_codes']['read_repo'].__setitem__(1,0))
    def test_changed_manifest_hash_rejected(self):
        self.changed_config_rejected(lambda c:c['manifest_sha256'].__setitem__('core_patch','0'*64))
    def test_reordered_path_rejected(self):
        self.changed_config_rejected(lambda c:c['path'].reverse())

if __name__=='__main__':unittest.main()
