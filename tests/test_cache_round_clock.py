"""Virtual clock only: no sleeping, services, devices or collectors."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cache_round_clock import CacheRoundClock,finish_ticket,summarize_round

class Fake:
    def __init__(self):self.ns=0;self.stopped=False;self.delay=0
    def now(self):return self.ns
    def wait(self,seconds):
        self.ns+=round(seconds*1e9)+self.delay
        return self.stopped

class ClockTests(unittest.TestCase):
    def test_sessions_join_same_future_tick(self):
        f=Fake();c=CacheRoundClock(now=f.now);f.ns=100
        a=c.wait_next(None,f)
        f.ns=200;b=c.wait_next(None,f)
        self.assertEqual(a['scheduled_ns'],b['scheduled_ns'])
        self.assertIsNone(b['skipped_rounds'])
        self.assertFalse(b['atomic_snapshot'])
    def test_overrun_skips_without_fake_samples(self):
        f=Fake();c=CacheRoundClock(now=f.now)
        a=c.wait_next(None,f);f.ns=2500000000;b=c.wait_next(a['round'],f)
        self.assertEqual(b['round'],3);self.assertEqual(b['skipped_rounds'],2)
    def test_late_dispatch_and_actual_end_retained(self):
        f=Fake();c=CacheRoundClock(now=f.now);f.delay=1200000000
        a=c.wait_next(None,f);r=finish_ticket(a,f.ns,f.ns+100)
        self.assertEqual(r['start_lateness_ns'],1200000000)
        self.assertTrue(r['finished_after_next_tick']);self.assertEqual(r['read_end_ns'],1200000100)
    def test_missing_or_late_session_never_claims_full_snapshot(self):
        f=Fake();c=CacheRoundClock(now=f.now);a=c.wait_next(None,f)
        a=finish_ticket(a,0,50)
        value=summarize_round({'a':a},['a','b'])
        self.assertEqual(value['status'],'INCOMPLETE')
        self.assertEqual(value['missing_sessions'],['b'])
        self.assertIsNone(value['common_window_ns'])
        b=dict(a,dispatch_ns=2000000000,read_start_ns=2000000000,read_end_ns=2000000050,finished_after_next_tick=True)
        value=summarize_round({'a':a,'b':b},['a','b'])
        self.assertEqual(value['late_sessions'],['b'])
        self.assertIsNone(value['common_window_ns']);self.assertFalse(value['atomic_snapshot'])

    def test_stop_returns_no_measurement(self):
        f=Fake();c=CacheRoundClock(now=f.now);f.stopped=True
        self.assertIsNone(c.wait_next(None,f))
    def test_no_duplicate_tick_after_fast_query(self):
        f=Fake();c=CacheRoundClock(now=f.now)
        a=c.wait_next(None,f);b=c.wait_next(a['round'],f)
        self.assertEqual(b['round'],a['round']+1)

if __name__=='__main__':unittest.main()
