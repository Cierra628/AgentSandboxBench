"""Fake samples/identities/CLI only; no guest or live system interfaces."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import light_memory_round as m
import run_guest_light_memory as g
import run_live_child_calibration as e

class LifecycleTests(unittest.TestCase):
    def test_transition_rounds_do_not_claim_full_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);bindings={r:dict(pid=i,start_ticks='1',sandbox_id=r) for i,r in enumerate('abcdefg',1)}
            def fake(parent,pid,active):
                t=time.monotonic_ns();return dict(start_monotonic_ns=t,end_monotonic_ns=t,cpu_ns=0,vmms=active.copy(),pss_sum_kib=len(active)*10,denominator=len(active))
            with patch.object(m,'sample',side_effect=fake),patch.object(m,'check_vmm'):
                s=m.Sampler(out,bindings,out,lifecycle=True)
                s.transition('a','SAVING');time.sleep(.13)
                s.transition('a','RESTORING')
                with self.assertRaisesRegex(ValueError,'duplicate'):s.activate('a',bindings['b'])
                with self.assertRaisesRegex(ValueError,'did not change'):s.activate('a',bindings['a'])
                s.activate('a',dict(pid=99,start_ticks='2',sandbox_id='restored'));time.sleep(.13);s.stop()
            rows=[json.loads(l) for l in (out/'light-memory.jsonl').read_text().splitlines()]
            gap=[r for r in rows if r['transition_roles']];self.assertTrue(gap)
            self.assertTrue(all(r['denominator']==6 and r['all_roles_pss_sum_kib'] is None for r in gap))
            self.assertEqual(rows[-1]['vmms']['a']['sandbox_id'],'restored');self.assertEqual(rows[-1]['denominator'],7)
            self.assertEqual(bindings['a']['pid'],1)
    def test_restore_budget_and_guard(self):
        self.assertEqual(e.volume_gib(7,restore_one=True),40);self.assertEqual(e.volume_gib(7),24)
        with patch.object(sys,'argv',['entry','--output','/unused','--guests','2','--light-memory','--restore-one']):
            with self.assertRaisesRegex(ValueError,'seven light'):e.main()
    def test_complete_mock_save_restore_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'mount/controller/guest-cache';out.parent.mkdir(parents=True)
            calls=[];changes=[]
            class Sampler:
                def __init__(self,*a,**kw):self.lifecycle=kw['lifecycle']
                def transition(self,role,state):changes.append(state)
                def activate(self,role,vm):changes.append('ACTIVE')
                def stop(self):return dict(status='PASS',samples=1)
            def command(args,**kw):
                calls.append(args);return SimpleNamespace(stdout=b'restored' if args[1]=='start' else b'',stderr=b'',returncode=0,check_returncode=lambda:None)
            def bind(b,sid,out,role):return dict(pid=99999999 if sid!='restored' else 99999998,start_ticks='1',sandbox_id=sid)
            argv=['guest','--container','fake','--service-url','http://unused','--config-home',tmp,'--output',str(out),'--guests','7','--restore-one']
            with patch.object(sys,'argv',argv),patch.object(g,'Binding',return_value=SimpleNamespace(cgroup=Path(tmp))),patch.object(g,'create',side_effect=list('abcdefg')),patch.object(g,'bind_vmm',side_effect=bind),patch.object(g,'Sampler',Sampler),patch.object(g.subprocess,'run',side_effect=command),patch.object(g.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(g.main(),0)
            self.assertEqual(changes,['SAVING','DELETING','RESTORING','ACTIVE'])
            self.assertEqual(sum(c[1]=='delete' for c in calls),8)
            self.assertEqual(sum(c[1]=='start' for c in calls),1)
            r=json.loads((out/'result.json').read_text());self.assertTrue(r['snapshot_removed']);self.assertEqual(r['restore_target']['sandbox_id'],'restored')

if __name__=='__main__':unittest.main()
