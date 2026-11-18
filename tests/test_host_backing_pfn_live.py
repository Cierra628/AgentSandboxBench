"""Temporary files and simulated probe rows only; no service, mincore or BPF."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_pfn_live as m
import host_backing_pfns as attachment
from host_file_pfn_query import file_identity,decode_rows,VERSION,FOLIO
from host_backing_evidence import summarize
import run_live_child_calibration as entry


class JointFilePfnTests(unittest.TestCase):
    def fixture(self,out):
        path=out/'first-lower';path.write_bytes(b'x'*(300*4096))
        identity=file_identity(path.stat());binding=dict(pid=42,boot_id='boot',start_ticks='7')
        raw=dict(status='COLLECTED',guests={'private-a':'sid'},bindings={'private-a':binding},
            rootfs_inventory=dict(status='IDENTIFIED',guests={'private-a':dict(status='IDENTIFIED',
                lowers=[dict(index=0,path=str(path),status='IDENTIFIED',**identity)])}),
            expanded_files=dict(status='OBSERVED',files=[dict(status='OBSERVED',device=identity['device'],inode=identity['inode'],
                categories=['active_snapshot_rootfs_lower'],users=['private-a'],aliases=[dict(path=str(path))])]),cache_layers={})
        return path,identity,raw

    def run_case(self,out,mode='present'):
        path,identity,raw=self.fixture(out);calls=[];checks=[]
        def recheck():
            checks.append(1);vm=copy.deepcopy(raw['bindings'])
            if mode=='binding_change' and len(checks)==2:vm['private-a']['pid']=99
            return vm
        def query(file,expected,first,pages,directory):
            calls.append((file,expected,first,pages));directory.mkdir()
            if mode=='query_fail':return dict(status='FAIL')
            rows=np.array([[i,1,1,FOLIO|i+100] if mode!='empty' else [i,0,1,0] for i in range(pages)],dtype='<u8')
            summary=decode_rows(rows);rows.tofile(directory/'chunk-0000.bin')
            now=time.monotonic_ns()
            chunk=dict(first_page=0,pages=pages,mapping_length=pages*4096,token=1,query_process=os.getpid(),
                object_stable=True,probe_alive_after=True,identity_before=identity,identity_after=identity,alias_after=identity,
                permission=dict(host=dict(host_root_cap_fowner=True),thread=dict(basis='HOST_CAP_FOWNER')),
                binary='chunk-0000.bin',costs=dict(host_start_ns=now,host_end_ns=now+1),
                raw_summary=summary,status=summary['status'],accepted_pfn_union_bytes=summary['confirmed_pfn_union_bytes'])
            q=dict(version=VERSION,status='OBSERVED_NONATOMIC',identity=identity,expected_identity=list(expected),
                path=str(file),page_size=4096,first_page=0,pages=pages,chunks=[chunk],
                cleanup=dict(owned_tracer_exited=True,tracer_returncode=0))
            (directory/'result.json').write_text(json.dumps(q))
            if mode=='file_change':path.write_bytes(b'changed')
            return q
        if mode=='unidentified':raw['rootfs_inventory']['guests']['private-a']['lowers'][0]['status']='UNKNOWN'
        with patch.object(m,'host_boot_id',return_value='boot'),patch.object(m,'observe_query',side_effect=query):
            receipt=m.observe_one(raw,out,recheck)
        return raw,receipt,calls,checks

    def test_first_lower_bounded_query_attaches_one_current_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);raw,r,calls,checks=self.run_case(out)
            self.assertEqual(r['status'],'PASS');self.assertEqual(len(calls),1);self.assertEqual(len(checks),2)
            self.assertEqual(calls[0][2:],(0,256));raw['single_file_pfn']=r
            inv=summarize(raw);q=attachment.load_query(out/'host-backing-pfn-query/result.json',out/'host-backing-single-pfn.json')
            joined=attachment.attach(inv,q);self.assertEqual(joined['pfn_attachment']['available_objects'],1)
            obj=joined['objects'][0];self.assertEqual(obj['references'],[dict(role='private-a',sandbox_id='sid')])
            self.assertEqual(obj['file_page_pfns']['chunks'][0]['confirmed_pfn_union_bytes'],2**20)
            self.assertIsNone(joined['complete_sandbox_host_memory_bytes'])
            self.assertIsNone(obj['file_page_pfns']['complete_object_physical_bytes'])
            with self.assertRaises(FileExistsError):m.observe_one(raw,out,lambda:raw['bindings'])
            raw['bindings']['private-a']['pid']=999
            with self.assertRaisesRegex(ValueError,'VMM identities'):summarize(raw)

    def test_observed_empty_range_is_not_missing_or_whole_file_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);raw,r,_,_=self.run_case(out,'empty');self.assertEqual(r['status'],'PASS')
            raw['single_file_pfn']=r
            q=attachment.load_query(out/'host-backing-pfn-query/result.json',out/'host-backing-single-pfn.json')
            obj=attachment.attach(summarize(raw),q)['objects'][0]
            self.assertEqual(obj['file_page_pfns']['chunks'][0]['complete_listed_pfn_union_bytes'],0)
            self.assertIsNone(obj['file_page_pfns']['complete_object_physical_bytes'])

    def test_failure_or_change_preserves_failure_no_retry_or_target_switch(self):
        for mode in ('query_fail','binding_change','file_change','unidentified'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);raw,r,calls,_=self.run_case(out,mode)
                self.assertEqual(r['status'],'FAIL');self.assertEqual(len(calls),0 if mode=='unidentified' else 1)
                self.assertTrue((out/'host-backing-single-pfn-failure.txt').is_file())
                self.assertEqual(json.loads((out/'host-backing-single-pfn.json').read_text())['status'],'FAIL')

    def test_cli_forwards_optin_and_rejects_invalid_scope_before_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kernel=root/'.artifacts/kernel';kernel.parent.mkdir();kernel.write_bytes(b'fake')
            out=root/'.artifacts/out';argv=['entry','--inside','--unit','fake','--output',str(out),'--kernel',str(kernel),
                '--host-backing-evidence','--host-backing-pfn-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0)
                self.assertTrue(inside.call_args.kwargs['host_backing_pfn_evidence'])
                for extra in (['--guests','2'],[]):
                    bad=argv+extra if extra else [x for x in argv if x!='--host-backing-evidence']
                    with patch.object(sys,'argv',bad),self.assertRaisesRegex(ValueError,'one private guest'):
                        entry.main()
                self.assertEqual(inside.call_count,1)


if __name__=='__main__':unittest.main()
