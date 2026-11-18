"""Temporary fixtures and saved JSON only. No service, mount, loop, or live query."""
import ast
import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_live_child_calibration as entry
from experiment_memory import normalize_groups

SOURCE=Path(__file__).resolve().parents[1]/'scripts/validate_isolated_agentenv.py'
tree=ast.parse(SOURCE.read_text())
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='finish_fixture')
namespace={}
exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(SOURCE),'exec'),namespace)
finish_fixture=namespace['finish_fixture']


class PrivateFinalAcceptanceTests(unittest.TestCase):
    def row(self,timestamp):
        parent=Path('/fake/asbvmmtest.slice')
        fields=dict(current=8192,file=4096,anon=2048,kernel=2048)
        groups={str(parent):fields,str(parent/'controller.service'):fields}
        return dict(monotonic_ns=timestamp,duration_ns=1000,groups=groups,experiment_memory=normalize_groups(parent,groups))

    def windows(self):
        return entry.memory_window_checks([self.row(i*100000000) for i in range(6)],
            dict(status='OBSERVED',snapshot=self.row(250000000)),self.row(600000000))

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))

    def fixture(self,root):
        out=root/'.artifacts/batch';out.mkdir(parents=True)
        storage=root/'.artifacts/storage-isolated-service/owned';storage.mkdir(parents=True)
        volume=storage/'volume.ext4';volume.write_bytes(b'temporary fake volume')
        stored=dict(status='PASS',cleanup=dict(owned_container_removed=True,mount_and_loop_removed=True))
        finish_fixture(volume,True,stored,stored['cleanup'],retain_for_final_acceptance=True)
        self.write(storage/'result.json',stored)
        raw=storage/'guest-cache'
        for name,status in [('result.json','PASS'),('live-cache/result.json','PASS'),
                ('live-cache/vmm-child-binding.json','PASS'),
                ('dynamic-file-check.json','PASS'),('live-cache/ram-page-analysis.json','OBSERVED'),
                ('live-cache/vmm-page-analysis.json','OBSERVED_EVIDENCE'),('host-backing-full-pfn/result.json','PASS')]:
            self.write(raw/name,dict(status=status))
        self.write(raw/'host-backing-full-pfn/coverage.json',dict(known_page_offsets=1,total_planned_page_offsets=1,
            unqueried_page_offsets=0,unknown_observed_page_offsets=0,empty_objects_not_revalidated=0))
        self.write(out/'launch.json',dict(dynamic_file=True,ram_page_evidence=True,vmm_page_evidence=True))
        self.write(out/'result.json',dict(status='PASS',storage=str(storage),source_returned_to_stopped=True,
            sampling=dict(status='PASS'),memory_window_checks=self.windows()))
        self.write(root/'.artifacts/prettier6604/child-cache-batch/resources.json',dict(released=True))
        return out,storage,volume

    def final(self,root,out,rc=0,cleanup=None):
        with patch.object(entry,'ROOT',root):
            return entry.finalize_full_pfn_acceptance(out,rc,cleanup or dict(status='PASS'))

    def test_ordinary_success_and_failure_keep_original_volume_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            volume=Path(tmp)/'volume.ext4';volume.write_bytes(b'fixture');receipt={};cleanup={}
            finish_fixture(volume,True,receipt,cleanup)
            self.assertFalse(volume.exists());self.assertTrue(cleanup['successful_fixture_removed'])
            self.assertFalse(receipt['failure_fixture_retained']);self.assertNotIn('fixture_retention',receipt)
            volume.write_bytes(b'fixture');receipt={};cleanup={}
            finish_fixture(volume,False,receipt,cleanup)
            self.assertTrue(volume.exists());self.assertTrue(cleanup['failed_fixture_retained'])

    def test_private_inner_pass_retains_volume_even_if_outer_never_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            volume=Path(tmp)/'volume.ext4';volume.write_bytes(b'fixture');receipt={};cleanup={}
            finish_fixture(volume,True,receipt,cleanup,retain_for_final_acceptance=True)
            self.assertTrue(volume.exists());self.assertEqual(receipt['fixture_retention']['status'],'PENDING_FINAL_ACCEPTANCE')
            self.assertTrue(cleanup['fixture_retained_for_final_acceptance']);self.assertNotIn('successful_fixture_removed',cleanup)

    def test_private_inner_failure_retains_and_records_original_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            volume=Path(tmp)/'volume.ext4';volume.write_bytes(b'fixture');receipt=dict(status='FAIL');cleanup={}
            finish_fixture(volume,False,receipt,cleanup,retain_for_final_acceptance=True)
            self.assertTrue(volume.exists());self.assertEqual(receipt['status'],'FAIL')
            self.assertEqual(receipt['fixture_retention']['status'],'RETAINED_AFTER_INNER_FAIL')

    def test_endpoint_failure_is_final_failure_without_deleting_or_relabeling_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out,storage,volume=self.fixture(root)
            saved=json.loads((out/'result.json').read_text())
            saved['memory_window_checks']['measurement']['checks']['endpoint_file_closure'].update(status='MISMATCH',parent_minus_children_bytes=-33751040)
            self.write(out/'result.json',saved)
            final=self.final(root,out)
            self.assertEqual(final['status'],'FAIL');self.assertIn('measurement_endpoint_file',final['memory']['failed_checks'])
            outer=json.loads((out/'result.json').read_text())
            self.assertEqual(outer['pipeline_status'],'PASS');self.assertEqual(outer['status'],'FAIL');self.assertTrue(volume.exists())

    def test_final_pass_keeps_volume_for_explicit_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out,storage,volume=self.fixture(root);final=self.final(root,out)
            self.assertEqual(final['status'],'PASS',final);self.assertTrue(volume.exists())
            self.assertEqual(json.loads((out/'result.json').read_text())['fixture_final_disposition'],'RETAINED_FOR_EXPLICIT_CLEANUP')

    def test_missing_final_evidence_fails_and_retains_volume(self):
        for missing in ('result','endpoint','coverage'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out,storage,volume=self.fixture(root)
                if missing=='result':(out/'result.json').unlink()
                elif missing=='coverage':(storage/'guest-cache/host-backing-full-pfn/coverage.json').unlink()
                else:
                    saved=json.loads((out/'result.json').read_text());saved['memory_window_checks']['measurement']=dict(status='UNAVAILABLE')
                    self.write(out/'result.json',saved)
                final=self.final(root,out);self.assertEqual(final['status'],'FAIL');self.assertTrue(volume.exists())

    def test_incomplete_point_and_unavailable_peak_do_not_pass_when_thresholds_pass(self):
        for mode in ('incomplete','peak'):
            windows=copy.deepcopy(self.windows())
            if mode=='incomplete':
                windows['full_collection']['periodic'].update(status='FAIL',data_complete=False,incomplete_indices=[3])
            elif mode=='peak':windows['full_collection']['peaks']['file']['value_bytes']=None
            self.assertEqual(entry.memory_acceptance(windows)['status'],'FAIL')
        # Do not turn a short cleanup curve's different denominator into a new
        # overall acceptance rule. Full collection and fixed endpoints govern.
        windows=self.windows();windows['cleanup']['checks']['periodic']['status']='FAIL'
        self.assertEqual(entry.memory_acceptance(windows)['status'],'PASS')

    def test_slice_failure_or_inner_failure_cannot_get_final_pass(self):
        for mode in ('slice','inner'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out,storage,volume=self.fixture(root)
                final=self.final(root,out,rc=1 if mode=='inner' else 0,cleanup=dict(status='FAIL' if mode=='slice' else 'PASS'))
                self.assertEqual(final['status'],'FAIL');self.assertTrue(volume.exists())

    def test_explicit_retain_flag_is_scoped_to_full_list_and_forwarded_only_there(self):
        source=SOURCE.read_text();runner=(entry.ROOT/'scripts/run_live_child_calibration.py').read_text()
        self.assertIn("if retain_fixture and not host_backing_full_pfn_evidence:",source)
        self.assertIn("['--guest-cache-host-backing-full-pfn-evidence','--retain-fixture-for-final-acceptance'] if host_backing_full_pfn_evidence else []",runner)
        flag=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_argument'
            and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='--retain-fixture-for-final-acceptance')
        self.assertEqual(next(k.value.value for k in flag.keywords if k.arg=='action'),'store_true')

    def test_outer_exit_follows_final_acceptance_only_for_explicit_full_list(self):
        for full,status,expected in ((True,'FAIL',1),(True,'PASS',0),(False,'UNUSED',7)):
            with self.subTest(full=full,status=status),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);kernel=root/'.artifacts/kernel';kernel.parent.mkdir();kernel.write_bytes(b'fake')
                out=root/'.artifacts/new-run';argv=['entry','--output',str(out),'--kernel',str(kernel)]
                if full:argv+=['--host-backing-evidence','--host-backing-full-pfn-evidence']
                with patch.object(entry,'ROOT',root),patch.object(sys,'argv',argv),\
                        patch.object(entry,'available',return_value=100*2**30),patch.object(entry.shutil,'copy2'),\
                        patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0 if full else 7)),\
                        patch.object(entry,'release_empty_slice',return_value=dict(status='PASS')) as release,\
                        patch.object(entry,'finalize_full_pfn_acceptance',return_value=dict(status=status)) as final,\
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(entry.main(),expected)
                    if full:
                        final.assert_called_once_with(out,0,dict(status='PASS'));release.assert_called_once()
                        self.assertTrue(json.loads((out/'launch.json').read_text())['retain_fixture_for_final_acceptance'])
                    else:final.assert_not_called();release.assert_not_called()


if __name__=='__main__':unittest.main()
