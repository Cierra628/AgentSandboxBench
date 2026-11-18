"""Mocked block syscalls/proc only; no real device, cache or sandbox query."""
from contextlib import ExitStack
import errno
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_schedule_cache_layers as m

CAPACITY=64*2**30
STATS=dict(nr_cache=2,nr_dirty=1,nr_writeback=0,nr_evicted=7,nr_recently_evicted=3)


class BlockCachestatTests(unittest.TestCase):
    def probe(self,method='cachestat',*,refs=None,capacity_effect=None,source_effect=None,cache_effect=None,mincore_effect=None,identity_effect=None):
        refs=refs or [dict(path='/fake/'+str(i),device=1,inode=i,rdev=50) for i in (10,11)]
        info=lambda fd:SimpleNamespace(st_dev=1,st_ino=fd,st_rdev=50,st_mode=stat.S_IFBLK)
        with ExitStack() as s:
            s.enter_context(patch.object(m.os,'open',side_effect=list(range(10,10+len(refs)))))
            close=s.enter_context(patch.object(m.os,'close'))
            s.enter_context(patch.object(m.os,'fstat',side_effect=identity_effect or info))
            s.enter_context(patch.object(m,'check_process',side_effect=source_effect))
            s.enter_context(patch.object(m,'capacity',side_effect=capacity_effect,return_value=CAPACITY))
            cache=s.enter_context(patch.object(m,'cachestat',side_effect=cache_effect,return_value=STATS))
            old=s.enter_context(patch.object(m,'mincore_file',side_effect=mincore_effect,
                return_value=dict(stable=True,resident_bytes=4096,device=1,inode=10,rdev=50,mapping_length=CAPACITY)))
            value=m.observe_devices(refs,{},method=method)
        return value,cache,old,close

    def test_full_range_once_per_rdev_and_independent_field(self):
        value,cache,old,close=self.probe()
        self.assertEqual(value['status'],'OBSERVED');self.assertEqual(len(value['objects']),1)
        cache.assert_called_once_with(10,offset=0,length=CAPACITY);old.assert_not_called()
        row=value['objects'][0];self.assertEqual(len(row['aliases']),2)
        self.assertEqual(row['cache_index_bytes'],2*os.sysconf('SC_PAGE_SIZE'))
        self.assertIsNone(row['resident_bytes']);self.assertNotIn('residency',row)
        self.assertIsNone(value['stable_resident_bytes'])
        self.assertEqual(value['method_totals']['mincore']['status'],'NOT_REQUESTED')
        self.assertEqual(row['queries']['cachestat']['cache_counters']['nr_evicted'],7)
        self.assertEqual(row['queries']['cachestat']['status'],'OBSERVED')  # Not TMPFS swap.
        self.assertEqual({c.args[0] for c in close.call_args_list},{10,11})

    def test_default_still_mincore_and_zero_is_measured(self):
        value,cache,old,_=self.probe('mincore')
        cache.assert_not_called();old.assert_called_once()
        self.assertEqual(value['stable_resident_bytes'],4096)
        self.assertIsNone(value['stable_cache_index_bytes'])
        value,cache,_,_=self.probe(cache_effect=lambda *a,**kw:dict(STATS,nr_cache=0))
        self.assertEqual(value['stable_cache_index_bytes'],0)
        self.assertEqual(value['method_totals']['cachestat']['status'],'OBSERVED')

    def test_compare_different_values_preserved_on_same_pinned_object(self):
        calls=[]
        def old(*args,**kw):calls.append('mincore');return dict(stable=True,resident_bytes=4096)
        def cache(*args,**kw):calls.append('cachestat');return STATS
        value,c,o,_=self.probe('compare',mincore_effect=old,cache_effect=cache)
        self.assertEqual(calls,['mincore','cachestat']);self.assertEqual(o.call_count,c.call_count,1)
        self.assertEqual(value['stable_resident_bytes'],4096)
        self.assertEqual(value['stable_cache_index_bytes'],8192)
        row=value['objects'][0]
        self.assertLessEqual(row['queries']['mincore']['end_monotonic_ns'],row['queries']['cachestat']['start_monotonic_ns'])
        self.assertEqual(row['queries']['mincore']['range'],row['queries']['cachestat']['range'])

    def test_permission_unsupported_and_syscall_failure_have_no_fallback(self):
        for error in (PermissionError(errno.EPERM,'denied'),OSError(errno.ENOSYS,'missing'),NotImplementedError('arch')):
            with self.subTest(error=error):
                value,cache,old,_=self.probe(cache_effect=error)
                old.assert_not_called();cache.assert_called_once()
                self.assertEqual(value['status'],'PARTIAL')
                q=value['objects'][0]['queries']['cachestat']
                self.assertEqual(q['status'],'UNKNOWN');self.assertIsNone(q['cache_index_bytes'])
                self.assertIsNone(value['method_totals']['cachestat']['complete_bytes'])

    def test_compare_one_method_failure_does_not_erase_other_observation(self):
        for failing in ('mincore','cachestat'):
            with self.subTest(failing=failing):
                value,c,o,_=self.probe('compare',**({'cache_effect':OSError('failed')} if failing=='cachestat' else {'mincore_effect':OSError('failed')}))
                self.assertEqual(value['status'],'PARTIAL');self.assertEqual(c.call_count,o.call_count,1)
                other='mincore' if failing=='cachestat' else 'cachestat'
                self.assertEqual(value['method_totals'][other]['status'],'OBSERVED')
                self.assertEqual(value['method_totals'][failing]['status'],'PARTIAL')

    def test_alias_capacity_disagreement_rejects_whole_object_before_query(self):
        value,cache,old,close=self.probe(capacity_effect=[CAPACITY,CAPACITY//2])
        cache.assert_not_called();old.assert_not_called()
        self.assertEqual(value['status'],'PARTIAL');self.assertIsNone(value['objects'][0]['cache_index_bytes'])
        self.assertTrue(value['objects'][0]['reference_errors']);self.assertEqual(close.call_count,2)

    def test_pre_or_post_capacity_change_retains_unknown(self):
        refs=[dict(path='/fake/10',device=1,inode=10,rdev=50)]
        for capacities,expected_calls in (([CAPACITY,CAPACITY//2],0),([CAPACITY,CAPACITY,CAPACITY//2],1)):
            value,cache,_,close=self.probe(refs=refs,capacity_effect=capacities)
            self.assertEqual(cache.call_count,expected_calls);self.assertIsNone(value['objects'][0]['cache_index_bytes'])
            self.assertEqual(value['status'],'PARTIAL');close.assert_called_once_with(10)
            if expected_calls:self.assertIn('cache_counters',value['objects'][0]['queries']['cachestat'])

    def test_source_mapping_change_pre_or_post_query_is_unknown(self):
        refs=[dict(path='/fake/10',device=1,inode=10,rdev=50)]
        for effects,expected in (([None,None,ValueError('mapping changed')],0),([None,None,None,ValueError('mapping changed')],1)):
            value,cache,_,_=self.probe(refs=refs,source_effect=effects)
            self.assertEqual(cache.call_count,expected);self.assertIsNone(value['objects'][0]['cache_index_bytes'])
            self.assertEqual(value['status'],'PARTIAL')

    def test_pinned_fd_identity_change_after_query_keeps_raw_not_capacity(self):
        refs=[dict(path='/fake/10',device=1,inode=10,rdev=50)]
        good=SimpleNamespace(st_dev=1,st_ino=10,st_rdev=50,st_mode=stat.S_IFBLK)
        changed=SimpleNamespace(st_dev=1,st_ino=11,st_rdev=50,st_mode=stat.S_IFBLK)
        value,cache,_,close=self.probe(refs=refs,identity_effect=[good,good,changed])
        cache.assert_called_once();close.assert_called_once_with(10)
        q=value['objects'][0]['queries']['cachestat']
        self.assertEqual(q['cache_counters'],STATS);self.assertIsNone(q['cache_index_bytes'])
        self.assertEqual(value['status'],'PARTIAL')

    def test_unstable_mincore_never_enters_success_subtotal(self):
        value,cache,_,_=self.probe('compare',mincore_effect=lambda *a,**kw:dict(stable=False,resident_bytes=4096))
        self.assertEqual(value['status'],'PARTIAL');cache.assert_called_once()
        q=value['objects'][0]['queries']['mincore']
        self.assertEqual(q['residency']['resident_bytes'],4096);self.assertIsNone(q['resident_bytes'])
        self.assertEqual(value['method_totals']['mincore']['observed_subtotal_bytes'],0)
        self.assertIsNone(value['method_totals']['mincore']['complete_bytes'])
        self.assertEqual(value['method_totals']['cachestat']['status'],'OBSERVED')

    def test_phase_costs_are_recorded_on_success_and_failure(self):
        for error in (None,OSError('failed')):
            value,_,_,_=self.probe(cache_effect=error)
            self.assertEqual(set(value['stages']),{'inventory','queries'})
            q=value['objects'][0]['queries']['cachestat'];phases=q['stages']
            self.assertIn('identity_before',phases);self.assertIn('core_query',phases)
            if error is None:self.assertIn('identity_after',phases)
            for phase in [*value['stages'].values(),*phases.values()]:
                self.assertGreaterEqual(phase['end_monotonic_ns'],phase['start_monotonic_ns'])
                self.assertGreaterEqual(phase['cpu_ns'],0)
            self.assertGreaterEqual(q['cpu_ns'],sum(p['cpu_ns'] for p in phases.values()))

    def test_all_ten_objects_are_queried_even_when_empty(self):
        refs=[dict(path='/fake/'+str(i),device=1,inode=i,rdev=100+i) for i in range(10)]
        def info(fd):return SimpleNamespace(st_dev=1,st_ino=fd,st_rdev=100+fd,st_mode=stat.S_IFBLK)
        def cap(fd):return (64 if fd<8 else 4)*2**30
        with patch.object(m,'check_process'),patch.object(m.os,'open',side_effect=list(range(10))),\
             patch.object(m.os,'close') as close,patch.object(m.os,'fstat',side_effect=info),\
             patch.object(m,'capacity',side_effect=cap),patch.object(m,'cachestat',return_value=dict(STATS,nr_cache=0)) as query:
            value=m.observe_devices(refs,{},method='cachestat')
        self.assertEqual(len(value['objects']),10);self.assertEqual(query.call_count,10)
        self.assertEqual([call.kwargs['length'] for call in query.call_args_list],[64*2**30]*8+[4*2**30]*2)
        self.assertEqual(close.call_count,10);self.assertEqual(value['stable_cache_index_bytes'],0)

    def test_invalid_method_rejected_before_any_open(self):
        with patch.object(m.os,'open') as opened:
            with self.assertRaises(ValueError):m.observe_devices([],{},method='auto')
        opened.assert_not_called()


if __name__=='__main__':unittest.main()
