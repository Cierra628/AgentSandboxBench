"""Temp fixtures and simulated query processes; no mincore, BPF, VM or service."""
import copy,json,os
from pathlib import Path
import sys,tempfile,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_pfn_batch as m
from host_file_pfn_query import decode_rows,FOLIO
from host_backing_pfn_plan import make_plan as real_plan
import run_live_child_calibration as entry


class BatchPfnTests(unittest.TestCase):
    def fixture(self,root):
        rows=[]
        for i,size in enumerate((3*4096,0,4096)):
            path=root/f'file-{i}';path.write_bytes(bytes(size));s=m.file_identity(path.stat())
            rows.append(dict(device=s['device'],inode=s['inode'],size=size,mtime_ns=s['mtime_ns'],status='OBSERVED',
                users=['private-a'],categories=['active_snapshot_rootfs_lower'],aliases=[],resident_bytes=0,
                queried_alias=dict(path=str(path),follow=False),residency=dict(device=s['device'],inode=s['inode'],rdev=0,
                    stable=True,resident_bytes=0,mincore_permission=dict(basis='HOST_CAP_FOWNER'))))
        raw=dict(status='PENDING',guests={'private-a':'sid'},bindings={'private-a':dict(pid=42,start_ticks='7',boot_id='boot')},
            rootfs_inventory=dict(status='IDENTIFIED',guests={'private-a':dict(lowers=[])}),
            expanded_files=dict(status='OBSERVED',files=rows,unresolved=[]),cache_layers=dict(status='OBSERVED',process_errors=[]))
        return raw

    def run_case(self,root,mode=None,*,record_budget=m.RAW_BUDGET):
        raw=self.fixture(root);calls=[];constructs=[];closes=[];checks=[];queries=[]
        log_index=1 if mode in ('log_growth_empty','log_empty_prepare_growth','log_empty_stays_empty') else 2 if mode=='log_late_failure' else 0
        log=root/f'file-{log_index}'
        if mode and mode.startswith('log_'):
            raw['expanded_files']['files'][log_index]['categories']=['owned_logs','open_regular_file']
        if mode and mode.startswith('upper_'):
            kind='index' if mode in ('upper_index_growth','upper_index_prepare32') else 'data'
            row=raw['expanded_files']['files'][0]
            if mode=='upper_index_prepare32':
                log.write_bytes(b'x'*7872)
                row.update(size=7872,mtime_ns=log.stat().st_mtime_ns)
            row['categories']=['owned_firecracker-work','rootfs_upper_'+kind,'open_regular_file']
            row['aliases']=[dict(path='/proc/42/cwd/overlaybd/active-write.bin',source='rootfs_upper_'+kind),
                dict(path=str(log),source='open_fd:actual-runtime-writable-file',process=11)]
            drive=dict(drive_id='user_rootfs',block_device=True,is_read_only=False,rdev=7)
            raw['drives']={'private-a':[drive]}
            raw['rootfs_inventory']['guests']['private-a'].update(status='IDENTIFIED',pid=42,
                image_config='/proc/42/cwd/overlaybd/image.json',config_identity=dict(device=1,inode=2),
                drive=drive,upper={kind:'./active-write.bin'})
            if mode=='upper_bad_alias':row['aliases'][0]['path']='/wrong/config/reference'
            if mode=='upper_no_fd':row['aliases'].pop()
            if mode=='upper_lower_conflict':row['categories'].append('active_snapshot_rootfs_lower')
            if mode=='upper_wrong_vmm':raw['rootfs_inventory']['guests']['private-a']['pid']=99
        class Query:
            def __init__(self,path,directory,expected_identity):
                self.path=Path(path);self.directory=Path(directory);self.fd=os.open(path,os.O_RDONLY)
                self.initial=m.file_identity(os.fstat(self.fd));self.records=[];self.raw_bytes=0
                self.kernel={};self.permission=dict(host=dict(host_root_cap_fowner=True),thread=dict(basis='HOST_CAP_FOWNER'))
                self.magic=0xef53;self.attached=False;self.batch=SimpleNamespace(buffer_bytes=0);self.cleanup=None
                constructs.append(str(path));queries.append(self)
            def attach(self):
                assert len(constructs)==3,'all objects must be pinned before first attach'
                self.attached=True;calls.append(self.path.name)
                if mode=='attach_failure_closed':
                    self.close();raise RuntimeError('owned host-file probe attach failed')
                if mode=='file_change':
                    self.path.write_bytes(b'changed')
                if mode in ('upper_prepare_growth','log_prepare_growth','log_empty_prepare_growth') and self.path==log:
                    with self.path.open('ab') as f:f.write(b'x'*4096)
                if mode=='upper_index_prepare32' and self.path==log:
                    with self.path.open('ab') as f:f.write(b'x'*32)
                if mode=='upper_prepare_replace' and self.path==log:
                    self.path.unlink();self.path.write_bytes(b'replacement')
                if mode=='upper_prepare_mode' and self.path==log:self.path.chmod(self.path.stat().st_mode^0o100)
                if mode=='upper_prepare_budget' and self.path==log:
                    with self.path.open('r+b') as f:f.truncate(64*2**20)
                if mode=='upper_prepare_attach_fail' and self.path==log:raise RuntimeError('probe preparation failed')
            def query_chunk(self,first,pages,*,before_write):
                if m.file_identity(os.fstat(self.fd))!=self.initial or m.file_identity(self.path.stat())!=self.initial:
                    raise ValueError('file/alias changed before query')
                n=len(self.records);ident=self.initial;now=time.monotonic_ns()
                rows=np.array([[first+j,0 if mode=='empty' else 1,0 if mode=='partial' else 1,
                    0 if mode in ('empty','partial') else FOLIO|101+j] for j in range(pages)],dtype='<u8')
                summary=decode_rows(rows)
                record=dict(first_page=first,pages=pages,mapping_length=min(pages*4096,ident['size']-first*4096),token=n+1,query_process=os.getpid(),
                    object_stable=True,probe_alive_after=True,identity_before=ident,identity_after=ident,alias_after=ident,
                    permission=self.permission,binary=f'chunk-{n:04}.bin',costs=dict(host_start_ns=now,host_end_ns=now+1),
                    raw_summary=summary,status=summary['status'],accepted_pfn_union_bytes=summary['confirmed_pfn_union_bytes'])
                text=json.dumps(record,indent=2)+'\n';before_write(rows.nbytes+len(text.encode()))
                rows.tofile(self.directory/record['binary']);(self.directory/f'chunk-{n:04}.json').write_text(text)
                self.raw_bytes+=rows.nbytes+len(text.encode());self.records.append(record)
                if (mode=='upper_between_chunks' or
                        mode in ('log_during_query','upper_during_query') and first+pages>=(ident['size']+4095)//4096 or
                        mode=='log_late_failure' and self.path==log):
                    self.path.write_bytes(b'changed')
                if mode=='file_change':raise ValueError('file changed; raw retained')
                return record
            def close(self):
                closes.append(self.path.name)
                if self.fd is not None:os.close(self.fd);self.fd=None
                self.cleanup=dict(owned_tracer_exited=mode!='cleanup_fail',tracer_returncode=0 if self.attached else None)
        def recheck():
            checks.append(1);v=copy.deepcopy(raw['bindings'])
            if mode in ('log_growth','log_growth_empty','upper_growth','upper_index_growth'):
                with log.open('ab') as f:f.write(b'x'*4096)
            if mode=='upper_index_prepare32' and len(checks)==2:
                with log.open('ab') as f:f.write(b'x'*96)
            if mode=='strict_change' and len(checks)==1:
                with log.open('ab') as f:f.write(b'x')
            if mode=='strict_after_query' and len(checks)==3:
                with log.open('ab') as f:f.write(b'x')
            if mode in ('log_replace','upper_replace') and len(checks)==2:
                log.unlink();log.write_bytes(b'replacement')
            if mode in ('log_mode_change','upper_mode_change') and len(checks)==2:log.chmod(log.stat().st_mode^0o100)
            if mode in ('log_budget','upper_budget') and len(checks)==2:
                with log.open('r+b') as f:f.truncate(64*2**20)
            if mode=='binding_change' and len(checks)>=3:v['private-a']['pid']=99
            return v
        def plan(*a,**kw):return real_plan(*a,**kw,chunk_pages=2)
        with patch.object(m,'host_boot_id',return_value='boot'),patch.object(m,'HostFilePfnQuery',Query),patch.object(m,'make_plan',side_effect=plan):
            value=m.observe_batch(raw,root,recheck,record_budget=record_budget)
        return raw,value,calls,constructs,closes,queries

    def replay(self,raw,root):
        def plan(*a,**kw):return real_plan(*a,**kw,chunk_pages=2)
        with patch.object(m,'make_plan',side_effect=plan),patch('host_file_pfn_query.MAX_CHUNK_PAGES',2):return m.replay_batch(dict(raw,status='COLLECTED'),root/'host-backing-full-pfn')

    def test_all_files_pinned_first_chunks_and_empty_file_replay_no_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,constructs,closes,queries=self.run_case(root)
            self.assertEqual(v['status'],'PASS',v);self.assertEqual(len(constructs),3);self.assertEqual(len(calls),2)
            self.assertTrue(all(q.fd is None for q in queries));self.assertEqual(len(closes),3)
            evidence,coverage=self.replay(raw,root)
            self.assertEqual(evidence['pfn_attachment']['available_objects'],2)
            self.assertEqual(coverage['known_page_offsets'],4);self.assertEqual(coverage['empty_objects_revalidated'],1)
            self.assertEqual(coverage['unqueried_page_offsets'],0);self.assertEqual(coverage['empty_objects_not_revalidated'],0)
            self.assertIsNone(coverage['cross_object_pfn_union_bytes']);self.assertIsNone(evidence['complete_sandbox_host_cache_bytes'])
            self.assertEqual(v['actual_record_bytes'],m.RecordBudget(root/'host-backing-full-pfn',m.RAW_BUDGET).used())
            with self.assertRaises(FileExistsError):m.observe_batch(raw,root,lambda:raw['bindings'])

    def test_null_returns_are_known_empty_ranges_not_unknown_or_whole_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,*_=self.run_case(root,'empty');self.assertEqual(v['status'],'PASS')
            evidence,coverage=self.replay(raw,root);self.assertEqual(coverage['known_page_offsets'],4)
            self.assertTrue(all(c['confirmed_pfn_union_bytes']==0 for o in evidence['objects'] for c in o['file_page_pfns']['chunks']))
            self.assertIsNone(coverage['complete_sandbox_host_cache_bytes'])

    def test_partial_change_or_cleanup_failure_stops_without_retry_and_closes_all(self):
        for mode in ('partial','file_change','binding_change','cleanup_fail'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,constructs,closes,queries=self.run_case(root,mode)
                self.assertEqual(v['status'],'FAIL',v);self.assertEqual(len(calls),1)
                self.assertTrue(all(q.fd is None for q in queries));self.assertEqual(len(closes),3)
                self.assertTrue((root/'host-backing-full-pfn/failure.txt').is_file())
                with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_global_record_callback_blocks_before_any_raw_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);budget=m.RecordBudget(root,100);(root/'used').write_bytes(b'x'*90)
            with self.assertRaisesRegex(RuntimeError,'strict budget'):budget.check(20)
            with self.assertRaises(RuntimeError):budget.write(root/'new.json',dict(x='y'))
            self.assertFalse((root/'new.json').exists())

    def test_plan_blocked_before_opening_files_or_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=self.fixture(root)
            with patch.object(m,'HostFilePfnQuery') as query,patch.object(m,'host_boot_id',return_value='boot'):
                v=m.observe_batch(raw,root,lambda:raw['bindings'],record_budget=1024)
            self.assertEqual(v['status'],'FAIL');query.assert_not_called()

    def test_replay_rejects_reference_plan_and_file_version_conflicts(self):
        for mode in ('plan','reference','identity'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,*_=self.run_case(root);self.assertEqual(v['status'],'PASS')
                directory=root/'host-backing-full-pfn'
                path=directory/'plan.json' if mode=='plan' else directory/'object-0000/context.json'
                value=json.loads(path.read_text())
                if mode=='plan':value['targets'][0]['size_bytes']+=1
                elif mode=='reference':value['references']=[]
                else:value['identity_after']['ctime_ns']+=1
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):self.replay(raw,root)

    def test_log_growth_during_binding_checks_keeps_inventory_and_query_versions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,constructs,closes,queries=self.run_case(root,'log_growth')
            self.assertEqual(v['status'],'PASS',v);self.assertEqual(len(constructs),3)
            evidence,coverage=self.replay(raw,root);directory=root/'host-backing-full-pfn'
            saved=json.loads((directory/'plan.json').read_text())
            index=next(i for i,t in enumerate(saved['targets']) if t['candidate_path'].endswith('/file-0'))
            cx=json.loads((directory/f'object-{index:04}/context.json').read_text())
            self.assertEqual(cx['inventory_identity']['size'],3*4096)
            self.assertEqual(cx['identity']['size'],6*4096)
            self.assertEqual(cx['query_target']['page_offsets'],6)
            self.assertEqual(cx['before_query']['inventory_differences']['size'],dict(before=3*4096,after=6*4096))
            self.assertEqual(cx['after_query']['expected_differences'],{})
            self.assertIn('size',cx['after_binding_check']['expected_differences'])
            self.assertGreater(v['endpoint_versions'][index]['identity']['size'],cx['identity']['size'])
            self.assertEqual(coverage['inventory_planned_page_offsets'],4)
            self.assertEqual(coverage['total_planned_page_offsets'],7)
            self.assertEqual(coverage['known_page_offsets'],7)
            self.assertEqual(coverage['endpoint_changed_objects'],1)
            self.assertIsNone(coverage['cross_object_pfn_union_bytes'])
            obj=next(o for o in evidence['objects'] if o['object_key']==cx['object_key'])
            self.assertEqual(obj['query']['bytes'],0)  # old mincore semantics untouched
            self.assertEqual(obj['pfn_version_transition']['query'],cx['identity'])
            self.assertEqual(raw['expanded_files']['files'][0]['size'],3*4096)

    def test_empty_log_growth_uses_current_version_instead_of_skipping_original_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'log_growth_empty')
            self.assertEqual(v['status'],'PASS',v);self.assertEqual(len(calls),3)
            evidence,coverage=self.replay(raw,root)
            self.assertEqual(coverage['empty_objects_revalidated'],0)
            self.assertGreater(coverage['total_planned_page_offsets'],coverage['inventory_planned_page_offsets'])
            self.assertEqual(coverage['known_page_offsets'],coverage['total_planned_page_offsets'])

    def test_late_failure_retains_earlier_object_receipts_without_accepting_failed_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'log_late_failure')
            self.assertEqual(v['status'],'FAIL',v);self.assertEqual(len(calls),2)
            self.assertEqual([o['status'] for o in v['objects']],['PASS','PASS','FAIL'])
            directory=root/'host-backing-full-pfn'
            before=json.loads((directory/v['objects'][0]['context']).read_text())
            self.assertEqual(before['status'],'PASS')
            self.assertTrue((directory/'object-0000/query/chunk-0000.bin').exists())
            with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_log_change_inside_query_still_fails_with_field_differences_and_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'log_during_query')
            self.assertEqual(v['status'],'FAIL');self.assertEqual(len(calls),1)
            cx=json.loads((root/'host-backing-full-pfn'/v['objects'][-1]['context']).read_text())
            self.assertEqual(cx['after_query']['status'],'FAIL')
            self.assertIn('size',cx['after_query']['expected_differences'])
            self.assertEqual(cx['query_status'],'FAIL')
            self.assertTrue((root/'host-backing-full-pfn'/v['objects'][-1]['context']).parent.joinpath('query/chunk-0000.bin').exists())
            with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_stable_layer_change_fails_at_pin_with_original_and_observed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'strict_change')
            self.assertEqual(v['status'],'FAIL');self.assertEqual(calls,[])
            self.assertEqual(v['failed_stage'],'pin_all_files')
            index=v['failed_object']['index'];pin=json.loads((root/f'host-backing-full-pfn/object-{index:04}/pin.json').read_text())
            self.assertEqual(pin['observation']['status'],'FAIL')
            self.assertIn('size',pin['observation']['inventory_differences'])

    def test_log_replacement_mode_change_or_budget_growth_stops_before_query(self):
        for mode in ('log_replace','log_mode_change','log_budget'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,*_=self.run_case(root,mode,record_budget=4500000 if mode=='log_budget' else m.RAW_BUDGET)
                self.assertEqual(v['status'],'FAIL',v);self.assertEqual(calls,[])
                cx=json.loads((root/'host-backing-full-pfn'/v['objects'][-1]['context']).read_text())
                if mode=='log_budget':
                    self.assertIn('updated query ranges exceed record budget',cx['error'])
                else:
                    observed=cx['before_probe_preparation']
                    self.assertEqual(observed['status'],'FAIL')
                    field='mode' if mode=='log_mode_change' else 'inode'
                    self.assertTrue(field in observed['expected_differences'] or observed['identity']!=observed['alias'])

    def test_attach_failure_that_already_closed_fd_keeps_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,constructs,closes,queries=self.run_case(root,'attach_failure_closed')
            self.assertEqual(v['status'],'FAIL');self.assertEqual(len(calls),1)
            self.assertIn('owned host-file probe attach failed',v['error'])
            cx=json.loads((root/'host-backing-full-pfn'/v['objects'][-1]['context']).read_text())
            self.assertIn('owned host-file probe attach failed',cx['error'])
            self.assertIn('already closed',cx['failure_observation']['read_error'])
            self.assertTrue(all(q.fd is None for q in queries))

    def test_nonlog_version_is_strict_after_binding_check_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'strict_after_query')
            self.assertEqual(v['status'],'FAIL');self.assertEqual(len(calls),1)
            cx=json.loads((root/'host-backing-full-pfn'/v['objects'][-1]['context']).read_text())
            self.assertEqual(cx['after_query']['status'],'PASS')
            self.assertEqual(cx['after_binding_check']['status'],'FAIL')
            self.assertIn('size',cx['after_binding_check']['expected_differences'])

    def test_replay_rejects_tampered_version_transition_ranges_and_endpoint(self):
        for mode in ('inventory','pin','differences','ranges','endpoint','query_plan','policy'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,*_=self.run_case(root,'log_growth');self.assertEqual(v['status'],'PASS')
                directory=root/'host-backing-full-pfn'
                path=directory/('result.json' if mode=='endpoint' else 'query-plan.json' if mode=='query_plan' else 'object-0000/context.json')
                saved=json.loads(path.read_text())
                if mode=='inventory':saved['inventory_identity']['size']+=1
                elif mode=='pin':saved['pinned_identity']['inode']+=1
                elif mode=='differences':saved['before_query']['inventory_differences']={}
                elif mode=='ranges':saved['query_target']['page_offsets']+=1
                elif mode=='endpoint':saved['endpoint_versions'][0]['query_version_differences']={}
                elif mode=='policy':saved['version_policy']='STRICT_INVENTORY_VERSION'
                else:saved['targets'][0]['size_bytes']+=1
                path.write_text(json.dumps(saved))
                with self.assertRaises(ValueError):self.replay(raw,root)

    def test_legacy_success_replay_keeps_old_contract_and_legacy_failure_stays_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,*_=self.run_case(root);self.assertEqual(v['status'],'PASS')
            directory=root/'host-backing-full-pfn';saved=json.loads((directory/'result.json').read_text())
            saved['version']=m.LEGACY_VERSION;(directory/'result.json').write_text(json.dumps(saved))
            for p in directory.glob('object-*/context.json'):
                cx=json.loads(p.read_text());cx['version']='host-backing-object-pfn-v1';p.write_text(json.dumps(cx))
            evidence,coverage=self.replay(raw,root);self.assertEqual(coverage['known_page_offsets'],4)
            self.assertEqual(evidence['pfn_attachment']['version'],m.LEGACY_VERSION)
            saved['status']='FAIL';(directory/'result.json').write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_identified_upper_data_and_index_rebaseline_only_outside_query(self):
        for mode in ('upper_growth','upper_index_growth'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,*_=self.run_case(root,mode)
                self.assertEqual(v['status'],'PASS',v)
                evidence,coverage=self.replay(raw,root)
                self.assertEqual(coverage['inventory_planned_page_offsets'],4)
                self.assertEqual(coverage['total_planned_page_offsets'],7)
                cx=json.loads((root/'host-backing-full-pfn/object-0000/context.json').read_text())
                self.assertEqual(cx['version_policy'],'LIVE_UPPER_QUERY_VERSION')
                self.assertEqual(cx['identity']['size'],6*4096)
                self.assertEqual(cx['after_query']['expected_differences'],{})
                self.assertIn('size',cx['after_binding_check']['expected_differences'])
                self.assertEqual(raw['expanded_files']['files'][0]['size'],3*4096)
                self.assertEqual(v['upper_version_bindings']['0']['configured_path'],'/proc/42/cwd/overlaybd/active-write.bin')
                self.assertIsNone(evidence['complete_sandbox_host_cache_bytes'])

    def test_upper_needs_configuration_inode_alias_open_fd_and_matching_vmm(self):
        for mode in ('upper_bad_alias','upper_no_fd','upper_lower_conflict','upper_wrong_vmm'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,constructs,*_=self.run_case(root,mode)
                self.assertEqual(v['status'],'FAIL',v);self.assertEqual(calls,[]);self.assertEqual(constructs,[])
                with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_upper_inside_query_change_and_identity_changes_still_fail_without_retry(self):
        for mode in ('upper_during_query','upper_replace','upper_mode_change','upper_budget'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,*_=self.run_case(root,mode,record_budget=4500000 if mode=='upper_budget' else m.RAW_BUDGET)
                self.assertEqual(v['status'],'FAIL',v)
                self.assertEqual(len(calls),1 if mode=='upper_during_query' else 0)
                if mode=='upper_during_query':
                    cx=json.loads((root/'host-backing-full-pfn/object-0000/context.json').read_text())
                    self.assertEqual(cx['after_query']['status'],'FAIL')
                    self.assertIn('size',cx['after_query']['expected_differences'])
                with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_replay_rejects_tampered_upper_binding_and_does_not_apply_it_to_v2(self):
        for mode in ('binding','v2'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,*_=self.run_case(root,'upper_growth');self.assertEqual(v['status'],'PASS')
                directory=root/'host-backing-full-pfn';path=directory/'result.json';saved=json.loads(path.read_text())
                if mode=='binding':saved['upper_version_bindings']['0']['object_key'][2]+=1
                else:
                    saved['version']=m.PREVIOUS_VERSION
                    for p in directory.glob('object-*/context.json'):
                        cx=json.loads(p.read_text());cx['version']=m.PREVIOUS_CONTEXT_VERSION;p.write_text(json.dumps(cx))
                path.write_text(json.dumps(saved))
                with self.assertRaises(ValueError):self.replay(raw,root)

    def test_v2_log_success_still_replays_under_original_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,*_=self.run_case(root,'log_growth');self.assertEqual(v['status'],'PASS')
            directory=root/'host-backing-full-pfn';path=directory/'result.json';saved=json.loads(path.read_text())
            saved['version']=m.PREVIOUS_VERSION;saved.pop('upper_version_bindings');path.write_text(json.dumps(saved))
            for p in directory.glob('object-*/context.json'):
                cx=json.loads(p.read_text());cx['version']=m.PREVIOUS_CONTEXT_VERSION;p.write_text(json.dumps(cx))
            evidence,coverage=self.replay(raw,root)
            self.assertEqual(evidence['pfn_attachment']['version'],m.PREVIOUS_VERSION)
            self.assertEqual(coverage['known_page_offsets'],7)

    def test_probe_preparation_growth_is_recorded_before_final_query_version(self):
        for mode in ('upper_prepare_growth','upper_index_prepare32','log_prepare_growth','log_empty_prepare_growth','log_empty_stays_empty'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,*_=self.run_case(root,mode)
                self.assertEqual(v['status'],'PASS',v)
                evidence,coverage=self.replay(raw,root)
                index=1 if mode.startswith('log_empty') else 0
                cx=json.loads((root/f'host-backing-full-pfn/object-{index:04}/context.json').read_text())
                preparation=cx['probe_preparation'];pre=cx['before_probe_preparation']
                self.assertEqual(preparation['status'],'READY')
                self.assertLessEqual(preparation['end_monotonic_ns'],cx['before_query']['monotonic_ns'])
                self.assertEqual(cx['identity']['size']-pre['identity']['size'],0 if mode=='log_empty_stays_empty' else 32 if mode=='upper_index_prepare32' else 4096)
                if mode=='upper_index_prepare32':
                    self.assertEqual((cx['inventory_identity']['size'],pre['identity']['size'],cx['identity']['size']),(7872,7968,8000))
                self.assertEqual(cx['after_query']['identity'],cx['identity'])
                self.assertEqual(cx['query_invocations'],0 if mode=='log_empty_stays_empty' else 1)
                self.assertEqual(coverage['known_page_offsets'],coverage['total_planned_page_offsets'])
                self.assertIsNone(evidence['complete_sandbox_host_cache_bytes'])

    def test_prepare_replacement_permission_budget_and_attach_failure_stop_and_close(self):
        for mode in ('upper_prepare_replace','upper_prepare_mode','upper_prepare_budget','upper_prepare_attach_fail'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,calls,constructs,closes,queries=self.run_case(root,mode,record_budget=4500000 if mode=='upper_prepare_budget' else m.RAW_BUDGET)
                self.assertEqual(v['status'],'FAIL',v);self.assertEqual(len(calls),1)
                self.assertTrue(all(q.fd is None for q in queries))
                self.assertFalse(any((root/'host-backing-full-pfn').glob('object-*/query/chunk-*.bin')))
                with self.assertRaisesRegex(ValueError,'failure retained'):self.replay(raw,root)

    def test_later_chunk_change_fails_without_rebaseline_or_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,calls,*_=self.run_case(root,'upper_between_chunks')
            self.assertEqual(v['status'],'FAIL',v);self.assertEqual(len(calls),1)
            cx=json.loads((root/'host-backing-full-pfn/object-0000/context.json').read_text())
            self.assertIn('file/alias changed before query',cx['error'])
            self.assertEqual(len(list((root/'host-backing-full-pfn/object-0000/query').glob('chunk-*.bin'))),1)
            self.assertEqual(cx['identity']['size'],3*4096)

    def test_replay_rejects_missing_or_tampered_probe_preparation(self):
        for mode in ('missing','clock','binding','differences'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,v,*_=self.run_case(root,'upper_prepare_growth');self.assertEqual(v['status'],'PASS')
                path=root/'host-backing-full-pfn/object-0000/context.json';cx=json.loads(path.read_text())
                if mode=='missing':cx['probe_preparation']['status']='STARTED'
                elif mode=='clock':cx['probe_preparation']['end_monotonic_ns']=cx['after_query']['monotonic_ns']
                elif mode=='binding':cx['bindings_after_probe_preparation']['private-a']['pid']=99
                else:cx['before_probe_preparation']['inventory_differences']={'size':{'before':0,'after':1}}
                path.write_text(json.dumps(cx))
                with self.assertRaises(ValueError):self.replay(raw,root)

    def test_v3_upper_success_replays_with_original_query_order_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,v,*_=self.run_case(root,'upper_growth');self.assertEqual(v['status'],'PASS')
            directory=root/'host-backing-full-pfn';path=directory/'result.json';saved=json.loads(path.read_text())
            saved['version']=m.UPPER_VERSION;path.write_text(json.dumps(saved))
            for p in directory.glob('object-*/context.json'):
                cx=json.loads(p.read_text());cx['version']=m.UPPER_CONTEXT_VERSION
                for k in ('probe_preparation','before_probe_preparation','bindings_after_probe_preparation'):cx.pop(k,None)
                p.write_text(json.dumps(cx))
            evidence,coverage=self.replay(raw,root)
            self.assertEqual(evidence['pfn_attachment']['version'],m.UPPER_VERSION)
            self.assertEqual(coverage['known_page_offsets'],7)

    def test_cli_forwards_explicit_option_and_rejects_scope_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kernel=root/'.artifacts/kernel';kernel.parent.mkdir();kernel.write_bytes(b'fake')
            out=root/'.artifacts/out';argv=['entry','--inside','--unit','fake','--output',str(out),'--kernel',str(kernel),
                '--host-backing-evidence','--host-backing-full-pfn-evidence']
            with patch.object(entry,'ROOT',root),patch.object(entry,'inside',return_value=0) as inside,patch.object(sys,'argv',argv):
                self.assertEqual(entry.main(),0);self.assertTrue(inside.call_args.kwargs['host_backing_full_pfn_evidence'])
                for extra in (['--guests','2'],['--host-backing-pfn-evidence']):
                    with patch.object(sys,'argv',argv+extra),self.assertRaisesRegex(ValueError,'full-list PFNs'):entry.main()
                self.assertEqual(inside.call_count,1)


if __name__=='__main__':unittest.main()
