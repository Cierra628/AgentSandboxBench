"""Saved temporary JSON/binary fixtures only; no host residency or BPF query."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_pfns as m


class BackingPfnAttachmentTests(unittest.TestCase):
    def fixture(self,root,*,rows=None):
        identity=dict(zip(m.FIELDS,(1,2,0,33188,8192,3,4)))
        rows=np.array(rows if rows is not None else [[0,1,1,m.FOLIO|100],[1,0,1,m.FOLIO|100]],dtype='<u8')
        summary=m.decode_rows(rows);directory=root/'query';directory.mkdir()
        rows.tofile(directory/'chunk-0000.bin')
        chunk=dict(first_page=0,pages=2,mapping_length=8192,token=1,query_process=10,
            object_stable=True,probe_alive_after=True,identity_before=identity,identity_after=identity,
            alias_after=identity,permission=dict(host=dict(host_root_cap_fowner=True),thread=dict(basis='HOST_CAP_FOWNER')),
            binary='chunk-0000.bin',costs=dict(host_start_ns=110,host_end_ns=120),
            raw_summary=summary,status=summary['status'],accepted_pfn_union_bytes=summary['confirmed_pfn_union_bytes'])
        query=dict(version=m.QUERY_VERSION,status=summary['status'],identity=identity,simulated=True,
            expected_identity=[1,2],path='/not-read/synthetic.bin',host_boot_id='boot',page_size=4096,
            first_page=0,pages=2,chunks=[chunk],cleanup=dict(owned_tracer_exited=True,tracer_returncode=0))
        source=directory/'result.json';source.write_text(json.dumps(query))
        obj=dict(object_key=['regular',1,2],kind='regular',references=[dict(role='a',sandbox_id='sid-a'),dict(role='b',sandbox_id='sid-b')],
            query=dict(bytes=999999,status='OBSERVED'),reference_status='BOUND',
            pfn_context=dict(identity=identity,host_boot_id='boot',start_monotonic_ns=100,end_monotonic_ns=130))
        inventory=dict(version='host-backing-references-v1',status='PARTIAL',simulated=True,guests=dict(a='sid-a',b='sid-b'),
            bindings={r:dict(boot_id='boot',pid=i) for i,r in enumerate(('a','b'),1)},objects=[obj],
            per_guest={r:dict(exclusive_physical_bytes=None) for r in ('a','b')},complete_sandbox_host_cache_bytes=None)
        return source,query,inventory

    def test_attach_preserves_old_fields_deduplicates_and_keeps_clear_bit_pfn(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inventory=self.fixture(Path(tmp));original=copy.deepcopy(inventory)
            out=m.attach(inventory,m.load_query(source))
            p=out['objects'][0].pop('file_page_pfns');out.pop('pfn_attachment')
            self.assertEqual(out,original);self.assertEqual(inventory,original)
            chunk=p['chunks'][0]
            self.assertEqual(chunk['physical_pfns'],[100]);self.assertEqual(chunk['confirmed_pfn_union_bytes'],4096)
            self.assertEqual(chunk['state_counts']['FOLIO_PRESENT_MINCORE_CLEAR'],1)
            self.assertIsNone(p['exclusive_physical_bytes']);self.assertIsNone(p['complete_object_physical_bytes'])
            self.assertEqual(len(out['objects'][0]['references']),2)

    def test_explicit_null_zero_differs_from_missing_returns(self):
        for found in (0,1):
            with self.subTest(found=found),tempfile.TemporaryDirectory() as tmp:
                source,q,inv=self.fixture(Path(tmp),rows=[[0,0,found,0],[1,0,found,0]])
                out=m.attach(inv,m.load_query(source));row=out['objects'][0]['file_page_pfns'];chunk=row['chunks'][0]
                self.assertEqual(row['status'],'OBSERVED_LISTED_CHUNKS' if found else 'PARTIAL')
                self.assertEqual(chunk['complete_listed_pfn_union_bytes'],0 if found else None)
                self.assertEqual(chunk['unknown_pages'],0 if found else 2)
                self.assertEqual(chunk['physical_pfns'],[])

    def test_missing_query_never_uses_old_mincore_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));out=m.attach(inv)
            self.assertEqual(out['objects'][0]['file_page_pfns']['status'],'UNAVAILABLE')
            self.assertEqual(out['objects'][0]['query']['bytes'],999999)
            self.assertIsNone(out['objects'][0]['file_page_pfns']['complete_object_physical_bytes'])

    def test_identity_boot_window_and_failed_inventory_reject_attachment(self):
        for mode in ('identity','host_boot','binding_boot','window','context','failed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                source,q,inv=self.fixture(Path(tmp));context=inv['objects'][0]['pfn_context']
                if mode=='identity':context['identity']=dict(context['identity'],ctime_ns=99)
                elif mode=='host_boot':context['host_boot_id']='other-boot'
                elif mode=='binding_boot':inv['bindings']['a']['boot_id']='other-boot'
                elif mode=='window':context['end_monotonic_ns']=115
                elif mode=='context':inv['objects'][0].pop('pfn_context')
                else:inv['status']='FAIL'
                out=m.attach(inv,m.load_query(source));row=out['objects'][0]['file_page_pfns']
                self.assertEqual(row['status'],'UNAVAILABLE');self.assertTrue(row['reasons']);self.assertEqual(row['chunks'],[])

    def test_other_object_different_inode_and_nonregular_layers_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));other=copy.deepcopy(inv['objects'][0]);other['object_key']=['regular',1,3]
            loop=copy.deepcopy(inv['objects'][0]);loop['kind']='outer_loop';loop['references']=[]
            inv['objects']+=[other,loop];out=m.attach(inv,m.load_query(source))
            self.assertEqual([o['file_page_pfns']['status'] for o in out['objects']],['OBSERVED_LISTED_CHUNKS','UNAVAILABLE','UNAVAILABLE'])
            self.assertEqual(out['pfn_attachment']['available_objects'],1)
            self.assertIsNone(out['pfn_attachment']['cross_object_pfn_union_bytes'])

    def test_unmatched_query_and_conflicting_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));query=m.load_query(source)
            inv['objects'][0]['object_key']=['regular',1,999]
            self.assertTrue(m.attach(inv,query)['pfn_attachment']['unmatched_query'])
            inv['objects'][0]['references'][0]['sandbox_id']='wrong'
            with self.assertRaisesRegex(ValueError,'reference conflict'):m.attach(inv,query)

    def test_query_permission_cleanup_boot_or_stability_missing_cannot_attach(self):
        for mode in ('permission','cleanup','boot','stable'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                source,q,inv=self.fixture(Path(tmp))
                if mode=='permission':q['chunks'][0]['permission']={}
                elif mode=='cleanup':q['cleanup']={}
                elif mode=='boot':q.pop('host_boot_id')
                else:q['chunks'][0]['object_stable']=False
                source.write_text(json.dumps(q));row=m.attach(inv,m.load_query(source))['objects'][0]['file_page_pfns']
                self.assertEqual(row['status'],'UNAVAILABLE');self.assertEqual(row['chunks'],[])

    def test_corrupt_rows_range_summary_and_token_raise_instead_of_accepting(self):
        for mode in ('summary','offset','size','range','token','identity','version'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                source,q,inv=self.fixture(Path(tmp))
                if mode=='summary':q['chunks'][0]['raw_summary']['confirmed_pfn_union_bytes']=0
                elif mode=='offset':np.array([[9,1,1,m.FOLIO|100],[1,0,1,m.FOLIO|100]],dtype='<u8').tofile(source.parent/'chunk-0000.bin')
                elif mode=='size':(source.parent/'chunk-0000.bin').write_bytes(b'short')
                elif mode=='range':q['chunks'][0]['first_page']=1
                elif mode=='token':q['chunks'][0]['token']=0
                elif mode=='identity':q['expected_identity']=[1,999]
                else:q['version']='wrong'
                source.write_text(json.dumps(q))
                with self.assertRaises(ValueError):m.load_query(source)

    def test_failed_saved_query_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));q['status']='FAIL';q.pop('chunks');source.write_text(json.dumps(q))
            row=m.attach(inv,m.load_query(source))['objects'][0]['file_page_pfns']
            self.assertEqual(row['status'],'UNAVAILABLE');self.assertIn('SAVED_QUERY_FAILED',row['reasons'])

    def test_no_repeat_attachment_or_duplicate_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));query=m.load_query(source)
            out=m.attach(inv,query)
            with self.assertRaises(ValueError):m.attach(out,query)
            inv['objects']*=2
            with self.assertRaises(ValueError):m.attach(inv,query)

    def test_calibration_context_is_bound_to_exact_saved_query_and_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,q,inv=self.fixture(root);q.pop('host_boot_id');source.write_text(json.dumps(q))
            context=dict(version='host-file-pfn-positive-v1',status='PASS',simulated=True,query_invocations=1,query_status=q['status'],
                identity=q['identity'],file=q['path'],host_boot_id='boot',process_id=10,
                reference_before=dict(start_monotonic_ns=100),reference_after=dict(end_monotonic_ns=130))
            path=root/'result.json';path.write_text(json.dumps(context))
            query=m.load_query(source,path);normalized=m.calibration_inventory(path)
            self.assertEqual(normalized['guests'],{})
            out=m.attach(normalized,query);self.assertEqual(out['pfn_attachment']['available_objects'],1)
            self.assertEqual(out['objects'][0]['references'],[])
            context['process_id']=11;path.write_text(json.dumps(context))
            with self.assertRaisesRegex(ValueError,'process differs'):m.load_query(source,path)
            context['identity']=dict(context['identity'],inode=9);path.write_text(json.dumps(context))
            with self.assertRaisesRegex(ValueError,'does not bind'):m.load_query(source,path)

    def test_joint_context_path_and_stable_references_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,q,inv=self.fixture(root)
            directory=root/'host-backing-pfn-query';source.parent.rename(directory);source=directory/'result.json'
            context=dict(version='host-backing-single-pfn-v1',status='PASS',simulated=True,
                query_invocations=1,query_status=q['status'],identity=q['identity'],identity_after=copy.deepcopy(q['identity']),
                file=q['path'],host_boot_id='boot',process_id=10,
                bindings_before=inv['bindings'],bindings_after=copy.deepcopy(inv['bindings']))
            path=root/'host-backing-single-pfn.json';path.write_text(json.dumps(context))
            self.assertEqual(m.attach(inv,m.load_query(source,path))['pfn_attachment']['available_objects'],1)
            for mode in ('binding','identity','empty'):
                bad=copy.deepcopy(context)
                if mode=='binding':bad['bindings_after']['a']['pid']=999
                elif mode=='identity':bad['identity_after']['inode']=99
                else:bad['bindings_before']=bad['bindings_after']={}
                path.write_text(json.dumps(bad))
                with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'stability'):
                    m.load_query(source,path)

    def test_two_nonatomic_chunks_keep_separate_sets_without_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));query=m.load_query(source)
            # Synthetic normalized chunks: test attachment policy, not a live multi-chunk query.
            next_chunk=copy.deepcopy(query['chunks'][0]);next_chunk.update(first_page=2,token=2,start_monotonic_ns=121,end_monotonic_ns=125)
            query['chunks'].append(next_chunk)
            out=m.attach(inv,query);row=out['objects'][0]['file_page_pfns']
            self.assertEqual(len(row['chunks']),2);self.assertIsNone(row['complete_object_physical_bytes'])
            self.assertIsNone(out['pfn_attachment']['cross_chunk_pfn_union_bytes'])

    def test_simulation_cannot_supply_a_real_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,q,inv=self.fixture(Path(tmp));inv['simulated']=False
            row=m.attach(inv,m.load_query(source))['objects'][0]['file_page_pfns']
            self.assertEqual(row['status'],'UNAVAILABLE')
            self.assertIn('SIMULATION_SCOPE_MISMATCH',row['reasons'])

    def test_cli_saved_inputs_only_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,q,inv=self.fixture(root);inp=root/'inventory.json';inp.write_text(json.dumps(inv))
            out=root/'output.json';args=['offline','--inventory',str(inp),'--saved-query',str(source),'--output',str(out)]
            with patch.object(sys,'argv',args):m.main()
            self.assertEqual(json.loads(out.read_text())['pfn_attachment']['available_objects'],1)
            with patch.object(sys,'argv',args),self.assertRaises(FileExistsError):m.main()


if __name__=='__main__':unittest.main()
