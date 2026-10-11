"""Offline paired effect arithmetic, no service access."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_runtime_monitor_pairs import comparison,tool_variability


class PairMetrics(unittest.TestCase):
    def test_uses_within_pair_ratios_for_each_separate_timing_scope(self):
        pairs=[]
        for base in (1,10,100):
            pairs.append(dict(off={k:base for k in ('guest_action_seconds','frozen_replay_seconds','lifecycle_seconds')},
                continuous=dict(guest_action_seconds=base*.9,frozen_replay_seconds=base*1.2,lifecycle_seconds=base*1.3)))
        result=comparison(pairs)
        for key,expected in [('guest_action_seconds',-10),('frozen_replay_seconds',20),('lifecycle_seconds',30)]:
            self.assertAlmostEqual(result[key]['mean_paired_percent'],expected)
            for edge in result[key]['bootstrap_mean_95_percent_interval']:self.assertAlmostEqual(edge,expected)
        self.assertEqual(result,comparison(pairs))

    def test_one_pair_does_not_invent_variance_or_confidence_interval(self):
        data={k:1 for k in ('guest_action_seconds','frozen_replay_seconds','lifecycle_seconds')}
        result=comparison([dict(off=data,continuous=data)])
        self.assertIsNone(result['guest_action_seconds']['off_cv_percent'])
        self.assertIsNone(result['guest_action_seconds']['bootstrap_mean_95_percent_interval'])

    def test_step_variability_retains_slow_pair_and_rejects_changed_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            pairs=[]
            for i in range(2):
                pair={}
                for mode in ('off','continuous'):
                    raw=Path(temp)/f'{i}-{mode}';directory=raw/'rollouts/backbone/segment';directory.mkdir(parents=True)
                    (directory/'actions.tsv').write_text('step\tsha256\tcommand\n001\ta\ttrue\n002\tb\tfalse\n')
                    durations=[1,8 if i==1 and mode=='off' else 2]
                    (directory/'steps.tsv').write_text('step\tduration_ns\n'+''.join(f'{j:03}\t{v*10**9}\n' for j,v in enumerate(durations,1)))
                    pair[mode]=dict(raw=str(raw),configuration=dict(path=['segment']))
                pairs.append(pair)
            result=tool_variability(pairs)
            slow=result['largest_range_steps'][0]
            self.assertEqual(slow['step'],'002');self.assertEqual(slow['off']['seconds'],[2,8])
            self.assertEqual(slow['paired_delta_seconds'],[0,-6]);self.assertEqual(len(result['steps']),2)
            manifest=Path(pairs[-1]['continuous']['raw'])/'rollouts/backbone/segment/actions.tsv'
            manifest.write_text(manifest.read_text().replace('002\tb\t','002\tchanged\t'))
            with self.assertRaises(AssertionError):tool_variability(pairs)


if __name__=='__main__':unittest.main()
