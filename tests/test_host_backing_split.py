"""Pure saved dictionaries only; no services, proc, mincore or device queries."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_host_backing_split import split,totals


class HostBackingSplitTests(unittest.TestCase):
    def fixture(self):
        def obj(inode,roles,kind='regular',size=4096,status='OBSERVED'):
            return dict(object_key=['block',inode] if kind=='block' else ['regular',1,inode],kind=kind,
                references=[dict(role=r,sandbox_id='sid-'+r) for r in roles],categories=['owned_logs'],
                aliases=[dict(path='/not-read/'+str(inode))],source_status=status,
                query=dict(status=status,bytes=size))
        rows=[obj(1,['a']),obj(2,['b'],size=0),obj(3,['a','b']),obj(4,[],size=None,status='UNKNOWN'),
            obj(33,['a','b'],kind='block',size=8192),obj(50,[],kind='outer_loop',size=16384)]
        evidence=dict(guests=dict(a='sid-a',b='sid-b'),objects=rows)
        source=[dict(device=1,inode=r['object_key'][2],primary_category='owned_logs',
            resident_bytes=99999 if r['query']['status']=='UNKNOWN' else r['query']['bytes'],
            residency=dict(stable=True),query_window_status='OBSERVED') for r in rows if r['kind']=='regular']
        return evidence,source

    def test_disjoint_reference_partition_reconciles_each_layer(self):
        e,s=self.fixture();v=split(e,s);regular=v['layers']['regular']
        self.assertEqual(regular['objects'],4);self.assertEqual(regular['observed_subtotal_bytes'],8192)
        self.assertEqual(regular['unknown_objects'],1);self.assertEqual(regular['partition_check'],'EXACT')
        self.assertIsNone(regular['complete_listed_bytes'])
        self.assertEqual({r['reference_group'] for r in regular['reference_groups']},
            {'single_reference:a','single_reference:b','shared_reference:a,b','unassigned'})

    def test_shared_values_not_divided_and_no_exclusive_total(self):
        e,s=self.fixture();v=split(e,s)
        for role in ('a','b'):
            g=v['per_guest'][role]['shared_reference_groups']
            self.assertEqual([r['observed_subtotal_bytes'] for r in g],[4096,8192])
            self.assertIsNone(v['per_guest'][role]['complete_host_memory_bytes'])
            self.assertIsNone(v['per_guest'][role]['complete_host_cache_bytes'])
        self.assertTrue(all(r['exclusive_physical_bytes'] is None for r in v['objects']))

    def test_observed_zero_not_missing_and_unknown_not_filled_from_raw(self):
        e,s=self.fixture();v=split(e,s)
        zero=next(r for r in v['groups'] if r['reference_group']=='single_reference:b')
        unknown=next(r for r in v['groups'] if r['reference_group']=='unassigned')
        self.assertEqual(zero['complete_listed_bytes'],0);self.assertEqual(zero['observed_objects'],1)
        self.assertIsNone(unknown['complete_listed_bytes']);self.assertEqual(unknown['observed_subtotal_bytes'],0)
        self.assertEqual(next(r for r in v['objects'] if r['reference_group']=='unassigned')['raw_resident_bytes'],99999)
        self.assertEqual(totals([])['status'],'EMPTY_LIST')

    def test_multiple_aliases_do_not_repeat_object_count(self):
        e,s=self.fixture();e['objects'][0]['aliases']*=4
        v=split(e,s);g=next(r for r in v['groups'] if r['reference_group']=='single_reference:a')
        self.assertEqual(g['objects'],1);self.assertEqual(g['observed_subtotal_bytes'],4096)

    def test_loop_is_containment_and_cross_layer_same_identity_kept_separate(self):
        e,s=self.fixture();e['objects'][-1]['object_key']=e['objects'][0]['object_key']
        v=split(e,s);self.assertEqual(v['layers']['outer_loop']['observed_subtotal_bytes'],16384)
        self.assertEqual(v['layers']['regular']['observed_subtotal_bytes'],8192)
        self.assertNotIn('physical_total_bytes',v)
        loop=next(r for r in v['objects'] if r['layer']=='outer_loop')
        self.assertEqual(loop['reference_group'],'container_only')

    def test_duplicate_object_reference_identity_and_invalid_bytes_rejected(self):
        for mode in ('object','source','wrong_sid','unknown_role','duplicate_role','negative','loop_ref'):
            e,s=self.fixture()
            if mode=='object':e['objects'].append(copy.deepcopy(e['objects'][0]))
            elif mode=='source':s.append(copy.deepcopy(s[0]))
            elif mode=='wrong_sid':e['objects'][0]['references'][0]['sandbox_id']='other'
            elif mode=='unknown_role':e['objects'][0]['references'][0]['role']='other'
            elif mode=='duplicate_role':e['objects'][0]['references']*=2
            elif mode=='negative':e['objects'][0]['query']['bytes']=-1
            else:e['objects'][-1]['references']=[dict(role='a',sandbox_id='sid-a')]
            with self.subTest(mode=mode),self.assertRaises(ValueError):split(e,s)

    def test_missing_regular_source_not_invented(self):
        e,s=self.fixture();s.pop()
        with self.assertRaisesRegex(ValueError,'source missing'):split(e,s)


if __name__=='__main__':unittest.main()
