import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tool_trajectory as trace
import platform_management as management
from remove_approved_isolated_volume import remove
from platform_tasks import configuration,commands


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cwd=self.root/'guest';self.cwd.mkdir();self.file=self.cwd/'state';self.raw=self.root/'capture'
        trace.begin(self.raw,dict(session_semantics='fresh bash per tool call; filesystem persists',tracked_files=[str(self.file)]),{str(self.file):None})
    def cli(self,label,*args,**kw):
        return subprocess.check_output([sys.executable,str(Path(trace.__file__).with_name('guest_tool_capture.py')),args[-1]],text=True)
    def capture(self):
        for command in ("printf alpha > state; cat state","printf failed >&2; exit 7","printf beta >> state; cat state"):
            trace.capture(self.raw,self.cli,'guest',command,cwd=str(self.cwd),env={'PATH':'/usr/bin:/bin'},timeout_seconds=5)
        return trace.freeze(self.raw,{str(self.file):trace.sha(self.file)})
    def test_real_execution_failure_preserved_and_replayed(self):
        manifest=self.capture();self.assertEqual(manifest['expected_failures'],{'002':7});self.file.unlink()
        proof=trace.replay(self.raw,self.root/'replay',self.cli,'other')
        self.assertEqual(proof['status'],'PASS');self.assertEqual(self.file.read_text(),'alphabeta')
    def test_modified_or_added_files_rejected(self):
        self.capture();p=self.raw/'steps/001/stdout.bin';p.write_bytes(b'changed')
        with self.assertRaises(AssertionError):trace.validate(self.raw)
        p.write_bytes(b'alpha');trace.validate(self.raw)
        (self.raw/'steps/001/manifest.json').write_text('{}')
        with self.assertRaises(AssertionError):trace.validate(self.raw)
    def test_replay_state_and_output_mismatch_rejected(self):
        self.capture()
        with self.assertRaises(AssertionError):trace.replay(self.raw,self.root/'wrong-state',self.cli,'other')
        self.file.unlink()
        def wrong(label,*args,**kw):
            result=json.loads(self.cli(label,*args,**kw))
            if 'exit_code' in result:result['exit_code']=99
            return json.dumps(result)
        with self.assertRaises(AssertionError):trace.replay(self.raw,self.root/'wrong-output',wrong,'other')
    def test_frozen_trace_cannot_append(self):
        self.capture()
        with self.assertRaises(AssertionError):trace.capture(self.raw,self.cli,'guest','true',cwd=str(self.cwd),env={})


class ManagementTests(unittest.TestCase):
    def test_resource_catalog_does_not_export_tokens(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(management,'ROOT',Path(temp)):
            output=Path(temp)/'.artifacts/query'
            row={'sandboxID':'id','accessToken':'secret','metadata':{'secret':'x'}}
            with patch.object(management.subprocess,'run',return_value=type('Reply',(),dict(returncode=0,stdout=json.dumps([row])))()):
                result=management.resources(Path(temp)/'config',output)
            self.assertNotIn('secret',(output/'result.json').read_text());self.assertEqual(result['resources']['instances'],[{'sandboxID':'id'}])
    def test_retained_run_blocks_cleanup_before_accessing_devices(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'.artifacts/storage-isolated-service/run';directory.mkdir(parents=True)
            (directory/'result.json').write_text('{"status":"FAIL"}')
            with patch.object(management,'ROOT',root),patch('remove_approved_isolated_volume.ROOT',root):
                management.retain(directory,'retain evidence')
                with patch('remove_approved_isolated_volume.subprocess.check_output') as query:
                    with self.assertRaisesRegex(ValueError,'retained'):remove(directory,'authorized')
                    query.assert_not_called()
    def test_workflow_retention_protects_linked_storage_run(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(management,'ROOT',Path(temp)):
            root=Path(temp);workflow=root/'.artifacts/platform-tasks/run';child=root/'.artifacts/storage-isolated-service/run'
            workflow.mkdir(parents=True);child.mkdir(parents=True)
            (child/'result.json').write_text('{}');(workflow/'result.json').write_text(json.dumps(dict(storage_result_dir=str(child))))
            management.retain(workflow,'keep this workflow')
            self.assertTrue(management.protected(child))
    def test_demo_dispatch_does_not_run_benchmark_or_restore(self):
        cfg=configuration(Path(management.ROOT/'configs/platform-sphinx7590.json'))
        check,live=commands(cfg,Path('/unused'),tool_capture=True)
        self.assertIn('--python-tool-capture-demo',live);self.assertNotIn('--python-sphinx-pipeline-diagnostic',live)
        self.assertIn('run_tool_capture.py',check[2]);cfg['checkpoint_after_step']=29
        with self.assertRaises(ValueError):commands(cfg,Path('/unused'),tool_capture=True)


if __name__=='__main__':unittest.main()
