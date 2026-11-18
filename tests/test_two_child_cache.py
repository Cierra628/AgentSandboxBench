"""Two guest orchestration uses mock CLI and files; no services or devices."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_guest_cache_two_children as runner
import report_two_child_cache as report
import numpy as np
from kernel_cache_metadata import HEADER,PRESENT
from run_live_child_calibration import volume_gib
from isolated_storage import capacity_budget,GIB
from live_task_cache import LiveTaskCache


class TwoChildCacheTest(unittest.TestCase):
    def case(self,fail_start=False,fail_evidence=False,dynamic=False,ram=False,backing=False,fail_backing=False,vmm=False,io_uring=False):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'mount/controller/guest-cache';out.parent.mkdir(parents=True)
            order=[];calls=[];next_sid=iter(('sid-a','sid-b'))
            class Live:
                def __init__(self,out,container,cli,session,**options):
                    expected=dict(file_cache_path="/workspace/asb-child-cache.bin",expected_kernel="6.1.175-asb-file-query-v1") if dynamic else {}
                    if ram:expected['ram_page_evidence']=True
                    if vmm:expected['vmm_page_evidence']=True
                    if io_uring:expected['io_uring_page_evidence']=True
                    assert options==expected
                    self.role=session;order.append(('capture',session));self.error=None
                    n=runner.ROLES.index(session)
                    self.capture=SimpleNamespace(child_scope=True,cgpath=Path('/fake-parent'))
                    self.vm={'pid':100+n,'cgroup':'/fake-parent/vmm-'+str(n)}
                def start(self,sid):
                    order.append(('start',self.role))
                    if fail_start and self.role=='private-b':raise RuntimeError('wrong registration')
                def close(self):order.append(('close',self.role))
            def create(*args,**kw):
                sid=next(next_sid);order.append(('create',sid));self.assertTrue(kw['local_no_proxy']);return sid
            def command(argv,**kw):
                calls.append(argv)
                return SimpleNamespace(stdout=b'{"status":"PASS"}',stderr=b'',returncode=0,check_returncode=lambda:None)
            checked={'status':'FAIL' if fail_evidence else 'PASS','errors':['wrong target'] if fail_evidence else []}
            argv=['runner','--container','owned','--config-home',str(Path(tmp)/'config'),
                  '--service-url','http://127.0.0.1:12345','--output',str(out)]+(['--dynamic-file'] if dynamic else [])+(['--ram-page-evidence'] if ram else [])+(['--host-backing-evidence'] if backing else [])
            if vmm:argv+=['--vmm-page-evidence']
            if io_uring:argv+=['--io-uring-page-evidence']
            def backing_observation(container,path,guests,expected):
                self.assertEqual(guests,dict(zip(runner.ROLES,('sid-a','sid-b'))))
                self.assertEqual(set(expected),set(runner.ROLES))
                self.assertEqual(order[-2:],[('close','private-a'),('close','private-b')])
                if fail_backing:raise RuntimeError('backing identity changed')
                return dict(status='PARTIAL',raw='host-backing-raw.json')
            with patch.object(sys,'argv',argv),patch.object(runner,'LiveTaskCache',Live),patch.object(runner,'create',create),\
                 patch.object(runner.subprocess,'run',side_effect=command),patch.object(runner.time,'sleep'),\
                 patch.object(runner,'identity'),patch.object(runner,'sample_groups',return_value={'observed':True}),\
                 patch.object(runner,'read_vmm',return_value={'pss_kib':1024}),\
                 patch.object(runner,'check_targets',return_value=checked) as old_check,\
                 patch('host_backing_evidence.observe',side_effect=backing_observation) as backing_call,\
                 patch.object(runner,'check_dynamic_targets',return_value=checked) as new_check,contextlib.redirect_stdout(io.StringIO()):
                code=runner.main()
                if not backing:backing_call.assert_not_called()
                if backing and not fail_start:backing_call.assert_called_once()
                if dynamic and not fail_start and not fail_backing:
                    old_check.assert_not_called();new_check.assert_called_once()
            return code,json.loads((out/'result.json').read_text()),order,calls

    def test_both_captures_precede_both_creates_and_eviction_is_separate(self):
        code,value,order,calls=self.case();self.assertEqual(code,0)
        self.assertEqual(order[:4],[('capture','private-a'),('capture','private-b'),('create','sid-a'),('create','sid-b')])
        self.assertEqual([p['name'] for p in value['phases']],['both_written','both_read','a_evicted_b_retained','both_evicted'])
        evict=[c for c in calls if c[-1]=='/dev/shm/asb-child-fixture/evict']
        self.assertEqual([c[2] for c in evict],['sid-a','sid-b'])
        self.assertEqual(sum('delete' in c for c in calls),2)
        self.assertFalse(any(any(v in c for v in ('pause','resume','snapshot')) for c in calls))

    def test_dynamic_path_keeps_asymmetric_commands_and_downloads_separate(self):
        code,value,order,calls=self.case(dynamic=True)
        self.assertEqual(code,0)
        self.assertEqual([p['name'] for p in value['phases']],['both_written','a_evicted','a_reread','b_evicted','b_reread'])
        commands=[(c[2],c[-1].rsplit('/',1)[-1]) for c in calls if 'touch' in c and c[-1].rsplit('/',1)[-1] in ('evict','reread')]
        self.assertEqual(commands,[('sid-a','evict'),('sid-a','reread'),('sid-b','evict'),('sid-b','reread')])
        self.assertEqual(sum('download' in c for c in calls),6)
        self.assertEqual(sum(k=='create' for k,_ in order),2)
        self.assertFalse(any(any(v in c for v in ('pause','resume','snapshot')) for c in calls))

    def test_second_identity_failure_retains_fail_without_recreate(self):
        code,value,order,calls=self.case(fail_start=True)
        self.assertEqual(code,1);self.assertEqual(value['failed_stage'],'start-collector-private-b')
        self.assertEqual(sum(kind=='create' for kind,_ in order),2)
        self.assertEqual(sum('delete' in c for c in calls),2)

    def test_explicit_ram_evidence_reaches_both_private_collectors(self):
        code,value,order,calls=self.case(dynamic=True,ram=True)
        self.assertEqual(code,0)
        self.assertEqual(sum(kind=='capture' for kind,_ in order),2)

    def test_explicit_vmm_evidence_reaches_both_private_collectors(self):
        code,value,order,calls=self.case(dynamic=True,ram=True,vmm=True)
        self.assertEqual(code,0)
        self.assertEqual(sum(kind=='capture' for kind,_ in order),2)

    def test_explicit_io_uring_evidence_reaches_both_private_collectors(self):
        code,value,order,calls=self.case(dynamic=True,ram=True,vmm=True,io_uring=True)
        self.assertEqual(code,0)
        self.assertEqual(sum(kind=='capture' for kind,_ in order),2)

    def test_backing_query_runs_once_after_collectors_before_vm_cleanup(self):
        code,value,order,calls=self.case(dynamic=True,ram=True,backing=True)
        self.assertEqual(code,0);self.assertEqual(value['host_backing_evidence']['status'],'PARTIAL')
        self.assertEqual(sum('delete' in c for c in calls),2)

    def test_backing_identity_failure_stops_without_recreate(self):
        code,value,order,calls=self.case(backing=True,fail_backing=True)
        self.assertEqual(code,1);self.assertEqual(value['failed_stage'],'host_backing_evidence')
        self.assertEqual(sum(k=='create' for k,_ in order),2)
        self.assertEqual(sum('delete' in c for c in calls),2)

    def test_evidence_failure_is_not_hidden_by_successful_fixture(self):
        code,value,_,_=self.case(fail_evidence=True)
        self.assertEqual(code,1);self.assertEqual(value['failed_stage'],'saved_page_evidence_check')

    def test_private_sessions_do_not_start_restore_queries(self):
        for name in runner.ROLES:
            live=LiveTaskCache.__new__(LiveTaskCache);live.session=name;live.backend=None
            live._start_snapshot_queries(Path('/nonexistent'))
            self.assertIsNone(live.backend)

    def test_budgets_and_single_guest_default_are_distinct(self):
        self.assertEqual(volume_gib(1),12);self.assertEqual(volume_gib(2),16)
        self.assertFalse(capacity_budget(42*GIB,16*GIB)['ready'])
        self.assertTrue(capacity_budget(42*GIB+1,16*GIB)['ready'])

    def saved_pairs(self,out,bad_b=False):
        run={'phases':[dict(name=name,start_monotonic_ns=i*100,end_monotonic_ns=i*100+90)
                       for i,name in enumerate(report.EXPECTED)],'guests':{}}
        pfns=np.arange(1,4097,dtype='<u8');tags={str(int(p)):2 for p in pfns}
        present=np.column_stack((pfns,np.full(4096,2,dtype='<u8'))).tobytes()
        def metadata(full):
            return HEADER.pack(b'ASBCACH1',1,4096,1<<21,4096 if full else 0,1,0,0,0,0,0,0,0)+(present if full else b'')
        for ri,role in enumerate(report.ROLES):
            live=out/'live-cache'/role;live.mkdir(parents=True)
            (out/(role+'-written.json')).write_text(json.dumps({'lifetime_tags':tags}))
            (live/'vmm-child-binding.json').write_text(json.dumps({'registration_identity':{'cgroup_id':ri+1}}))
            (live/'result.json').write_text(json.dumps({'status':'PASS'}))
            run['guests'][role]={'host_identity':{'pid':100+ri}};samples=[]
            for i,(name,expected) in enumerate(report.EXPECTED.items()):
                full=bool(expected[ri]) and not (bad_b and role=='private-b' and name=='a_evicted_b_retained')
                for j in range(2):
                    point=i*2+j;prefix=live/f'pair-{point:04}'
                    Path(str(prefix)+'-before.bin').write_bytes(metadata(full));Path(str(prefix)+'-after.bin').write_bytes(metadata(full))
                    entries=pfns+10000+ri*10000+PRESENT
                    host=np.column_stack((pfns,entries,entries,entries)) if full else np.empty((0,4),dtype='<u8')
                    Path(str(prefix)+'-host.bin').write_bytes(host.astype('<u8').tobytes())
                    samples.append(dict(point=point,host_sample_start_ns=i*100+j*10+1,host_elapsed_ns=5,
                        guest_cache_after_bytes=16*2**20 if full else 0,host_confirmed_cache_bytes=16*2**20 if full else 0,
                        unconfirmed_after_pages=0,host_thread_cpu_ns=2,guest_publication_before={},guest_start_ns=0))
            (live/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples))
        return run

    def test_saved_binary_pages_confirm_asymmetric_eviction(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);run=self.saved_pairs(out)
            value=report.check_targets(out,run)
            self.assertEqual(value['status'],'PASS',value['errors'])
            self.assertEqual(value['shared_page_deduplication'],'NOT_VALIDATED_PRIVATE_GUESTS')

    def test_saved_binary_pages_detect_b_cache_disappearing_too_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);run=self.saved_pairs(out,bad_b=True)
            value=report.check_targets(out,run)
            self.assertEqual(value['status'],'FAIL')
            self.assertTrue(all('private-b a_evicted_b_retained' in e for e in value['errors']))


if __name__=='__main__':unittest.main()
