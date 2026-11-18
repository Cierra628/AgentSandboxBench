"""Offline metadata tests only; no services, devices, processes or cache queries."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('python_schedule_preparation', ROOT / 'scripts/prepare_python_schedule_matrix.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'configs').mkdir()
        for profile in module.TASKS:
            prepared = self.root / profile
            prepared.mkdir()
            backbone = ['read_repo', 'trace', 'core_patch', 'core_verify', 'finish']
            paths = {'backbone': backbone}
            counts = dict(zip(backbone, [6, 7, 9, 7, 6]))
            for role in module.ROLES[1:]:
                prefix = 2 if role.startswith('early_') else 4
                paths[role] = backbone[:prefix] + [role + '_inspect', role + '_finish']
                counts.update({role + '_inspect': 5, role + '_finish': 7 if prefix == 2 else 6})
            samples = []
            for segment, count in counts.items():
                owner = next(r for r, path in paths.items() if segment in path)
                samples.append(dict(role=owner, segment=segment, actions=[dict(exit_code=0)] * count))
                directory = prepared / 'segments' / segment
                directory.mkdir(parents=True)
                (directory / 'actions.tsv').write_text('step\tcommand\n' + ''.join(f'{i:03}\ttrue\n' for i in range(1, count + 1)))
            cfg = dict(path=backbone, workload=profile, guest_cpu=2, guest_memory_mib=4096,
                       expected_exit_codes={s: [0] * counts[s] for s in backbone},
                       task_id=profile, source_image='local-fixture')
            for name, value in [('config.json', cfg), ('rollouts.json', paths), ('agent-sampling.json', dict(segments=samples))]:
                (prepared / name).write_text(json.dumps(value))
            (self.root / f'configs/platform-{profile}-endpoint-metrics.json').write_text(json.dumps(dict(inputs=profile, replay_policy='fixture')))

    def test_rotating_matrix_is_planned_not_executed(self):
        value = module.plan(self.root)
        self.assertEqual(len(value['runs']), 18)
        self.assertFalse(value['runtime_ready'])
        self.assertEqual(value['business_runs'], 0)
        self.assertTrue(all(r['status'] == 'NOT_RUN' for r in value['runs']))
        for task in value['tasks']:
            self.assertEqual([p['after_backbone_action'] for p in task['checkpoints']], [13, 29])
            self.assertEqual([task['roles'][r]['actions'] for r in module.ROLES], [35, 25, 25, 25, 40, 40, 40])
            self.assertEqual(task['expected']['grpo']['actual_actions'], 230)
            self.assertEqual(task['expected']['bpo']['actual_actions'], 104)
            self.assertEqual(task['expected']['tvcache']['restores'], 6)

    def test_changed_tree_is_rejected(self):
        original = module.read
        def changed(path):
            value = original(path)
            if Path(path).name == 'rollouts.json':
                value = copy.deepcopy(value)
                value['early_1'][0] = 'core_patch'
            return value
        module.read = changed
        try:
            with self.assertRaises(AssertionError):
                module.task_plan(self.root, 'sphinx7590')
        finally:
            module.read = original

    def test_missing_inputs_are_not_marked_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                module.plan(Path(directory))


if __name__ == '__main__':
    unittest.main()
