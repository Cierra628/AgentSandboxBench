"""Offline dictionaries/temporary saved data only; never launch real collectors."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_pfn_plan as m


class BackingPfnPlanTests(unittest.TestCase):
    def raw(self):
        def row(inode,size,users,cats):
            return dict(device=1,inode=inode,size=size,mtime_ns=3,status='OBSERVED',users=users,
                categories=cats,aliases=[],resident_bytes=0,
                queried_alias=dict(path='/definitely/not/opened/'+str(inode),source='saved'),
                residency=dict(device=1,inode=inode,rdev=0,stable=True,resident_bytes=0,
                    mincore_permission=dict(basis='HOST_CAP_FOWNER')))
        raw=dict(status='COLLECTED',guests=dict(a='sid-a',b='sid-b'),
            bindings=dict(a=dict(pid=11,start_ticks='1',boot_id='boot'),b=dict(pid=12,start_ticks='2',boot_id='boot')),
            rootfs_inventory=dict(status='IDENTIFIED',guests={r:dict(lowers=[]) for r in ('a','b')}),
            expanded_files=dict(status='OBSERVED',files=[row(2,8193,['a','b'],['active_snapshot_rootfs_lower']),
                row(3,0,['a'],['owned_logs']),row(4,4096,[],['owned_service'])],unresolved=[dict(error='saved closed FD')]),
            cache_layers=dict(status='OBSERVED',process_errors=[]))
        return raw

    def evidence(self,plan,chunks=None):
        t=plan['targets'][0]
        identity=dict(t['saved_identity'],ctime_ns=9,mode=33188)
        chunks=chunks if chunks is not None else [dict(first_page=0,pages=1,unknown_pages=0,
            confirmed_pfn_union_bytes=4096,physical_pfns=[101],state_counts={'FOLIO_PRESENT_MINCORE_SET':1})]
        return dict(guests=copy.deepcopy(plan['guests']),bindings=copy.deepcopy(plan['bindings']),objects=[dict(kind='regular',
            object_key=t['object_key'],references=copy.deepcopy(t['references']),pfn_context=dict(identity=identity),
            file_page_pfns=dict(status='OBSERVED_LISTED_CHUNKS',chunks=chunks))])

    def test_bound_objects_dedup_shared_references_and_zero_residency_not_skipped(self):
        raw=self.raw();original=copy.deepcopy(raw);p=m.make_plan(raw)
        self.assertEqual(raw,original);self.assertEqual(p['status'],'PLANNED_NOT_EXECUTED')
        self.assertEqual(len(p['targets']),2);self.assertEqual(len(p['excluded']),1)
        t=p['targets'][0];self.assertEqual(len(t['references']),2)
        self.assertEqual(t['page_offsets'],3);self.assertEqual(t['ranges'],[dict(first_page=0,pages=3,mapping_length=8193)])
        self.assertEqual(t['missing_identity_fields'],['mode','ctime_ns'])
        self.assertEqual(t['candidate_path'],'/definitely/not/opened/2')
        self.assertEqual(p['inventory_limits']['unresolved_sources'],raw['expanded_files']['unresolved'])

    def test_actual_chunk_limit_and_partial_last_page(self):
        raw=self.raw();size=(m.MAX_CHUNK_PAGES+1)*m.PAGE+7;raw['expanded_files']['files'][0]['size']=size
        t=m.make_plan(raw)['targets'][0]
        self.assertEqual(t['chunk_count'],2)
        self.assertEqual(t['ranges'],[dict(first_page=0,pages=m.MAX_CHUNK_PAGES,mapping_length=m.MAX_CHUNK_PAGES*m.PAGE),
            dict(first_page=m.MAX_CHUNK_PAGES,pages=2,mapping_length=4103)])

    def test_budget_exact_boundary_blocks_without_truncating_targets(self):
        raw=self.raw();p=m.make_plan(raw);needed=p['budget']['planned_record_bytes']
        self.assertEqual(m.make_plan(raw,record_budget=needed)['status'],'BLOCKED')
        self.assertEqual(m.make_plan(raw,record_budget=needed+1)['status'],'PLANNED_NOT_EXECUTED')
        raw['expanded_files']['files'][1]['size']=4096
        one=m.make_plan(self.raw())['budget']['planned_record_bytes']+1
        q=m.make_plan(raw,record_budget=one)
        self.assertEqual(q['status'],'BLOCKED');self.assertEqual(len(q['targets']),2)
        self.assertIn('TOTAL_RECORD_BUDGET_NOT_SATISFIED',q['reasons'])

    def test_existing_per_object_limit_and_invalid_parameters(self):
        raw=self.raw();raw['expanded_files']['files'][0]['size']=m.RAW_BUDGET//m.ROW_BYTES*m.PAGE
        p=m.make_plan(raw);self.assertEqual(p['status'],'BLOCKED')
        self.assertIn('OBJECT_EXCEEDS_EXISTING_QUERY_BUDGET',p['targets'][0]['reasons'])
        for kw in (dict(record_budget=0),dict(record_budget=m.RAW_BUDGET+1),dict(record_budget=True),dict(chunk_pages=0),dict(chunk_pages=m.MAX_CHUNK_PAGES+1)):
            with self.subTest(kw=kw),self.assertRaises(ValueError):m.make_plan(self.raw(),**kw)

    def test_unstable_missing_size_or_path_remain_explicit_targets_not_excluded(self):
        for mode in ('changed','size','path','fingerprint'):
            raw=self.raw();r=raw['expanded_files']['files'][0]
            if mode=='changed':r['status']='CHANGED_DURING_QUERY'
            elif mode=='size':r.pop('size')
            elif mode=='fingerprint':r.pop('mtime_ns')
            else:r['queried_alias']={}
            p=m.make_plan(raw)
            with self.subTest(mode=mode):
                self.assertEqual(p['status'],'BLOCKED');self.assertEqual(len(p['targets']),2)
                self.assertTrue(p['targets'][0]['reasons'])

    def test_special_kinds_retained_separately_even_with_bound_references(self):
        raw=self.raw();r=copy.deepcopy(raw['expanded_files']['files'][0]);r['inode']=10;r['status']='SEPARATE_SHMEM_OR_HUGETLB'
        raw['expanded_files']['files'].append(r)
        p=m.make_plan(raw);self.assertEqual(len(p['targets']),2)
        self.assertIn('shmem_or_hugetlb',[o['kind'] for o in p['excluded']])

    def test_no_target_live_stat_open_or_collector_call(self):
        with patch.object(Path,'stat',side_effect=AssertionError('no live stat')),\
             patch('host_file_pfn_query.observe',side_effect=AssertionError('no live query')):
            p=m.make_plan(self.raw());c=m.offset_coverage(p)
        self.assertEqual(c['known_page_offsets'],0);self.assertEqual(c['unqueried_page_offsets'],3)
        self.assertIsNone(c['complete_sandbox_host_cache_bytes'])

    def test_existing_range_covers_offsets_only_not_rest_or_other_objects(self):
        p=m.make_plan(self.raw());e=self.evidence(p);c=m.offset_coverage(p,e)
        self.assertEqual(c['known_page_offsets'],1);self.assertEqual(c['unqueried_page_offsets'],2)
        self.assertEqual(c['empty_objects_not_revalidated'],1);self.assertEqual(c['known_offset_fraction'],1/3)
        self.assertIsNone(c['cross_chunk_pfn_union_bytes']);self.assertIsNone(c['complete_sandbox_host_cache_bytes'])
        self.assertNotIn('physical_pfns',c['objects'][0]['chunks'][0])

    def test_missing_returns_partial_and_clear_mincore_folio_not_lost(self):
        p=m.make_plan(self.raw());e=self.evidence(p,[dict(first_page=0,pages=3,unknown_pages=1,
            confirmed_pfn_union_bytes=4096,physical_pfns=[101],state_counts={'FOLIO_PRESENT_MINCORE_CLEAR':1,'NO_FOLIO_OBSERVED':1,'MISSING_KERNEL_RETURN':1})])
        c=m.offset_coverage(p,e)
        self.assertEqual(c['known_page_offsets'],2);self.assertEqual(c['unknown_observed_page_offsets'],1)
        self.assertEqual(c['unqueried_page_offsets'],0);self.assertEqual(c['objects'][0]['status'],'PARTIAL')
        self.assertEqual(c['objects'][0]['chunks'][0]['confirmed_pfn_union_bytes'],4096)

    def test_all_offsets_nonatomic_never_fills_physical_total_or_sums_chunks(self):
        p=m.make_plan(self.raw());e=self.evidence(p,[dict(first_page=0,pages=1,unknown_pages=0,confirmed_pfn_union_bytes=4096,physical_pfns=[101]),
            dict(first_page=1,pages=2,unknown_pages=0,confirmed_pfn_union_bytes=4096,physical_pfns=[101])])
        c=m.offset_coverage(p,e);r=c['objects'][0]
        self.assertEqual(r['status'],'ALL_OFFSETS_OBSERVED_NONATOMIC');self.assertEqual(c['known_offset_fraction'],1)
        self.assertIsNone(r['complete_object_physical_bytes']);self.assertIsNone(c['cross_object_pfn_union_bytes'])

    def test_changed_boot_process_version_or_references_rejects_coverage(self):
        for mode in ('boot','pid','version','reference'):
            p=m.make_plan(self.raw());e=self.evidence(p)
            if mode=='boot':e['bindings']['a']['boot_id']='other'
            elif mode=='pid':e['bindings']['a']['pid']=99
            elif mode=='version':e['objects'][0]['pfn_context']['identity']['mtime_ns']=99
            else:e['objects'][0]['references']=[]
            with self.subTest(mode=mode),self.assertRaises(ValueError):m.offset_coverage(p,e)

    def test_overlapping_or_out_of_range_offsets_and_unknown_counts_rejected(self):
        for chunks in ([dict(first_page=0,pages=4,unknown_pages=0)],
            [dict(first_page=0,pages=2,unknown_pages=0),dict(first_page=1,pages=1,unknown_pages=0)],
            [dict(first_page=0,pages=1,unknown_pages=2)]):
            p=m.make_plan(self.raw())
            with self.subTest(chunks=chunks),self.assertRaises(ValueError):m.offset_coverage(p,self.evidence(p,chunks))

    def test_failed_inventory_or_duplicate_objects_not_accepted(self):
        raw=self.raw();raw['status']='FAIL'
        with self.assertRaises(ValueError):m.make_plan(raw)
        raw=self.raw();raw['expanded_files']['files'].append(raw['expanded_files']['files'][0])
        with self.assertRaises(ValueError):m.make_plan(raw)

    def test_cli_offline_saved_files_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp=root/'raw.json';out=root/'plan.json';inp.write_text(json.dumps(self.raw()))
            argv=['offline','--saved-raw',str(inp),'--output',str(out)]
            with patch.object(sys,'argv',argv):self.assertEqual(m.main(),0)
            result=json.loads(out.read_text());self.assertEqual(result['coverage']['observed_page_offsets'],0)
            with patch.object(sys,'argv',argv),self.assertRaises(FileExistsError):m.main()


if __name__=='__main__':unittest.main()
