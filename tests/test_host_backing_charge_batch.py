"""Fake proc/cgroup trees, files, libc and probe only; never touch live services."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_backing_pfn_batch as batch
import host_backing_pfn_plan as plan
import host_backing_charge_scope as scope
import host_file_page_charge as charge
import host_file_pfn_query as pfn
import test_host_backing_pfn_batch as old


class FakeScope:
    def __init__(self):self.history=SimpleNamespace(records=[])
    def capture(self,raw):
        now=time.monotonic_ns();parent='/sys/fs/cgroup/asbvmmtest.slice'
        groups=[]
        for name,role,ident in [('', 'parent',700),('/control.service','controller',701),
                                ('/docker-test.scope','docker',702),('/vmm-1','vmm:private-a',777)]:
            groups.append(dict(host_boot_id='boot',path=parent+name,role=role,inode=ident,device=31,
                               id_kind='cgroupfs_st_ino',first_seen_ns=now,last_seen_ns=now,first_absent_ns=None))
        self.history.records=copy.deepcopy(groups)
        return dict(version='host-backing-charge-scope-v1',host_boot_id='boot',parent=parent,
                    groups=groups,start_monotonic_ns=now,end_monotonic_ns=time.monotonic_ns(),
                    sandboxes={'private-a':dict(sandbox_id='sid',pid=42,start_ticks='7',
                        cgroup=parent+'/vmm-1',cgroup_id=777,creation='clone3_CLONE_INTO_CGROUP')})


class FakeQuery(charge.HostFileChargeQuery):
    owner=777
    state=1
    calls=0
    def __init__(self,path,directory,*,expected_identity):
        self.path=Path(path);self.directory=Path(directory);self.fd=os.open(path,os.O_RDONLY)
        self.initial=pfn.file_identity(os.fstat(self.fd));self.raw_bytes=0;self.records=[];self.token=0
        self.pid=os.getpid();self.tid=threading.get_native_id();self.kernel={};self.magic=0xef53
        self.permission=dict(host=dict(host_root_cap_fowner=True),thread=dict(basis='HOST_CAP_FOWNER'))
        self._permission=Mock(return_value=self.permission);self._context=Mock();self.attached=False
        self.batch=Mock();self.batch.buffer_bytes=0;self.batch.last_statistics=dict(returncode=-2)
        self.batch.read.side_effect=self.entries
        self.libc=SimpleNamespace(mmap=Mock(return_value=0x100000),mincore=self.mincore,munmap=Mock(return_value=0))
        self.tracer=Mock();self.tracer.poll.return_value=None;self.cleanup=None
    def mincore(self,address,length,vector):
        FakeQuery.calls+=1
        self.begin=time.monotonic_ns()
        for i in range(len(vector)):vector[i]=1
        self.end=time.monotonic_ns();return 0
    def entries(self,ordered,token):
        return {int(i):(pfn.FOLIO|(100+int(i)),self.state,self.owner,self.owner,2,self.begin,self.end) for i in ordered}
    def attach(self):self.attached=True
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
        self.cleanup=dict(owned_tracer_exited=True,tracer_returncode=0 if self.attached else None)


class ChargeBatchTests(unittest.TestCase):
    def case(self,root,owner=777,state=1):
        raw=old.BatchPfnTests().fixture(root);FakeQuery.calls=0
        with patch.object(charge,'HostFileChargeQuery',FakeQuery),patch.object(FakeQuery,'owner',owner),\
             patch.object(FakeQuery,'state',state),patch.object(batch,'host_boot_id',return_value='boot'):
            value=batch.observe_batch(raw,root,lambda:copy.deepcopy(raw['bindings']),charge_scope=FakeScope())
        return raw,value

    def test_batch_executes_new_rows_and_replays_binding_without_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,value=self.case(root)
            self.assertEqual(value['status'],'PASS',value)
            evidence,coverage=batch.replay_batch(raw,root/'host-backing-full-pfn')
            self.assertEqual(value['version'],batch.CHARGE_VERSION)
            self.assertEqual(coverage['known_page_offsets'],4)
            self.assertEqual(coverage['empty_objects_revalidated'],1)
            self.assertEqual(evidence['pfn_attachment']['page_charge_owner'],'OBSERVED_PER_CHUNK')
            objects=[o for o in evidence['objects'] if 'page_charge_attribution' in o]
            self.assertEqual(len(objects),2)
            for obj in objects:
                owner=obj['page_charge_attribution']['chunks'][0]['owners'][0]
                self.assertEqual(owner['sandbox_id'],'sid')
                self.assertEqual(owner['group_role'],'vmm:private-a')
                self.assertIsNone(obj['page_charge_attribution']['cross_chunk_physical_bytes'])
            self.assertIsNone(coverage['complete_sandbox_host_cache_bytes'])
            saved=json.loads((root/'host-backing-full-pfn/plan.json').read_text())
            self.assertEqual(saved['budget']['binary_row_bytes'],4*80)

    def test_unlisted_owner_remains_unknown_not_assigned_to_file_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,value=self.case(root,owner=999)
            self.assertEqual(value['status'],'PASS',value)
            evidence,_=batch.replay_batch(raw,root/'host-backing-full-pfn')
            obj=next(o for o in evidence['objects'] if 'page_charge_attribution' in o)
            a=obj['page_charge_attribution'];self.assertEqual(a['status'],'PARTIAL')
            owner=a['chunks'][0]['owners'][0]
            self.assertIsNone(owner['sandbox_id']);self.assertEqual(owner['identity_status'],'UNKNOWN_UNLISTED_GROUP')

    def test_control_cost_not_assigned_to_referencing_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,value=self.case(root,owner=701)
            self.assertEqual(value['status'],'PASS')
            evidence,_=batch.replay_batch(raw,root/'host-backing-full-pfn')
            obj=next(o for o in evidence['objects'] if 'page_charge_attribution' in o)
            owner=obj['page_charge_attribution']['chunks'][0]['owners'][0]
            self.assertEqual(owner['group_role'],'controller');self.assertIsNone(owner['sandbox_id'])

    def test_unknown_stops_batch_no_retry_preserves_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,value=self.case(root,state=4)
            self.assertEqual(value['status'],'FAIL');self.assertEqual(FakeQuery.calls,1)
            self.assertTrue(list((root/'host-backing-full-pfn').glob('object-*/query/chunk-*.bin')))
            with self.assertRaisesRegex(ValueError,'failure retained'):batch.replay_batch(raw,root/'host-backing-full-pfn')

    def test_tampered_raw_charge_summary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,value=self.case(root)
            directory=root/'host-backing-full-pfn'
            path=next(directory.glob('object-*/query/chunk-*.bin'))
            rows=np.fromfile(path,dtype='<u8').reshape(-1,10);rows[0,5:7]=900;rows.tofile(path)
            with self.assertRaisesRegex(ValueError,'summary differs'):batch.replay_batch(raw,directory)

    def test_changed_scope_or_wrong_sandbox_rejected(self):
        for change in ('id','sandbox','time'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,value=self.case(root);directory=root/'host-backing-full-pfn'
                path=directory/'object-0000/context.json';data=json.loads(path.read_text())
                if change=='id':data['charge_scope_after']['groups'][0]['inode']+=1
                if change=='sandbox':data['charge_scope_before']['sandboxes']['private-a']['sandbox_id']='wrong'
                if change=='time':data['charge_scope_before']['end_monotonic_ns']=data['end_monotonic_ns']+1
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):batch.replay_batch(raw,directory)

    def test_80_byte_budget_blocks_before_any_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=old.BatchPfnTests().fixture(root)
            old_plan=plan.make_plan(dict(raw,status='COLLECTED'))
            limit=old_plan['budget']['planned_record_bytes']+1
            self.assertEqual(plan.make_plan(dict(raw,status='COLLECTED'),record_budget=limit)['status'],'PLANNED_NOT_EXECUTED')
            self.assertEqual(plan.make_plan(dict(raw,status='COLLECTED'),record_budget=limit,row_bytes=80)['status'],'BLOCKED')
            with patch.object(charge,'HostFileChargeQuery') as q,patch.object(batch,'host_boot_id',return_value='boot'):
                result=batch.observe_batch(raw,root,lambda:raw['bindings'],record_budget=limit,charge_scope=FakeScope())
            self.assertEqual(result['status'],'FAIL');q.assert_not_called()

    def test_changed_size_replans_at_80_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=old.BatchPfnTests().fixture(Path(tmp));target=plan.make_plan(dict(raw,status='COLLECTED'),row_bytes=80)['targets'][0]
            target['categories']=['owned_logs'];identity=pfn.file_identity(Path(target['candidate_path']).stat());identity['size']=8192
            new=batch.query_target(target,identity,131072,row_bytes=80)
            self.assertEqual(new['binary_row_bytes'],160)

    def fake_tree(self,root):
        proc=root/'proc';cg=root/'cg';parent=cg/'asbvmmtest.slice'
        groups={r:parent/name for r,name in [('controller','control.service'),('docker','docker.scope'),('vmm','vmm-1')]}
        for path in groups.values():path.mkdir(parents=True,exist_ok=True)
        (proc/'sys/kernel/random').mkdir(parents=True);(proc/'sys/kernel/random/boot_id').write_text('boot\n')
        (proc/'42').mkdir();(proc/'42/cgroup').write_text('0::/asbvmmtest.slice/vmm-1\n')
        (proc/'42/stat').write_text('42 (firecracker) '+' '.join(['0']*19+['7']))
        (groups['vmm']/'cgroup.procs').write_text('42\n')
        raw=dict(guests={'private-a':'sid'},bindings={'private-a':dict(pid=42,start_ticks='7',boot_id='boot')})
        birth={'private-a':dict(status='PASS',actual_cgroup='/asbvmmtest.slice/vmm-1',
             cgroup_birth=dict(birth_cgroup='0::/asbvmmtest.slice/vmm-1',creation='clone3_CLONE_INTO_CGROUP'),
             registration_identity=dict(cgroup_id=groups['vmm'].stat().st_ino,start_ticks=7),vmm_binding=raw['bindings']['private-a'])}
        capture=scope.ChargeScope(parent,{k:v for k,v in groups.items() if k!='vmm'},birth,proc_root=proc,cgroup_root=cg)
        return capture,raw,proc,groups

    def test_live_scope_reads_fake_proc_and_binds_original_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture,raw,proc,groups=self.fake_tree(Path(tmp));receipt=capture.capture(raw)
            self.assertEqual(receipt['sandboxes']['private-a']['sandbox_id'],'sid')
            self.assertEqual(len(receipt['groups']),4)
            self.assertEqual(receipt['sandboxes']['private-a']['cgroup_id'],groups['vmm'].stat().st_ino)

    def test_stale_process_group_id_missing_and_extra_member_fail(self):
        for mode in ('pid','id','missing','members','boot'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                capture,raw,proc,groups=self.fake_tree(Path(tmp))
                if mode=='pid':raw['bindings']['private-a']['start_ticks']='8'
                if mode=='id':capture.birth['private-a']['registration_identity']['cgroup_id']+=1
                if mode=='missing':groups['docker'].rmdir()
                if mode=='members':(groups['vmm']/'cgroup.procs').write_text('42\n43\n')
                if mode=='boot':(proc/'sys/kernel/random/boot_id').write_text('other')
                with self.assertRaises(ValueError):capture.capture(raw)


if __name__=='__main__':unittest.main()
