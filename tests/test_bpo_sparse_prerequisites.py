"""No services/VM/devices: real point loop with a virtual clock and fake page reads."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cache_round_clock import CacheRoundClock
from kernel_cache_metadata import PRESENT
from live_task_cache import LiveTaskCache
from rollout_cache_sessions import RolloutCacheSessions
from light_prettier_rollout import diagnostic_kernel_args
from provider_slot_cache import ProviderCache
from test_kernel_cache_metadata import dump
from test_rollout_cache_sessions import binding


class VirtualTime:
    ns=0
    def now(self):return self.ns
    def is_set(self):return False
    def wait(self,delay):self.ns+=round(delay*1e9);return False


class Windows(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.time=VirtualTime();self.calls=[];self.fail=None
        clock=CacheRoundClock(now=self.time.now)
        test=self
        class Reader(LiveTaskCache):
            def __init__(self,out,container,cli,*,session_name,session,round_clock,event_windows,**kw):
                self.directory=out/session_name;self.directory.mkdir();self.round_clock=round_clock
                self.event_windows=event_windows;self.stop_event=test.time;self.samples=[]
                self.backend=self.lower_query=self.provider=self.error=None
                self.ram_page_evidence=False;self.raw_bytes=0;self.capture=kw.get('capture',Mock());self.name=session_name
            def start(self,sid):self.sid=sid
            def close(self):test.calls.append(('close',self.name))
            def _snapshot(self):
                test.calls.append(('publication',self.name))
                if test.fail==self.name and self.samples and test.calls.count(('publication',self.name))==4:
                    raise RuntimeError('second publication failed')
                return dump([(1,20)]),{'publication':len(test.calls)}
            def _bulk(self):
                test.calls.append(('pagemap',self.name));return [({'guest':0,'size':8192},np.array([0,PRESENT|21],dtype='<u8'))]
        with patch('cache_round_clock.CacheRoundClock',return_value=clock):
            self.s=RolloutCacheSessions(self.root,'none',None,factory=Reader,event_windows=True)
        p=patch('live_task_cache.time.monotonic_ns',self.time.now);p.start();self.addCleanup(p.stop)
    def test_windows_clock_pair_and_save_gap(self):
        s=self.s;s.prepare('backbone',restored=False);self.time.ns+=4500000000
        self.assertEqual(self.calls,[]);s.register('backbone',binding())
        self.assertEqual([k for k,_ in self.calls],['publication','pagemap','pagemap','publication','pagemap'])
        for suff in ['before','after','host']:self.assertTrue((self.root/f'backbone-g1/pair-0000-{suff}.bin').is_file())
        self.time.ns+=10000000000;self.assertEqual(len(self.calls),5)
        s.saving('backbone');self.assertEqual(self.calls[-1],('close','backbone-g1'));n=len(self.calls)
        self.time.ns+=7000000000;self.assertEqual(len(self.calls),n)
        s.saved('backbone',binding());s.stop();rs=[e for e in s.events if e['event']=='WINDOW_CLOSED'];self.assertEqual([e['boundary'] for e in rs],['after_create','before_save','after_save','endpoint'])
        self.assertEqual([e['shared_round']['scheduled_ns'] for e in rs],[r*10**9 for r in (5,15,22,23)])
        self.assertTrue(all(e['shared_round']['period_ns']==10**9 for e in rs));self.assertEqual(sum(k=='pagemap' for k,_ in self.calls),12)
    def test_failed_window_other_role_continues_and_missing_registration(self):
        s=self.s;s.prepare('backbone',restored=False);s.register('backbone',binding());self.fail='backbone-g1'
        s.prepare('early_1',restored=True);s.register('early_1',binding(2));s.prepare('late_1',restored=True)
        s.saving('absent');self.assertEqual(s.stop()['status'],'UNKNOWN')
        closed={e['role']:e for e in s.events if e['event']=='WINDOW_CLOSED'}
        for role in ['backbone','late_1','absent']:
            self.assertEqual(closed[role]['status'],'UNKNOWN');self.assertNotIn('shared_round',closed[role])
        self.assertEqual(closed['early_1']['status'],'OBSERVED');self.assertFalse((self.root/'backbone-g1/pair-0001-after.bin').exists())
    def test_slow_window_keeps_original_deadline(self):
        s=self.s;s.prepare('backbone',restored=False);reader=s.roles['backbone']['collector'];bulk=reader._bulk
        def slow_read():
            self.time.ns+=400_000_000
            return bulk()
        reader._bulk=slow_read;s.register('backbone',binding())
        ticket=s.events[-1]['shared_round'];self.assertEqual(ticket['elapsed_ns'],1_200_000_000)
        self.assertTrue(ticket['finished_after_next_tick']);self.assertEqual(ticket['period_ns'],10**9)

    def test_real_start_no_worker_and_wrong_guest_release_rejected(self):
        obj=LiveTaskCache.__new__(LiveTaskCache);obj.capture=Mock();obj.directory=self.root
        (self.root/'kvm-slots.tsv').write_text('fixture');obj.capture.directory=self.root
        obj.capture.bind.return_value=({}, {'slots':[]});obj._check_rollout_binding=Mock()
        obj._start_snapshot_queries=Mock();obj.file_cache_path=None;obj.session_name='test'
        obj.expected_kernel='6.1.175-asb-cache-v3';obj.event_windows=True
        data=dict(kernel='ordinary', iomem='', export_pfns=[]);obj.cli=lambda *a,**kw:json.dumps(data)
        with patch('live_task_cache.threading.Thread') as worker, patch('live_task_cache.identity',return_value=MagicMock()), patch('live_task_cache.shutil.copy2'):
            with self.assertRaisesRegex(ValueError,'ordinary'):obj.start('fake')
            data['kernel']=obj.expected_kernel;obj.start('fake');worker.assert_not_called()
            obj.event_windows=False;obj.start('fake');worker.return_value.start.assert_called_once()

    def test_sparse_provider_teardown_does_not_query(self):
        provider=ProviderCache.__new__(ProviderCache);provider.snapshot=Mock();fd=Mock();provider.slots=[('x',fd,None)];provider.pm=Mock()
        provider.close(final_snapshot=False);provider.snapshot.assert_not_called();fd.close.assert_called_once();provider.pm.close.assert_called_once()

class Kernel(unittest.TestCase):
    def test_optional_missing_invalid_version_and_valid(self):
        self.assertEqual(diagnostic_kernel_args(None),[])
        with tempfile.TemporaryDirectory() as t,patch('platform_tasks.ROOT',Path(t)):
            base=Path(t)/'.artifacts';base.mkdir();p=base/'kernel'
            with self.assertRaisesRegex(ValueError,'missing'):diagnostic_kernel_args(p)
            p.write_bytes(b'ordinary');
            with self.assertRaisesRegex(ValueError,'ELF'):diagnostic_kernel_args(p)
            p.write_bytes(b'\x7fELFLinux version 6.1.175-stock ')
            with self.assertRaisesRegex(ValueError,'6.1.175-asb-cache-v3'):diagnostic_kernel_args(p)
            p.write_bytes(b'\x7fELFLinux version 6.1.175-asb-cache-v3 ')
            self.assertEqual(diagnostic_kernel_args(p),['--diagnostic-guest-kernel',str(p)])
