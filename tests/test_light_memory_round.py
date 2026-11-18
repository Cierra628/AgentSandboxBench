"""Fake metadata, clocks and CLI only. Never starts services or accesses devices."""
import contextlib
import io
import json
import itertools
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import light_memory_round as light
import run_guest_light_memory as guest
import run_live_child_calibration as entry

class LightTests(unittest.TestCase):
    def bindings(self):return {r:dict(pid=i,start_ticks='42',boot_id='boot',sandbox_id=r) for r,i in [('a',1),('b',2)]}
    def test_round_retains_individual_windows_and_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);v=dict(start_ticks='42',pss_kib=1024,pss_read_start_monotonic_ns=10,pss_read_end_monotonic_ns=20)
            with patch.object(light,'check_vmm'),patch.object(light,'read',return_value=v) as read,patch.object(light,'counters',return_value=dict(current=100,file=20,anon=70,kernel=10,shmem=2)):
                row=light.sample(parent,parent.stat().st_ino,self.bindings())
            self.assertEqual(row['denominator'],2);self.assertEqual(row['pss_sum_kib'],2048)
            self.assertEqual(read.call_count,2);self.assertTrue(read.call_args.kwargs['pss_timestamps'])
            self.assertEqual(row['parent_counters']['current'],100)
            self.assertLessEqual(row['vmms']['a']['end_monotonic_ns'],row['vmms']['b']['start_monotonic_ns'])
    def test_identity_change_fails_not_partial_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)
            with patch.object(light,'check_vmm',side_effect=ValueError('changed')),patch.object(light,'read') as read:
                with self.assertRaises(ValueError):light.sample(parent,parent.stat().st_ino,self.bindings())
                read.assert_not_called()
    def test_sampler_failure_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with patch.object(light,'sample',side_effect=ValueError('missing PSS')):
                with self.assertRaisesRegex(RuntimeError,'missing PSS'):light.Sampler(out,self.bindings(),out)
    def test_seven_is_explicit_and_has_own_budget(self):
        self.assertEqual(entry.volume_gib(1),12);self.assertEqual(entry.volume_gib(2),16)
        self.assertEqual(entry.volume_gib(7),24)
        with patch.object(sys,'argv',['entry','--output','/unused','--guests','7']),patch.object(entry.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'require light'):entry.main()
            run.assert_not_called()
        self.assertEqual(guest.roles_for(7),tuple('private-'+c for c in 'abcdefg'))

    def test_flag_rejects_heavy_before_launch(self):
        argv=['entry','--output','/unused','--guests','2','--light-memory','--ram-page-evidence']
        with patch.object(sys,'argv',argv),patch.object(entry.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'no heavy'):entry.main()
            run.assert_not_called()
    def test_two_guest_fake_lifecycle_and_cleanup(self):
        for failed,count in itertools.product(('none','write','prepare'),(2,7)):
            with self.subTest(failed=failed,count=count),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'mount/controller/guest-cache';out.parent.mkdir(parents=True)
                argv=['guest','--container','fake','--service-url','http://unused','--config-home',tmp,'--output',str(out),'--guests',str(count)]
                calls=[];prepared=set()
                def call(args,**kw):
                    calls.append(args)
                    if 'mkdir' in args:
                        if failed=='prepare':raise RuntimeError('directory preparation failed')
                        self.assertEqual(args[-3:],['mkdir','-p','/workspace']);prepared.add(args[2])
                    if 'python3' in args:
                        self.assertIn(args[2],prepared,'write before directory preparation')
                        if failed=='write':raise RuntimeError('synthetic workload failed')
                    return SimpleNamespace(stdout=b'',stderr=b'',returncode=0,check_returncode=lambda:None)
                class Sampler:
                    def __init__(self,*args):pass
                    def stop(self):return dict(status='PASS',samples=10)
                with patch.object(sys,'argv',argv),patch.object(guest,'Binding',return_value=SimpleNamespace(cgroup=Path(tmp))),patch.object(guest,'create',side_effect=[chr(ord('a')+i) for i in range(count)]),patch.object(guest,'bind_vmm',return_value=dict(pid=123)),patch.object(guest,'Sampler',Sampler),patch.object(guest.subprocess,'run',side_effect=call),patch.object(guest.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
                    rc=guest.main()
                self.assertEqual(rc,0 if failed=='none' else 1)
                self.assertEqual(sum('delete' in c for c in calls),1 if failed=='prepare' else count)
                value=json.loads((out/'result.json').read_text());self.assertFalse(value['heavy_page_scans'])
                self.assertTrue(all(g['vm_removed'] for g in value['guests'].values()))
                if failed=='prepare':
                    self.assertEqual(value['failed_stage'],'prepare-directory-private-a')
                    self.assertFalse(any('python3' in c for c in calls))
                else:self.assertEqual(prepared,{chr(ord('a')+i) for i in range(count)})

if __name__=='__main__':unittest.main()
