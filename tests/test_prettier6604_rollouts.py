"""Regression: internally valid hashes do not prove sampled branch identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_prettier6604_rollouts import audit, prepare
from prettier6604_inputs import validate


class FrozenBranches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg, cls.workload, _ = validate()
        except FileNotFoundError:
            raise unittest.SkipTest('requires pinned research workload checkout')
        cls.samples = json.loads((cls.workload/'agent-sampling.json').read_text())['segments']
        cls.rollouts = json.loads((cls.workload/'rollouts.json').read_text())

    def test_valid_hashes_still_contain_four_wrong_branch_segments(self):
        report = audit(self.workload, self.samples, self.rollouts)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual([(m['role'], m['segment']) for m in report['mismatches']],
                         [('early_1','early_inspect'), ('early_2','early_inspect'),
                          ('late_1','late_inspect'), ('late_2','late_inspect')])
        self.assertEqual(sum(len(m['differing_steps']) for m in report['mismatches']), 16)

    def test_export_preserves_sampling_and_backbone_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'export'
            original, exported, provenance = prepare(out)
            self.assertEqual(exported['status'], 'PASS')
            self.assertEqual(exported['logical_action_count'], 189)
            self.assertEqual(exported['logical_segment_count'], 35)
            self.assertTrue(provenance['original_inputs_unchanged'])
            for segment in self.cfg['path']:
                self.assertEqual((out/'workload/segments'/segment/'actions.tsv').read_bytes(),
                                 (self.workload/'segments'/segment/'actions.tsv').read_bytes())
            before = (out/'provenance.json').read_bytes()
            with self.assertRaises(FileExistsError):
                prepare(out)
            self.assertEqual((out/'provenance.json').read_bytes(), before)

    def test_missing_role_sample_cannot_be_replaced_by_another_branch(self):
        samples = [s for s in self.samples if not (s['role']=='early_1' and s['segment']=='early_inspect')]
        with self.assertRaisesRegex(ValueError, 'missing sampled segment'):
            audit(self.workload, samples, self.rollouts)


if __name__ == '__main__':
    unittest.main()
