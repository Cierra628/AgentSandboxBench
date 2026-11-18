"""No services/devices: exercise the explicit one-guest workflow with fake CLI."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_guest_cache_child as runner
import run_live_child_calibration as entry
from report_live_child_calibration import target_observation
import numpy as np
from kernel_cache_metadata import PRESENT
from isolated_storage import capacity_budget,GIB


class LiveChildCalibrationTest(unittest.TestCase):
    def run_case(self,fail_start=False,dynamic=False,backing=False,vmm=False,io_uring=False,pfn=False,full_pfn=False,backing_fail=False,charge=False,finish_fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'mount/controller/guest-cache';out.parent.mkdir(parents=True)
            order=[];test=self
            class Live:
                def __init__(self,*args,**kwargs):
                    test.assertEqual(kwargs.get('vmm_page_evidence',False),vmm)
                    test.assertEqual(kwargs.get('io_uring_page_evidence',False),io_uring)
                    order.append('capture');self.capture=types.SimpleNamespace(child_scope=True,cgpath=Path('/fake-slice'))
                    self.vm={'pid':123};self.error=None
                def start(self,sid):
                    order.append('start')
                    if fail_start:raise RuntimeError('registration identity mismatch')
                def close(self):order.append('close')
            def create(*args,**kwargs):
                order.append('create');self.assertTrue(kwargs['local_no_proxy']);self.assertEqual(args[2],runner.IMAGE)
                return 'test-sid'
            calls=[]
            def command(argv,**kwargs):
                calls.append(argv)
                if finish_fail and any('seq 1 100' in str(a) and '/done' in str(a) for a in argv):
                    raise runner.subprocess.CalledProcessError(1,argv,output=b'AssertionError: finish deadline')
                if 'cat' in argv:
                    payload={'status':'PASS'} if str(argv[-1]).endswith('result.json') else {'timestamp_ns':42}
                    return types.SimpleNamespace(stdout=json.dumps(payload).encode(),stderr=b'',returncode=0,check_returncode=lambda:None)
                return types.SimpleNamespace(stdout=b'',stderr=b'',returncode=0,check_returncode=lambda:None)
            argv=['runner','--container','owned','--config-home',str(Path(tmp)/'config'),
                  '--service-url','http://127.0.0.1:12345','--output',str(out)]
            if dynamic:argv+=['--dynamic-file']
            if backing:argv+=['--host-backing-evidence']
            if pfn:argv+=['--host-backing-pfn-evidence']
            if full_pfn:argv+=['--host-backing-full-pfn-evidence']
            if charge:argv+=['--host-backing-charge-evidence']
            if vmm:argv+=['--ram-page-evidence','--vmm-page-evidence']
            if io_uring:argv+=['--io-uring-page-evidence']
            def backing_observation(container,path,guests,expected,**kwargs):
                expected_options=dict(full_pfn_evidence=True) if full_pfn else dict(pfn_evidence=True) if pfn else {}
                if charge:expected_options['charge_scope']='fake-explicit-scope'
                self.assertEqual(kwargs,expected_options)
                self.assertEqual(order[-1],'close');self.assertEqual(guests,{'private-a':'test-sid'})
                self.assertEqual(expected,{'private-a':{'pid':123}})
                if backing_fail:raise RuntimeError('upper query failed; no retry')
                return dict(status='PARTIAL')
            endpoint_positions=[]
            def group_snapshot(parent,**kwargs):
                if kwargs.get('diagnostics'):
                    self.assertEqual(parent,Path('/fake-slice'));endpoint_positions.append(len(calls))
                return {'observed':True}
            with patch.object(sys,'argv',argv),patch.object(runner,'LiveTaskCache',Live),patch.object(runner,'create',create),\
                 patch.object(runner.subprocess,'run',side_effect=command),patch.object(runner.time,'sleep'),\
                 patch.object(runner,'identity'),patch.object(runner,'sample_groups',side_effect=group_snapshot),\
                 patch('host_backing_charge_scope.private_scope',return_value='fake-explicit-scope'),\
                 patch('host_backing_evidence.observe',side_effect=backing_observation) as backing_call,\
                 patch('report_dynamic_file_calibration.check',return_value={'status':'PASS','errors':[]}),\
                 patch.object(runner,'read_vmm',return_value={'pss_kib':1024}),contextlib.redirect_stdout(io.StringIO()):
                rc=runner.main()
                if backing and not fail_start:backing_call.assert_called_once()
                if not backing:backing_call.assert_not_called()
            value=json.loads((out/'result.json').read_text())
            if full_pfn:
                value['test_endpoint_positions']=endpoint_positions
                value['test_endpoint_file_exists']=(out/'measurement-endpoint.json').is_file()
            return rc,value,order,calls

    def test_charge_scope_reaches_observer_after_close(self):
        rc,value,order,calls=self.run_case(backing=True,full_pfn=True,charge=True)
        self.assertEqual(rc,0);self.assertEqual(order.count('create'),1)

    def test_charge_wait_budget_and_ordinary_defaults(self):
        for charge in (False,True):
            rc,value,order,calls=self.run_case(backing=charge,full_pfn=charge,charge=charge)
            self.assertEqual(rc,0)
            command=next(c[-1] for c in calls if any('nohup timeout' in str(a) for a in c))
            self.assertIn('timeout 210' if charge else 'timeout 150',command)
            self.assertEqual('--finish-timeout-seconds 180' in command,charge)
            self.assertEqual(value['fixture_timeouts']['finish_seconds'],180 if charge else 120)
            self.assertEqual(value['fixture_timeouts']['other_phase_seconds'],120)

    def test_finish_failure_has_correct_stage_and_keeps_endpoint(self):
        rc,value,order,calls=self.run_case(backing=True,full_pfn=True,charge=True,finish_fail=True)
        self.assertEqual(rc,1);self.assertEqual(value['failed_stage'],'fixture_finish_wait')
        self.assertEqual(value['measurement_endpoint']['status'],'OBSERVED')
        self.assertTrue(value['vm_removed']);self.assertEqual(sum('delete' in c for c in calls),1)

    def test_one_running_vm_file_phases_and_cleanup(self):
        rc,value,order,calls=self.run_case()
        self.assertEqual(rc,0);self.assertEqual(order,['capture','create','start','close'])
        self.assertEqual([p['name'] for p in value['phases']],['written','read-done','evicted'])
        self.assertTrue(value['vm_removed']);self.assertFalse(value['vm_pause']);self.assertFalse(value['checkpoint'])
        self.assertEqual(sum('delete' in c for c in calls),1)
        self.assertFalse(any(any(x in c for x in ['snapshot','pause','resume']) for c in calls))

    def test_explicit_vmm_evidence_reaches_single_private_collector(self):
        rc,value,order,calls=self.run_case(dynamic=True,vmm=True)
        self.assertEqual(rc,0)
        self.assertEqual(order.count('capture'),1)

    def test_explicit_io_uring_evidence_reaches_single_private_collector(self):
        rc,value,order,calls=self.run_case(dynamic=True,vmm=True,io_uring=True)
        self.assertEqual(rc,0)
        self.assertEqual(order.count('capture'),1)

    def test_identity_failure_stops_without_retry(self):
        rc,value,order,calls=self.run_case(fail_start=True)
        self.assertEqual(rc,1);self.assertEqual(value['status'],'FAIL')
        self.assertEqual(value['failed_stage'],'start_live_cache');self.assertIn('identity mismatch',value['error'])
        self.assertEqual(order.count('create'),1);self.assertEqual(order.count('start'),1)
        self.assertEqual(sum('delete' in c for c in calls),1)

    def test_backing_query_is_explicit_and_after_cache_close(self):
        rc,value,order,calls=self.run_case(dynamic=True,backing=True)
        self.assertEqual(rc,0);self.assertEqual(value['host_backing_evidence']['status'],'PARTIAL')
        self.assertEqual(sum('delete' in c for c in calls),1)

    def test_explicit_single_file_pfn_reaches_after_close_observer(self):
        rc,value,order,calls=self.run_case(dynamic=True,backing=True,pfn=True)
        self.assertEqual(rc,0);self.assertEqual(order.count('create'),1)

    def test_explicit_full_file_pfn_reaches_after_close_observer(self):
        rc,value,order,calls=self.run_case(dynamic=True,backing=True,full_pfn=True)
        self.assertEqual(rc,0);self.assertEqual(order.count('create'),1)
        self.assertEqual(value['measurement_endpoint']['status'],'OBSERVED')
        self.assertTrue(value['test_endpoint_file_exists'])
        self.assertEqual(len(value['test_endpoint_positions']),1)
        before=calls[:value['test_endpoint_positions'][0]]
        self.assertFalse(any('delete' in c or '/dev/shm/asb-child-fixture/finish' in c for c in before))
        self.assertIn('guest_cleanup_start_monotonic_ns',value)

    def test_failed_full_query_has_no_successful_endpoint_or_cleanup_fallback(self):
        rc,value,order,calls=self.run_case(dynamic=True,backing=True,full_pfn=True,backing_fail=True)
        self.assertEqual(rc,1);self.assertEqual(value['measurement_endpoint']['status'],'UNAVAILABLE')
        self.assertFalse(value['test_endpoint_file_exists']);self.assertEqual(value['test_endpoint_positions'],[])
        self.assertEqual(sum('delete' in c for c in calls),1)
        self.assertEqual(value['failed_stage'],'host_backing_evidence')

    def test_dynamic_path_evicts_then_rereads_once_without_pause(self):
        rc,value,order,calls=self.run_case(dynamic=True)
        self.assertEqual(rc,0)
        self.assertEqual([p['name'] for p in value['phases']],['written','evicted','reread'])
        markers=[c[-1] for c in calls if 'touch' in c]
        self.assertEqual(markers,['/dev/shm/asb-child-fixture/evict','/dev/shm/asb-child-fixture/reread','/dev/shm/asb-child-fixture/finish'])
        self.assertEqual(order.count('create'),1)
        self.assertEqual(sum('delete' in c for c in calls),1)

    def test_volume_budget_keeps_original_reserve_and_margin(self):
        self.assertEqual(entry.VOLUME_GIB,12)
        self.assertFalse(capacity_budget(38*GIB,12*GIB)['ready'])
        self.assertTrue(capacity_budget(38*GIB+1,12*GIB)['ready'])

    def test_fixture_does_not_lock_or_globally_drop_file_cache(self):
        source=(runner.ROOT/'scripts/guest_cache_child_fixture.py').read_text()
        self.assertNotIn('mlock(',source);self.assertNotIn("write_text('3",source)
        self.assertIn('os.POSIX_FADV_DONTNEED',source)

    def test_target_counts_union_not_logical_capacity(self):
        metadata=({},np.array([[1,2],[2,4]],dtype='<u8'))
        host=np.array([[1,PRESENT|100,PRESENT|100,PRESENT|100],
                       [2,PRESENT|100,PRESENT|100,PRESENT|100]],dtype='<u8')
        value=target_observation(metadata,metadata,host,{'1':2,'2':4})
        self.assertEqual(value['target_cache_after_bytes'],8192)
        self.assertEqual(value['target_confirmed_host_union_bytes'],4096)
        self.assertEqual(value['target_unconfirmed_after_pages'],0)

    def test_reused_guest_pfn_not_counted_as_old_target(self):
        before=({},np.array([[1,2]],dtype='<u8'));after=({},np.array([[1,6]],dtype='<u8'))
        host=np.array([[1,PRESENT|100,PRESENT|100,PRESENT|100]],dtype='<u8')
        value=target_observation(before,after,host,{'1':2})
        self.assertEqual(value['target_cache_after_bytes'],0)
        self.assertEqual(value['target_confirmed_host_union_bytes'],0)

    def test_mapping_change_stays_unconfirmed(self):
        metadata=({},np.array([[1,2]],dtype='<u8'))
        host=np.array([[1,PRESENT|100,PRESENT|101,PRESENT|101]],dtype='<u8')
        value=target_observation(metadata,metadata,host,{'1':2})
        self.assertEqual(value['target_confirmed_host_union_bytes'],0)
        self.assertEqual(value['target_unconfirmed_after_pages'],1)


if __name__=='__main__':unittest.main()
