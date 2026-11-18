"""Simulated rows/maps/files only: no BPF, cgroups, services or mincore."""
import ctypes
import errno
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_file_page_charge as c
import host_file_pfn_query as p
from test_host_file_pfn_query import HostFilePfnTests
from test_snapshot_bpf_batch import Call


def row(offset=0,pfn=100,state=1,raw=10,online=10,flags=2,bit=1):
    return [offset,bit,1,p.FOLIO|pfn,state,raw,online,flags,1000,1001]


class PageChargeTests(unittest.TestCase):
    def test_online_offline_and_clear_mincore(self):
        rows=np.array([row(),row(1,101,raw=20,online=5,flags=16,bit=0),
                       [2,0,1,0,0,0,0,0,1000,1001]],dtype='<u8')
        result=c.decode_charge_rows(rows)
        self.assertEqual(result['charge']['status'],'OBSERVED_NONATOMIC')
        self.assertEqual(result['confirmed_pfn_union_bytes'],8192)
        self.assertEqual(result['charge']['owners'][1],dict(raw_cgroup_id=20,online_ancestor_id=5,
            raw_online=False,observed_unique_pages=1,observed_bytes=4096))
        self.assertFalse(result['charge']['atomic'])

    def test_unknown_nocharge_changed_and_missing_not_assigned(self):
        rows=[row(i,100+i,state=i+2,raw=0,online=0,flags=0) for i in range(4)]
        rows.append([4,0,0,0,0,0,0,0,0,0])
        r=c.decode_charge_rows(np.array(rows,dtype='<u8'))
        self.assertEqual(r['status'],'PARTIAL');self.assertEqual(r['charge']['unknown_page_observations'],5)
        self.assertEqual(r['charge']['owners'],[])

    def test_conflicting_pfn_not_double_attributed(self):
        r=c.decode_charge_rows(np.array([row(),row(1,raw=20,online=20)],dtype='<u8'))
        self.assertEqual(r['charge']['conflicting_pfn_count'],1)
        self.assertEqual(sum(v['observed_bytes'] for v in r['charge']['owners']),0)
        self.assertEqual(r['confirmed_pfn_union_bytes'],4096)

    def test_bad_fields_rejected(self):
        for index,value in [(4,0),(4,9),(5,0),(6,99),(8,0),(9,999)]:
            r=row();r[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(ValueError):
                c.decode_charge_rows(np.array([r],dtype='<u8'))
        with self.assertRaises(ValueError):c.decode_charge_rows(np.array([row()[:4]],dtype='<u8'))

    def test_old_four_column_reader_unchanged(self):
        r=p.decode_rows(np.array([row()[:4]],dtype='<u8'))
        self.assertNotIn('charge',r);self.assertEqual(p.HostFilePfnQuery.row_bytes,32)
        self.assertEqual(p.HostFilePfnQuery.version,'host-file-pfn-query-v1')

    def test_same_return_program_guards_and_no_lock(self):
        s=c.charge_program(123,{})
        for text in ('pid == 123','tid==$c.1','@mapping[tid]','FOLIO_MEMCG_DATA',
                     '$raw,$online,$rawflags,$begin,nsecs','$depth<64','$state=4',
                     '$data!=','clear(@mapping)'):
            self.assertIn(text,s)
        self.assertNotIn('folio_lock',s);self.assertNotIn('PG_UPTODATE',s)
        self.assertNotIn('bpf_override_return',s)

    def test_new_rows_saved_in_existing_guarded_query(self):
        helper=HostFilePfnTests()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                q=helper.synthetic(Path(tmp));q.__class__=c.HostFileChargeQuery
                q.batch.read.return_value={0:tuple(row()[3:])}
                r=q.query_chunk(0,1)
                self.assertEqual(r['version'],c.VERSION)
                rows=np.fromfile(q.directory/r['binary'],dtype='<u8').reshape(-1,10)
                self.assertEqual(rows.tolist(),[row()])
                self.assertEqual(c.decode_charge_rows(rows),r['raw_summary'])
                self.assertEqual(q.raw_bytes,sum(f.stat().st_size for f in q.directory.iterdir()))
        finally:helper.doCleanups()

    def test_new_budget_checked_before_mapping(self):
        helper=HostFilePfnTests()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                q=helper.synthetic(Path(tmp));q.__class__=c.HostFileChargeQuery
                q.raw_bytes=p.RAW_BUDGET-80
                with self.assertRaisesRegex(ValueError,'budget'):q.query_chunk(0,1)
                q.libc.mmap.assert_not_called()
        finally:helper.doCleanups()

    def test_wide_map_layout_token_and_incomplete_read(self):
        info=dict(type='hash',bytes_key=8,bytes_value=64,flags=0,max_entries=4)
        def lookup(fd,inc,out,keys,values,count,opts):
            k=(ctypes.c_uint64*4).from_address(keys)
            v=(ctypes.c_uint64*32).from_address(values)
            for i,token in enumerate((7,6)):
                k[i]=i
                for j,n in enumerate([token,*row(i)[3:]]):v[i*8+j]=n
            ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))[0]=2
            return -errno.ENOENT
        batch=c.BatchEntries(SimpleNamespace(bpf_map_lookup_batch=Call(lookup)),12,info,value_words=8)
        self.assertEqual(batch.read(np.array([0,1],dtype='<u8'),7),{0:tuple(row()[3:])})
        self.assertEqual(batch.buffer_bytes,4*72)
        with self.assertRaises(ValueError):c.BatchEntries(object(),12,info)
        batch.lookup=Call(lambda *args:0)
        with self.assertRaises(RuntimeError):batch.read(np.array([0],dtype='<u8'),7)

    def test_history_keeps_removed_id_and_path_reuse_separate(self):
        h=c.GroupIdentityHistory('boot')
        def fake(ino):return lambda path:SimpleNamespace(st_ino=ino,st_dev=9)
        h.observe('/mock/a','a',now_ns=1,stat_func=fake(10))
        h.observe('/mock/a','a',now_ns=2,stat_func=fake(10))
        with patch.object(Path,'stat',side_effect=FileNotFoundError):h.observe('/mock/a','a',now_ns=3)
        h.observe('/mock/a','a',now_ns=4,stat_func=fake(11))
        h.observe('/mock/a','a',now_ns=5,stat_func=fake(12))
        self.assertEqual([r['inode'] for r in h.records],[10,11,12])
        self.assertEqual([r['first_absent_ns'] for r in h.records],[3,5,None])
        self.assertEqual(h.records[1]['end_reason'],'PATH_ID_REPLACED')
        with patch.object(Path,'stat',side_effect=PermissionError),self.assertRaises(PermissionError):
            h.observe('/mock/a','a')
        self.assertIsNone(h.records[-1]['first_absent_ns'])
        with tempfile.TemporaryDirectory() as tmp:
            h.save(Path(tmp)/'history.json')
            self.assertEqual(len(json.loads((Path(tmp)/'history.json').read_text())['records']),3)


if __name__=='__main__':unittest.main()
