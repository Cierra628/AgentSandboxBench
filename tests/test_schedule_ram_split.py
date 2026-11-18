"""Mocked devices and source checks only; no real process, device or cache reads."""
from contextlib import ExitStack
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_schedule_cache_layers as m
from schedule_smoke_metrics import options

CAPACITY=4*2**30


class RamSplitTests(unittest.TestCase):
    def refs(self):
        common=dict(device=1,inode=2,rdev=50)
        return [dict(common,path='/proc/100/fd/9',process=100,source='open_block_fd',region=None,role=None),
                dict(common,path='/proc/101/fd/13',process=101,source='open_block_fd',region=None,role='late_1'),
                dict(common,path='/proc/101/map_files/1000-2000',process=101,source='vmm_ram_mapping',
                     region=dict(start=4096,end=8192),role='late_1')]

    def probe(self,*,change=None,refs=None,enabled=True,zero=False):
        refs=refs if refs is not None else self.refs();counts={};opened=[];events=[]
        def check(ref,*args):
            path=ref['path'];counts[path]=counts.get(path,0)+1;n=counts[path]
            if (change=='mapping_query' and ref['source']=='vmm_ram_mapping' and n==3 or
                change=='mapping_inventory' and ref['source']=='vmm_ram_mapping' and n==1):
                e=ValueError('source mapping identity changed');e.failed_reference=ref
                e.mapping_diagnostic=dict(reason='EXACT_RANGE_MISSING',overlapping_regions=[dict(device=0,inode=0)])
                raise e
            if ref['source']=='open_block_fd' and ref['process']==100 and (
                    change=='anchor_pid_before' and n==3 or change=='anchor_pid_after' and n==6 or
                    change=='anchor_inventory_denied' and n==1):
                raise ValueError('source process identity/cgroup changed')
        def open_fd(*args):
            fd=10+len(opened);opened.append((fd,args[0]));return fd
        def fstat(fd):
            return SimpleNamespace(st_dev=1,st_ino=99 if change=='anchor_inode' and fd==13 else 2,
                st_rdev=50,st_mode=stat.S_IFBLK)
        def capacity(fd):return CAPACITY*2 if change=='capacity_conflict' and fd==11 else CAPACITY
        def cache(fd,**kw):
            events.append(dict(fd=fd,**kw))
            if change=='permission':raise PermissionError('denied')
            return dict(nr_cache=0 if zero else len(events)+1,nr_dirty=0,nr_writeback=0,nr_evicted=0,nr_recently_evicted=0)
        with ExitStack() as s:
            s.enter_context(patch.object(m,'check_process',side_effect=check))
            s.enter_context(patch.object(m.os,'open',side_effect=open_fd))
            close=s.enter_context(patch.object(m.os,'close'))
            s.enter_context(patch.object(m.os,'fstat',side_effect=fstat))
            s.enter_context(patch.object(m,'capacity',side_effect=capacity))
            s.enter_context(patch.object(m,'cachestat',side_effect=cache))
            old=s.enter_context(patch.object(m,'mincore_file'))
            result=m.observe_devices(refs,{100:{},101:{}},method='cachestat',split_ram=enabled)
        old.assert_not_called()
        self.assertEqual(sorted(c.args[0] for c in close.call_args_list),sorted(fd for fd,_ in opened))
        return result,events,opened

    def test_mapping_change_keeps_fixed_readout_but_not_old_strict_value(self):
        for change in ('mapping_query','mapping_inventory'):
            with self.subTest(change=change):
                value,calls,_=self.probe(change=change);row=value['objects'][0];fixed=row['fixed_ram_device']
                self.assertEqual(len(calls),1)
                self.assertEqual(fixed['anchor']['process'],100)
                self.assertEqual(fixed['query']['status'],'OBSERVED')
                self.assertEqual(fixed['query']['cache_index_bytes'],8192)
                self.assertEqual(fixed['source_relationships']['status'],'CHANGED')
                self.assertIsNone(row['cache_index_bytes']);self.assertEqual(value['status'],'PARTIAL')
                self.assertIsNone(value['method_totals']['cachestat']['complete_bytes'])
                self.assertEqual(value['fixed_ram_totals']['complete_bytes'],8192)
                self.assertEqual(calls[0]['offset'],0);self.assertEqual(calls[0]['length'],CAPACITY)

    def test_normal_queries_are_two_planned_windows_not_one_retried_value(self):
        value,calls,_=self.probe();row=value['objects'][0]
        self.assertEqual(len(calls),2)
        self.assertEqual(row['fixed_ram_device']['query']['cache_index_bytes'],8192)
        self.assertEqual(row['cache_index_bytes'],12288)
        self.assertEqual(row['fixed_ram_device']['source_relationships']['status'],'OBSERVED')
        self.assertLessEqual(row['fixed_ram_device']['end_monotonic_ns'],row['queries']['cachestat']['start_monotonic_ns'])

    def test_default_never_adds_a_fixed_query_or_new_fields(self):
        value,calls,_=self.probe(enabled=False)
        self.assertEqual(len(calls),1);self.assertNotIn('fixed_ram_totals',value)
        self.assertNotIn('fixed_ram_device',value['objects'][0])

    def test_missing_anchor_not_replaced_by_mapping_reference(self):
        value,calls,_=self.probe(refs=self.refs()[2:]);row=value['objects'][0]
        self.assertEqual(len(calls),1)  # Old strict query only, never fills the new field.
        self.assertEqual(row['queries']['cachestat']['status'],'OBSERVED')
        self.assertIsNone(row['fixed_ram_device']['query']['cache_index_bytes'])
        self.assertIsNone(row['fixed_ram_device']['anchor'])

    def test_anchor_identity_or_pid_failure_never_falls_back_to_other_good_fd(self):
        for change in ('anchor_inode','anchor_pid_before','anchor_pid_after'):
            with self.subTest(change=change):
                value,calls,_=self.probe(change=change);fixed=value['objects'][0]['fixed_ram_device']
                self.assertEqual(fixed['query']['status'],'UNKNOWN')
                self.assertIsNone(fixed['query']['cache_index_bytes'])
                self.assertEqual(fixed['anchor']['process'],100)
                self.assertIsNone(value['fixed_ram_totals']['complete_bytes'])
                if change=='anchor_pid_after':
                    self.assertIn('cache_counters',fixed['query']);self.assertEqual(len(calls),2)
                else:self.assertEqual(len(calls),1)

    def test_inventory_denial_and_capacity_conflict_block_new_query(self):
        for change in ('anchor_inventory_denied','capacity_conflict'):
            with self.subTest(change=change):
                value,calls,_=self.probe(change=change)
                self.assertFalse(calls);self.assertEqual(value['fixed_ram_totals']['status'],'PARTIAL')
                self.assertIsNone(value['fixed_ram_totals']['complete_bytes'])

    def test_cache_failure_does_not_falsely_assert_mapping_changed(self):
        value,calls,_=self.probe(change='permission');fixed=value['objects'][0]['fixed_ram_device']
        self.assertEqual(len(calls),2)  # One per explicitly different observation; no retry.
        self.assertEqual(fixed['source_relationships']['status'],'UNKNOWN')
        self.assertTrue(fixed['source_relationships']['before_complete'])
        self.assertFalse(fixed['source_relationships']['after_complete'])
        self.assertIsNone(fixed['query']['cache_index_bytes'])

    def test_real_zero_and_absent_ram_are_distinct(self):
        value,_,_=self.probe(zero=True)
        self.assertEqual(value['fixed_ram_totals']['complete_bytes'],0)
        value,calls,_=self.probe(refs=self.refs()[:2])
        self.assertEqual(value['fixed_ram_totals']['status'],'NOT_APPLICABLE')
        self.assertIsNone(value['fixed_ram_totals']['observed_subtotal_bytes'])
        self.assertEqual(len(calls),1)

    def test_duplicate_aliases_do_not_duplicate_device_observation(self):
        refs=self.refs();refs.append(dict(refs[-1],path='/proc/101/map_files/2000-3000'))
        value,calls,_=self.probe(refs=refs)
        self.assertEqual(len(calls),2);self.assertEqual(value['fixed_ram_totals']['observed_objects'],1)

    def test_invalid_mode_is_rejected_before_io(self):
        for method in ('mincore','compare'):
            with self.subTest(method=method),patch.object(m.os,'open') as opened:
                with self.assertRaises(ValueError):m.observe_devices([],{},method=method,split_ram=True)
                opened.assert_not_called()

    def test_split_option_requires_explicit_cache_mode_in_both_entries(self):
        for native in (False,True):
            args=dict(schedule_smoke_metrics=True,schedule_expanded_files=True,schedule_cache_layers=True,
                schedule_split_ram_cache=True,schedule_block_cache_method='cachestat')
            if native:args.update(prettier6604_task='task',prettier6604_export='export',immutable_cache_seed='cache')
            self.assertTrue(options(SimpleNamespace(**args),native=native))
            for method in (None,'mincore','compare'):
                with self.assertRaises(ValueError):options(SimpleNamespace(**dict(args,schedule_block_cache_method=method)),native=native)

    def test_explicit_layer_option_reaches_device_collector(self):
        with patch.object(m,'counters',return_value={}),patch.object(m,'pids',return_value=[]), \
             patch.object(m,'observe_devices',return_value=dict(status='OBSERVED')) as devices, \
             patch.object(m,'deleted_tmpfs',return_value=dict(status='NOT_REQUESTED')), \
             patch.object(m,'observe_loop',return_value=dict(status='OBSERVED')):
            value=m.observe_cache_layers(Path('/fake/mount/env'),{}, {},
                dict(process_identities={},special_block_references=[]),Path('/fake/group'),
                block_method='cachestat',split_ram=True)
        devices.assert_called_once_with([],{},method='cachestat',split_ram=True)
        self.assertEqual(value['status'],'OBSERVED')


if __name__=='__main__':unittest.main()
