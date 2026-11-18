"""Offline integration checks: no VM, BPF, service or real devices."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import collect_snapshot_child
import report_snapshot_cache_pair as pair
from provider_slot_cache import account_observed_provider
import test_provider_segments as fixtures


class RealProviderPairTests(unittest.TestCase):
    def test_child_provider_and_batch_are_explicit_and_close_on_cancel(self):
        for flags,options in [([],{}),(['--provider-cache','--batch-lookup'],
                                     dict(provider_cache=True,batch_lookup=True)),
                                     (['--provider-cache','--batch-lookup','--expected-guest-kernel','6.1.175-asb-cache-v3-reclaim'],
                                      dict(provider_cache=True,batch_lookup=True,expected_kernel='6.1.175-asb-cache-v3-reclaim'))]:
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as name:
                root=Path(name);d=root/'live-cache/child-a';d.mkdir(parents=True);(d/'stop').touch()
                live=Mock();argv=['child','--output',name,'--container','unused',
                                 '--config-home',name,'--side','child-a',*flags]
                with patch.object(sys,'argv',argv),patch.object(collect_snapshot_child,'LiveTaskCache',return_value=live) as factory, \
                     patch.object(collect_snapshot_child.signal,'signal'),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(collect_snapshot_child.main(),1)
                self.assertEqual(factory.call_args.kwargs,dict(session='child-a',lower_cache=True,**options))
                live.start.assert_not_called();live.close.assert_called_once()

    def test_child_batch_without_provider_fails_before_capture(self):
        argv=['child','--output','/unused','--container','unused','--config-home','/unused',
              '--side','child-a','--batch-lookup']
        with patch.object(sys,'argv',argv),patch.object(collect_snapshot_child,'LiveTaskCache') as factory, \
             contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            collect_snapshot_child.main()
        factory.assert_not_called()

    def write_point(self,d,args):
        d.mkdir(parents=True,exist_ok=True)
        for suffix,value in [('-before.bin',args[0]),('-after.bin',args[1])]:
            (d/('pair-0000'+suffix)).write_bytes(value)
        for suffix,rows in zip(['-host.bin','-backend.bin','-lower.bin'],args[2:5],strict=True):
            (d/('pair-0000'+suffix)).write_bytes(b''.join(struct.pack('<4Q',*r) for r in rows))
        (d/'pair-0000-provider.json').write_text(json.dumps(dict(first=args[5],last=args[6])))

    def test_independent_set_includes_provider_and_excludes_changed_or_old_copies(self):
        for mode in ['current','changed','old']:
            args=fixtures.ProviderSegmentTests().data(length=512)
            if mode=='changed':args[-1]['rows'][0]['pagemap'][0] ^= 1
            if mode=='old':args[2][0]=(1,(1<<63)|30,(1<<63)|30,(1<<63)|30)
            values,expected=account_observed_provider(*args)
            three={20,40}|({30} if mode=='old' else set())
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as name:
                d=Path(name);self.write_point(d,args)
                with patch.object(pair,'check_point',return_value=({},three)):
                    self.assertEqual(pair.physical_set(d,dict(point=0,host_confirmed_cache_bytes=4096,**values)),expected)

    def test_real_pair_report_deduplicates_provider_aliases_across_guests(self):
        with tempfile.TemporaryDirectory() as name:
            source=Path(name)/'source';source.mkdir()
            (source/'result.json').write_text(json.dumps(dict(status='PASS',cleanup=dict(done=True),
                task_id='prettier__prettier-6604',cache_pair=True,provider_cache=True,checks=dict(tasks='PASS'),
                phases=[dict(name='real-task-branches',start_ns=0,end_ns=1000)])))
            groups={};physical={}
            for side,extra in [('child-a',50),('child-b',60)]:
                d=source/'live-cache'/side;d.mkdir(parents=True)
                (d/'result.json').write_text(json.dumps(dict(status='PASS',host_thread_total_cpu_ns=20)))
                (d/'guest-result.json').write_text(json.dumps(dict(publication_cpu_ns=1)))
                q=dict(returncode=0,tracer_cpu_ns=1,queries=[dict(missing_kernel_results=0)])
                (d/'snapshot-query-result.json').write_text(json.dumps(q));(d/'lower-query').mkdir()
                (d/'lower-query/snapshot-query-result.json').write_text(json.dumps(q))
                groups[side]=[dict(point=0,timestamp_ns=10,host_elapsed_ns=100,guest_cached_bytes=8192,
                    host_confirmed_cache_bytes=4096,host_four_segment_current_union_bytes=3*4096,host_four_segment_unconfirmed_guest_capacity_bytes=4096,
                    host_nonresident_pages=0,host_swapped_pages=0,unconfirmed_after_pages=1)]
                physical[side]={20,40,extra}
            breakdown=dict(sessions={s:dict(status='PASS',points=[]) for s in groups})
            with patch.object(pair,'recount',side_effect=lambda d:groups[d.name]), \
                 patch.object(pair,'physical_set',side_effect=lambda d,p:physical[d.name]), \
                 patch('provider_residency_report.build_breakdown',return_value=breakdown), \
                 patch('provider_residency_report.write_breakdown'):
                result=pair.report(source,Path(name)/'report')
            p=result['pairs'][0]
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(p['host_sum_bytes'],6*4096)
            self.assertEqual(p['host_union_bytes'],4*4096)
            self.assertEqual(p['shared_bytes'],2*4096)
            self.assertIn('raw_stat',result);self.assertNotIn('raw_sha256',result)
            self.assertIn('provider PFN union',result['scope'])

if __name__=='__main__':unittest.main()
