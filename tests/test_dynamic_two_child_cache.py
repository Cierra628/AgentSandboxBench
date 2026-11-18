"""Synthetic two-guest receipts and mocked rejoin; no live service or devices."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import report_dynamic_two_child_cache as report
import report_dynamic_file_calibration as single
import test_dynamic_file_calibration as single_fixtures


class DynamicTwoChildTests(unittest.TestCase):
    def fixture(self,root):
        run=dict(phases=[dict(name=name,start_ns=i*100,end_ns=i*100+90) for i,name in enumerate(report.PHASES)],guests={})
        for index,role in enumerate(report.ROLES):
            temp=root/role;temp.mkdir();single_fixtures.DynamicCalibrationTests().fixture(temp)
            live=root/'live-cache'/role;live.parent.mkdir(exist_ok=True)
            (temp/'live-cache').rename(live)
            for name in ('written','evicted','reread'):
                for suffix in ('.json','-file.bin'):(temp/(name+suffix)).rename(root/(role+'-'+name+suffix))
            sid=role;boot='boot-'+role
            vm=dict(pid=100+index,start_time_ticks=200+index,cgroup='/owned/vmm-'+role)
            run['guests'][role]=dict(sandbox_id=sid,host_identity=vm)
            self.write(live/'marker.json',dict(sandbox_id=sid,boot_id=boot))
            self.write(live/'result.json',dict(status='PASS',sandbox_id=sid,guest_boot_id=boot,host_identity=vm))
            self.write(live/'vmm-child-binding.json',dict(status='PASS',actual_cgroup=vm['cgroup'],
                vmm_binding=dict(pid=vm['pid']),registration_identity=dict(start_ticks=vm['start_time_ticks'],cgroup_id=index+1)))
            observations=[];samples=[]
            for i,phase in enumerate(report.PHASES):
                # B can lose all cached pages while only A is being evicted.
                full=phase in ('both_written','a_reread','b_reread')
                observations.append(dict(point=i,current_file_cache_after_bytes=4096 if full else 0,confirmed_host_union_bytes=4096 if full else 0))
                samples.append(dict(point=i,timestamp_ns=i*100+1,host_elapsed_ns=1))
            self.write(live/'dynamic-file-cache.json',dict(sandbox_id=sid,boot_id=boot,observations=observations))
            (live/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples))
        return run

    def write(self,p,v):p.write_text(json.dumps(v))

    def check(self,root,run):
        def rejoin(live,side):
            self.assertEqual(side,live/'file-query-download/file-query')
            return json.loads((live/'dynamic-file-cache.json').read_text())
        with patch.object(single,'SIZE',8192),patch.object(report,'analyze',side_effect=rejoin):
            return report.check_targets(root,run)

    def test_peer_cache_loss_does_not_fail_and_guest_local_inodes_can_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=self.fixture(root);value=self.check(root,run)
            self.assertEqual(value['status'],'PASS',value['errors'])
            self.assertEqual(value['guests']['private-b']['phases']['a_evicted'][0]['current_file_cache_after_bytes'],0)

    def test_wrong_sandbox_or_collector_binding_is_rejected(self):
        for field in ('sandbox_id','host_identity'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);run=self.fixture(root);run['guests']['private-b'][field]=copy.deepcopy(run['guests']['private-a'][field])
                with self.assertRaisesRegex(ValueError,'identity mismatch'):self.check(root,run)

    def test_same_pid_cgroup_or_boot_cannot_pass(self):
        for field in ('pid','cgroup','boot'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);run=self.fixture(root);live=root/'live-cache/private-b'
                vm=run['guests']['private-b']['host_identity']
                if field=='boot':
                    for filename,key in (('marker.json','boot_id'),('result.json','guest_boot_id'),('dynamic-file-cache.json','boot_id')):
                        value=json.loads((live/filename).read_text());value[key]='boot-private-a';self.write(live/filename,value)
                else:
                    vm[field]=run['guests']['private-a']['host_identity'][field]
                    value=json.loads((live/'result.json').read_text());value['host_identity']=vm;self.write(live/'result.json',value)
                    value=json.loads((live/'vmm-child-binding.json').read_text())
                    if field=='pid':value['vmm_binding']['pid']=vm[field]
                    else:value['actual_cgroup']=vm[field]
                    self.write(live/'vmm-child-binding.json',value)
                self.assertEqual(self.check(root,run)['status'],'FAIL')

    def test_sidecars_cannot_be_substituted_with_another_guests_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=self.fixture(root)
            with patch.object(report,'analyze',return_value={'sandbox_id':'wrong'}):
                with self.assertRaisesRegex(ValueError,'binary evidence'):report.check_targets(root,run)

    def test_missing_boundary_binary_is_not_silently_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=self.fixture(root);(root/'private-b-evicted-file.bin').unlink()
            with self.assertRaises(FileNotFoundError):self.check(root,run)


if __name__=='__main__':unittest.main()
