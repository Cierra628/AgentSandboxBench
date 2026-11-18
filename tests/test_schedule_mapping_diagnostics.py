"""Synthetic proc/maps and mocked block syscalls only; no live target or cache reads.

RAM alias fields follow the saved BPO shape, with synthetic PID, address and inode.
These cases do not reconstruct the unknown mapping change in that actual run.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_deleted_backing as h
import host_schedule_cache_layers as m


class MappingDiagnosticTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.proc=Path(directory.name)/'proc';self.root=self.proc/'101';self.root.mkdir(parents=True)
        (self.root/'stat').write_text('101 (fixture) '+' '.join(['S']+['0']*18+['123']))
        (self.root/'cgroup').write_text('0::/fixture\n')
        self.region=dict(pid=101,start_ticks='123',start=0x1000,end=0x4000,permissions='rw-p',offset=3*2**30,
            device=1,inode=20,mapping_path='/dev/ublkb77')
        self.ref=dict(path='/fake/alias',device=1,inode=20,rdev=50,process=101,role='late_3',
            source='vmm_ram_mapping',region=self.region)
        self.capacity=4*2**30
        self.stats=dict(nr_cache=2,nr_dirty=0,nr_writeback=0,nr_evicted=0,nr_recently_evicted=0)
        (self.root/'maps').write_text(self.line(self.region))

    def line(self,r):
        return (f"{r['start']:x}-{r['end']:x} {r['permissions']} {r['offset']:x} "
                f"{os.major(r['device']):x}:{os.minor(r['device']):x} {r['inode']} {r['mapping_path']}\n")

    @contextmanager
    def maps_reads(self,changed_reads=(),*,original=None):
        read=Path.read_text;calls=[]
        def replacement(path,*args,**kwargs):
            if path==self.root/'maps':
                calls.append(str(path))
                if len(calls) in changed_reads:return self.line(dict(self.region,permissions='r--p'))
                if original is not None:return original
            return read(path,*args,**kwargs)
        with patch.object(Path,'read_text',replacement):yield calls

    def failure(self,rows):
        (self.root/'maps').write_text(''.join(self.line(r) for r in rows))
        with self.maps_reads() as calls:
            with self.assertRaises(ValueError) as caught:h._validate_region(self.proc,self.region)
        self.assertEqual(len(calls),1)
        self.assertIs(type(caught.exception),ValueError)
        self.assertEqual(repr(caught.exception),"ValueError('source mapping identity changed')")
        return caught.exception.mapping_diagnostic

    def block(self,changed_reads,method='compare'):
        identity=dict(start_ticks='123',cgroup='0::/fixture\n')
        info=SimpleNamespace(st_dev=1,st_ino=20,st_rdev=50,st_mode=stat.S_IFBLK)
        with ExitStack() as stack:
            reads=stack.enter_context(self.maps_reads(changed_reads))
            stack.enter_context(patch.object(m,'process_identity',return_value=identity))
            stack.enter_context(patch.object(Path,'stat',return_value=info))
            opened=stack.enter_context(patch.object(m.os,'open',return_value=10))
            close=stack.enter_context(patch.object(m.os,'close'))
            stack.enter_context(patch.object(m.os,'fstat',return_value=info))
            stack.enter_context(patch.object(m,'capacity',return_value=self.capacity))
            old=stack.enter_context(patch.object(m,'mincore_file',return_value=dict(stable=True,resident_bytes=4096)))
            cache=stack.enter_context(patch.object(m,'cachestat',return_value=self.stats))
            value=m.observe_devices([self.ref],{101:identity},proc_root=self.proc,method=method)
        return value,old,cache,opened,close,reads

    def test_unchanged_region_keeps_success_and_one_maps_read(self):
        with self.maps_reads() as calls:self.assertIsNone(h._validate_region(self.proc,self.region))
        self.assertEqual(len(calls),1)
        value,_,cache,_,_,reads=self.block(set(),method='cachestat')
        self.assertEqual(value['status'],'OBSERVED');cache.assert_called_once();self.assertEqual(len(reads),4)
        q=value['objects'][0]['queries']['cachestat']
        self.assertNotIn('mapping_diagnostic',q);self.assertNotIn('failed_reference',q)

    def test_each_same_range_field_change_is_rejected_and_named(self):
        for key,observed in dict(permissions='r--p',offset=0,device=2,inode=21,mapping_path='/dev/ublkb78').items():
            with self.subTest(key=key):
                diagnostic=self.failure([dict(self.region,**{key:observed})])
                self.assertEqual(diagnostic['reason'],'FIELDS_CHANGED')
                self.assertEqual(diagnostic['expected'],self.region)
                self.assertEqual(diagnostic['changed_fields'],{key:dict(expected=self.region[key],observed=observed)})
                self.assertEqual(diagnostic['observed'][key],observed)
                self.assertEqual(diagnostic['overlapping_regions'],[])

    def test_split_merged_or_missing_exact_range_stays_rejected(self):
        scenarios=[[],[dict(self.region,end=0x2000),dict(self.region,start=0x2000,offset=self.region['offset']+0x1000)],
            [dict(self.region,start=0,end=0x5000)], [dict(self.region,start=0x5000,end=0x6000)]]
        for rows in scenarios:
            with self.subTest(rows=rows):
                diagnostic=self.failure(rows)
                expected=[r for r in rows if r['start']<self.region['end'] and self.region['start']<r['end']]
                self.assertEqual(diagnostic['reason'],'EXACT_RANGE_MISSING')
                self.assertIsNone(diagnostic['observed'])
                self.assertEqual(len(diagnostic['overlapping_regions']),len(expected))
                self.assertEqual(diagnostic['changed_fields']['range'],dict(expected=[0x1000,0x4000],observed=None))
                for actual,wanted in zip(diagnostic['overlapping_regions'],expected):
                    self.assertEqual((actual['start'],actual['end']),(wanted['start'],wanted['end']))

    def test_pid_change_is_rejected_before_reading_maps(self):
        with self.maps_reads() as calls:
            with self.assertRaises(ProcessLookupError):h._validate_region(self.proc,dict(self.region,start_ticks='124'))
        self.assertFalse(calls)

    def test_pre_query_failure_records_alias_and_fields_for_both_methods(self):
        value,old,cache,_,close,reads=self.block({3,4})
        old.assert_not_called();cache.assert_not_called();close.assert_called_once_with(10)
        self.assertEqual(len(reads),4)  # Inventory twice, then one read per method; no retry.
        self.assertEqual(value['status'],'PARTIAL');row=value['objects'][0]
        for method in ('mincore','cachestat'):
            q=row['queries'][method]
            self.assertEqual(q['status'],'UNKNOWN');self.assertNotIn('core_query',q['stages'])
            self.assertEqual(q['failed_reference'],self.ref)
            self.assertEqual(q['mapping_diagnostic']['changed_fields']['permissions']['observed'],'r--p')
            self.assertIsNone(value['method_totals'][method]['complete_bytes'])
        self.assertIsNone(row['resident_bytes']);self.assertIsNone(row['cache_index_bytes'])
        self.assertEqual(json.loads(json.dumps(value))['objects'][0]['queries']['mincore']['failed_reference'],self.ref)

    def test_post_query_failure_keeps_raw_values_but_no_capacity(self):
        for method in ('mincore','cachestat'):
            with self.subTest(method=method):
                value,old,cache,_,close,reads=self.block({4},method=method)
                q=value['objects'][0]['queries'][method]
                self.assertEqual(q['status'],'UNKNOWN');self.assertIn('identity_after',q['stages'])
                self.assertEqual(q['failed_reference'],self.ref)
                self.assertIn('mapping_diagnostic',q)
                self.assertIsNone(q['resident_bytes']);self.assertIsNone(q['cache_index_bytes'])
                self.assertEqual(value['status'],'PARTIAL');self.assertEqual(len(reads),4)
                self.assertIn('residency' if method=='mincore' else 'cache_counters',q)
                (old if method=='mincore' else cache).assert_called_once()
                close.assert_called_once_with(10)

    def test_inventory_failure_preserves_diagnostic_and_prevents_core_queries(self):
        value,old,cache,opened,_,reads=self.block({1})
        opened.assert_not_called();old.assert_not_called();cache.assert_not_called()
        failure=value['errors'][0]
        self.assertEqual(failure['reference'],self.ref)
        self.assertEqual(failure['mapping_diagnostic']['reason'],'FIELDS_CHANGED')
        self.assertEqual(value['objects'][0]['reference_errors'][0],failure)
        self.assertEqual(len(reads),1);self.assertEqual(value['status'],'PARTIAL')

    def test_tmpfs_query_preserves_mapping_diagnostic_before_or_after_core(self):
        self.region['mapping_path']='/dev/zero (deleted)'
        (self.root/'maps').write_text(self.line(self.region))
        metadata=dict(device=1,inode=20,size=32768,mode=stat.S_IFREG|0o600,nlink=0,filesystem_magic=0x01021994)
        for bad in (1,2):
            with self.subTest(bad=bad),self.maps_reads({bad}),patch.object(h.os,'open',side_effect=[10,11]) as opened,\
                 patch.object(h.os,'close') as close,patch.object(h,'_metadata',return_value=metadata),\
                 patch.object(h,'cachestat',return_value=self.stats) as query:
                value=h.query_deleted_backing(self.region,proc=self.proc)
            self.assertEqual(value['status'],'UNKNOWN');self.assertIsNone(value['whole_backing_cache_bytes'])
            self.assertIsNone(value['cached_bytes']);self.assertEqual(value['source_region'],self.region)
            self.assertEqual(value['mapping_diagnostic']['reason'],'FIELDS_CHANGED')
            if bad==1:opened.assert_not_called();query.assert_not_called()
            else:query.assert_called_once();self.assertEqual(close.call_count,2)

    def test_bounded_worker_keeps_diagnostic_json_and_unknown_capacity(self):
        diagnostic=self.failure([dict(self.region,offset=0)])
        obj=dict(device=1,inode=20,status='UNKNOWN',whole_backing_cache_bytes=None,mapping_diagnostic=diagnostic)
        payload=dict(status='INCOMPLETE',objects=[obj],whole_backing_cache_bytes=None)
        worker=MagicMock();worker.pid=123;worker.returncode=0;worker.__enter__.return_value=worker
        worker.communicate.return_value=(json.dumps(payload),'')
        with patch.object(h.subprocess,'Popen',return_value=worker):value=h.query_deleted_backings_bounded([self.region])
        self.assertEqual(value['status'],'INCOMPLETE');self.assertIsNone(value['whole_backing_cache_bytes'])
        self.assertTrue(value['worker_reaped']);self.assertEqual(value['objects'][0]['mapping_diagnostic'],diagnostic)


if __name__=='__main__':unittest.main()
