"""Mock counters and identities only; never start guests or live samplers."""
import json
from pathlib import Path
import sys,tempfile,unittest,time
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import light_memory_round as m
from light_prettier_rollout import options

class RolloutTests(unittest.TestCase):
    def test_planned_empty_then_birth_save_and_same_source_resume(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            with patch.object(m.threading.Thread,'start'),patch.object(m.threading.Event,'wait',return_value=True),patch.object(m,'check_vmm'):
                s=m.Sampler(root,{},root,lifecycle=True,expected_roles=['backbone','child'])
                self.assertEqual(set(s.transitions),{'backbone','child'})
                vm=dict(pid=4,start_ticks='1',sandbox_id='root')
                s.activate('backbone',vm)
                s.transition('backbone','SAVING')
                with self.assertRaisesRegex(ValueError,'identity changed'):s.resume_same('backbone',dict(vm,pid=9))
                s.resume_same('backbone',vm)
                self.assertEqual(s.bindings['backbone'],vm)
                with self.assertRaisesRegex(ValueError,'duplicate'):s.activate('child',vm)
                s.activate('child',dict(pid=5,start_ticks='2',sandbox_id='child'))
                self.assertFalse(s.transitions)
                self.assertEqual([e['state'] for e in json.loads((root/'identity-events.json').read_text())],['ACTIVE','SAVING','RESUMED_SOURCE','ACTIVE'])
    def test_partial_rounds_are_never_full_totals(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            def fake(parent,pid,active):
                now=time.monotonic_ns()
                return dict(start_monotonic_ns=now,end_monotonic_ns=now,cpu_ns=0,vmms=active.copy(),pss_sum_kib=len(active)*10,denominator=len(active))
            with patch.object(m,'sample',side_effect=fake),patch.object(m,'check_vmm'):
                s=m.Sampler(root,{},root,lifecycle=True,expected_roles=['backbone','child'])
                try:
                    s.activate('backbone',dict(pid=4,start_ticks='1',sandbox_id='root'));time.sleep(.12)
                    s.transition('backbone','SAVING');time.sleep(.12)
                    s.resume_same('backbone',dict(pid=4,start_ticks='1',sandbox_id='root'))
                    s.activate('child',dict(pid=5,start_ticks='2',sandbox_id='child'));time.sleep(.12)
                finally:s.stop()
            rows=[json.loads(line) for line in (root/'light-memory.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['denominator'],0)
            self.assertTrue(all(r['all_roles_pss_sum_kib'] is None for r in rows if r['transition_roles']))
            self.assertEqual(rows[-1]['all_roles_pss_sum_kib'],20)

    def test_empty_default_still_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError,'empty'):m.Sampler(Path(t),{},Path(t))
    def test_explicit_bpo_only(self):
        self.assertFalse(options(SimpleNamespace()))
        self.assertTrue(options(SimpleNamespace(light_rollout=True,schedule='bpo',schedule_smoke_metrics=True)))
        with self.assertRaisesRegex(ValueError,'complete BPO'):options(SimpleNamespace(light_rollout=True,schedule='grpo',schedule_smoke_metrics=True))
        with self.assertRaisesRegex(ValueError,'excludes'):options(SimpleNamespace(light_rollout=True,schedule='bpo',schedule_smoke_metrics=True,schedule_cache_layers=True))

if __name__=='__main__':unittest.main()
