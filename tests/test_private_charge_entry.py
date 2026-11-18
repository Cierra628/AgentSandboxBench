"""Offline only: fake receipts/process metadata, no service or page query."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_backing_charge_scope import private_scope
import run_live_child_calibration as entry
import test_private_final_acceptance as fixtures

class ChargeEntryTests(unittest.TestCase):
    def test_scope_uses_real_controller_not_setup_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cg=root/'cgroup';parent=cg/'asbvmmtest.slice';proc=root/'proc'
            out=root/'storage/mount/controller/guest-cache';(out/'live-cache').mkdir(parents=True)
            (proc/'self').mkdir(parents=True);(proc/'self/cgroup').write_text('0::/asbvmmtest.slice/control.service\n')
            setup=dict(status='PASS',parent=str(parent),control=str(parent/'docker.scope'))
            path=out.parents[2]/'vmm-cgroup-setup.json';path.write_text(json.dumps(setup))
            (out/'live-cache/vmm-child-binding.json').write_text('{"status":"PASS"}')
            scope=private_scope(out,parent,proc_root=proc,cgroup_root=cg)
            self.assertEqual(scope.services['controller'],parent/'control.service')
            self.assertEqual(scope.services['docker'],parent/'docker.scope')
            setup['parent']=str(cg/'wrong');path.write_text(json.dumps(setup))
            with self.assertRaisesRegex(ValueError,'conflict'):private_scope(out,parent,proc_root=proc,cgroup_root=cg)

    def fixture(self,root):
        f=fixtures.PrivateFinalAcceptanceTests();out,storage,volume=f.fixture(root)
        read=lambda p:json.loads(p.read_text())
        launch=read(out/'launch.json');launch['host_backing_charge_evidence']=True;f.write(out/'launch.json',launch)
        outer=read(out/'result.json');outer['measurement_endpoint']={'status':'OBSERVED'}
        outer['memory_window_checks']['full_collection']['periodic']['status']='FAIL';f.write(out/'result.json',outer)
        group=dict(path='/fake',role='parent',inode=1,device=1)
        scope=dict(host_boot_id='boot',parent='/fake',groups=[group],sandboxes={})
        receipt=dict(version='host-backing-full-charge-v1',status='PASS',charge_scope_before=scope,
                     charge_scope_after=scope,charge_identity_history=[group])
        evidence=dict(charge_scope=dict(before=scope,after=scope,history=[group]),
            pfn_attachment=dict(version=receipt['version'],page_charge_owner='OBSERVED_PER_CHUNK',
                available_objects=1,charge_identity_status='PARTIAL'),
            objects=[dict(page_charge_attribution=dict(status='PARTIAL',chunks=[dict(unknown_page_observations=0)]))])
        base=storage/'guest-cache/host-backing-full-pfn'
        f.write(base/'result.json',receipt);f.write(base/'evidence.json',evidence)
        return f,out,volume,base,evidence

    def test_counter_failure_is_diagnostic_unknown_group_not_assigned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);f,out,volume,base,evidence=self.fixture(root)
            final=f.final(root,out)
            self.assertEqual(final['status'],'PASS',final)
            self.assertEqual(final['memory']['status'],'FAIL')
            self.assertEqual(final['memory']['acceptance_role'],'DIAGNOSTIC_ONLY')
            self.assertEqual(final['page_charge']['identity_status'],'PARTIAL');self.assertTrue(volume.exists())

    def test_missing_query_unknown_page_cleanup_and_endpoint_still_fail(self):
        for mode in ('missing','unknown','version','cleanup','endpoint'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);f,out,volume,base,evidence=self.fixture(root)
                if mode=='missing':(base/'evidence.json').unlink()
                if mode=='unknown':
                    evidence['objects'][0]['page_charge_attribution']['chunks'][0]['unknown_page_observations']=1
                    f.write(base/'evidence.json',evidence)
                if mode=='version':
                    evidence['pfn_attachment']['version']='old';f.write(base/'evidence.json',evidence)
                if mode=='endpoint':
                    outer=json.loads((out/'result.json').read_text());outer.pop('measurement_endpoint');f.write(out/'result.json',outer)
                result=f.final(root,out,cleanup={'status':'FAIL'} if mode=='cleanup' else None)
                self.assertEqual(result['status'],'FAIL',result);self.assertTrue(volume.exists())

    def test_missing_final_check_preserves_other_verdicts_and_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);f,out,volume,base,evidence=self.fixture(root)
            missing=base.parent/'dynamic-file-check.json';missing.unlink()
            result=f.final(root,out,rc=1)
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(set(result['failed_checks']),{'pipeline','dynamic_file'})
            self.assertTrue(result['checks']['page_charge'])
            self.assertTrue(result['checks']['ram_evidence']);self.assertTrue(result['checks']['vmm_evidence'])
            self.assertTrue(result['checks']['volume_retained']);self.assertTrue(volume.exists())
            self.assertIn(str(missing),result['errors'][0])
            saved=json.loads((out/'result.json').read_text())
            self.assertEqual(saved['fixture_final_disposition'],'RETAINED_FOR_EXPLICIT_CLEANUP')

    def test_flag_requires_full_before_any_launch(self):
        with patch.object(sys,'argv',['entry','--output','/unused','--host-backing-charge-evidence']),patch.object(entry.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'full-list'):entry.main()
            run.assert_not_called()

    def test_inside_forwarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kernel=root/'.artifacts/kernel';kernel.parent.mkdir();kernel.touch()
            argv=['entry','--output',str(root/'.artifacts/test'),'--kernel',str(kernel),'--inside',
                '--host-backing-evidence','--host-backing-full-pfn-evidence','--host-backing-charge-evidence']
            with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),patch.object(entry,'inside',return_value=0) as inside:
                self.assertEqual(entry.main(),0);self.assertTrue(inside.call_args.kwargs['host_backing_charge_evidence'])

if __name__=='__main__':unittest.main()
