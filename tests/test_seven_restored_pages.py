import sys,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from seven_restored_pages import DeviceRound,ROLES
from run_live_child_calibration import volume_gib
from snapshot_cache_residency import _probe_program
class SevenTests(unittest.TestCase):
    def test_budget_opt_in_only(self):
        self.assertEqual(volume_gib(2,cache_sessions=True),24)
        self.assertEqual(volume_gib(7),24)
        self.assertEqual(volume_gib(7,cache_sessions=True,seven_restored=True),64)
        with self.assertRaises(ValueError):volume_gib(7,cache_sessions=True)
    def test_same_two_snapshots_across_all_readers_and_failed_branch(self):
        provider=Mock();provider.snapshot.side_effect=[object(),object()]
        group=DeviceRound(provider,ROLES)
        def worker(r):
            if r==ROLES[-1]:group.drop(r);return
            return [group.frame(r,i) for i in (0,1)]
        with ThreadPoolExecutor(7) as pool:results=list(pool.map(worker,ROLES))
        self.assertEqual(results[:-1],[group.frames]*6)
        self.assertEqual(provider.snapshot.call_count,2)
    def test_dispatch_counts_do_not_change_routing_or_attachments(self):
        old=_probe_program(123);new=_probe_program(123,dispatch_counts=True)
        self.assertNotIn('@nent',old);self.assertNotIn('@nexit',old)
        self.assertEqual(old.count('tracepoint:'),new.count('tracepoint:'))
        self.assertEqual(old.split('kprobe:')[1].split('END {')[0],new.split('kprobe:')[1].split('END {')[0])
    def test_parallel_restore_before_postwork_and_return_receipt(self):
        import threading,tempfile
        from seven_restored_pages import restore_parallel
        fire=threading.Barrier(7);ids={};errors={}
        def cli(label,*args,timeout=60):
            if label.startswith('restore-'):
                fire.wait(timeout=3);return label
        with tempfile.TemporaryDirectory() as d:
            import json
            end=restore_parallel(Path(d),cli,'snapshot',ids,Mock(),errors,{r:Mock() for r in ROLES})
            timeline=json.loads((Path(d)/'parallel-restores.json').read_text())
        self.assertEqual(set(ids),set(ROLES));self.assertFalse(errors)
        self.assertLess(max(v['start_ns'] for v in timeline.values()),min(v['return_ns'] for v in timeline.values()))
        self.assertEqual(end,max(v['return_ns'] for v in timeline.values()))
    def test_adaptive_stops_and_actual_read_clock(self):
        from seven_restored_pages import stop_reason
        from cache_round_clock import finish_ticket
        self.assertEqual(stop_reason(0,[9999]+[37352]*6,9,100),'first_below_10000')
        self.assertEqual(stop_reason(0,[37352]*4,9,100),'configuration_failure')
        self.assertIsNone(stop_reason(0,[10000]*7,9,100))
        self.assertIsNone(stop_reason(1,[982]*7,9,100))
        self.assertEqual(stop_reason(5,[15000]*7,100,100),'120_seconds')
        self.assertEqual(stop_reason(59,[15000]*7,98,100),'60_rounds')
        t=finish_ticket({'round':0,'period_ns':None},20,32)
        self.assertEqual(t['elapsed_ns'],12);self.assertNotIn('finished_after_next_tick',t)
