"""Offline Xarray local mode: toy files, local Shell and mocked API/service calls only."""
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import prettier_no_proxy as n
import agentenv_cold_start as cold
import platform_tasks as platform
import run_host_file_workflow as outer
import host_persistent_input as persistent
import test_sphinx_no_proxy as shared


def config():return platform.configuration(ROOT/'configs/platform-xarray6992-host-current.json')


class XarrayLocalTests(unittest.TestCase):
    def metadata(self,path):
        path.mkdir(parents=True)
        raw=json.dumps(dict(config=dict(size=8085,digest='sha256:'+'a'*64),layers=[dict(size=1) for _ in range(13)])).encode()
        (path/'manifest.json').write_bytes(raw)
        return 'sha256:'+hashlib.sha256(raw).hexdigest()

    def cache(self,root):
        folder=root/'image-cache/configs';folder.mkdir(parents=True)
        lowers=[]
        for i in range(13):
            p=root/f'image-cache/commits/layer-{i}.commit';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'toy cache')
            lowers.append(dict(file='/workspace/env/'+str(p.relative_to(root)),size=p.stat().st_size))
        stem='sha256-'+n.XARRAY_DIGEST[7:]+'-image';path=folder/(stem+'.json')
        path.write_text(json.dumps(dict(repoBlobUrl='',lowers=lowers)))
        (folder/(stem+'.metadata.json')).write_text('{}')
        tools=root/'deps/tools/0.1.1';tools.mkdir(parents=True);(tools/'image.json').write_text('{}')
        return path

    def test_manifest_only_export_never_reads_docker_config_and_rejects_wrong_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin=self.metadata(root/'source')
            with patch.object(n,'XARRAY_DIGEST',pin),patch.object(n,'XARRAY_MANIFEST',root/'source/manifest.json'),patch.object(n.subprocess,'check_output') as docker:
                n.prepare_metadata(root/'metadata',task_id=n.XARRAY_TASK)
                self.assertEqual([p.name for p in (root/'metadata').iterdir()],['manifest.json'])
                self.assertEqual(n.metadata(root/'metadata',task_id=n.XARRAY_TASK)['config']['size'],8085);docker.assert_not_called()
                (root/'metadata/manifest.json').write_text('{}')
                with self.assertRaises(ValueError):n.metadata(root/'metadata',task_id=n.XARRAY_TASK)

    def test_exact_local_shell_requests_and_other_task_pins_never_fallback(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.metadata(root/'metadata');(root/'metadata/version.txt').write_text('VCSTag: v0.11.5\n')
            logs=root/'logs';logs.mkdir();entry=root/'regctl'
            entry.write_text(n.wrapper(root/'metadata','sha256:'+'a'*64,task_id=n.XARRAY_TASK).replace('/workspace/env/logs',str(logs)))
            for args,name in ((['version'],'version.txt'),(['manifest','get','--format','raw-body',n.XARRAY_IMAGE],'manifest.json')):
                got=subprocess.run(['sh',entry,*args],capture_output=True,timeout=3)
                self.assertEqual(got.returncode,0,got.stderr);self.assertEqual(got.stdout,(root/'metadata'/name).read_bytes())
            blocked=[['manifest','get','--format','raw-body',p] for p in (n.IMAGE,n.SPHINX_IMAGE)]+[
                ['blob','get',n.XARRAY_IMAGE.split('@')[0],'sha256:'+'a'*64],['image','copy',n.XARRAY_IMAGE,'ocidir:///unused'],['version','--format','json']]
            for args in blocked:
                got=subprocess.run(['sh',entry,*args],capture_output=True,timeout=3);self.assertEqual(got.returncode,78)
            self.assertEqual((logs/'local-no-proxy-regctl.log').read_text().splitlines(),['version','manifest']+['blocked']*len(blocked))
            self.assertNotIn('regctl.real',entry.read_text())

    def test_cache_identity_size_remote_duplicate_and_missing_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.cache(root);original=p.read_text()
            self.assertEqual(n.check_cache(root,task_id=n.XARRAY_TASK)['image'],n.XARRAY_IMAGE)
            for change in ('size','remote','duplicate','count'):
                v=json.loads(original)
                if change=='size':v['lowers'][0]['size']+=1
                if change=='remote':v['repoBlobUrl']='https://unwanted.invalid/'
                if change=='duplicate':v['lowers'][1]=v['lowers'][0]
                if change=='count':v['lowers'].pop()
                p.write_text(json.dumps(v))
                with self.subTest(change=change),self.assertRaises(ValueError):n.check_cache(root,task_id=n.XARRAY_TASK)
            p.write_text(original);metadata=p.with_name(p.name.replace('-image.json','-image.metadata.json'));metadata.unlink()
            with self.assertRaises(FileNotFoundError):n.check_cache(root,task_id=n.XARRAY_TASK)
            metadata.write_text('{}');(root/'image-cache/commits/layer-0.commit').unlink()
            with self.assertRaises(ValueError):n.check_cache(root,task_id=n.XARRAY_TASK)

    def test_install_preserves_wrapper_on_missing_or_bad_real_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'seed';pin=self.metadata(seed/'local-no-proxy-metadata')
            mount=root/'mount';entry=mount/'env/deps/regctl/v0.11.5/regctl';entry.parent.mkdir(parents=True);entry.write_text('# never execute old wrapper\n')
            with patch.object(n,'XARRAY_DIGEST',pin),patch.object(n,'dependency_version',return_value='VCSTag: v0.11.5\n') as version:
                self.cache(seed/'input/env')
                with self.assertRaises(ValueError):n.install(seed,mount,task_id=n.XARRAY_TASK)
                version.assert_not_called();real=entry.with_name('regctl.real');real.write_bytes(b'\x7fELFtoy')
                version.side_effect=ValueError('bad actual version')
                with self.assertRaises(ValueError):n.install(seed,mount,task_id=n.XARRAY_TASK)
                self.assertEqual(entry.read_text(),'# never execute old wrapper\n');self.assertFalse(entry.with_name('regctl.before-local-no-proxy').exists())
                version.side_effect=None;got=n.install(seed,mount,task_id=n.XARRAY_TASK)
                self.assertEqual(version.call_args.args,(real,));self.assertEqual(got['image'],n.XARRAY_IMAGE)
                self.assertEqual(entry.with_name('regctl.before-local-no-proxy').read_text(),'# never execute old wrapper\n')
                self.assertFalse((mount/'env/local-no-proxy/config.json').exists())

    def test_xarray_cold_request_is_explicit_4gib_and_old_requests_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);(home/'aenv').mkdir();(home/'aenv/credentials').write_text('url="http://127.0.0.1:12345"\napi_key="test"\n')
            bodies=[];response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=None);response.status=201
            response.read.return_value=b'{"sandboxID":"00000000-0000-0000-0000-000000000001"}'
            def opened(request,**kw):bodies.append(json.loads(request.data));return response
            with patch.object(cold.urllib.request,'build_opener',return_value=SimpleNamespace(open=opened)):
                cold.create(home,'http://127.0.0.1:12345',n.XARRAY_IMAGE)
                cold.create(home,'http://127.0.0.1:12345',n.XARRAY_IMAGE,local_no_proxy=True)
                for image,memory in ((n.XARRAY_IMAGE,8192),('unknown',4096)):
                    with self.assertRaises(ValueError):cold.create(home,'http://127.0.0.1:12345',image,memory_mib=memory,allow_8gib=True,local_no_proxy=True)
            self.assertNotIn('envVars',bodies[0]);self.assertNotIn('allowInternetAccess',bodies[0])
            self.assertEqual(bodies[1]['envVars'],{k:'' for k in n.PROXY_KEYS});self.assertFalse(bodies[1]['allowInternetAccess'])
            self.assertEqual((bodies[1]['memoryMB'],bodies[1]['cpuCount']),(4096,2))

    def test_outer_routes_task_before_restore_and_rejects_short_mode(self):
        cfg=config();n.require_mode(cfg,True)
        for change in ({'replay_policy':'sphinx-pipeline-v2'},{'replay_policy':'strict'},{'monitor_mode':'off'},{'checkpoint_after_step':22}):
            with self.subTest(change=change),self.assertRaises(ValueError):n.require_mode(dict(cfg,**change),True)
        with tempfile.TemporaryDirectory() as tmp:
            seen=[]
            def metadata(path,**kw):self.assertEqual(kw,dict(task_id=n.XARRAY_TASK));seen.append('metadata')
            def phase(action,path,timeout,**kw):
                seen.append(action)
                if action=='prepare':self.assertEqual(kw,dict(task_profile='xarray6992'))
            def run(argv,**kw):
                if argv[0]=='systemd-run':
                    seen.append('group');self.assertIn('--setenv=ASB_LOCAL_NO_PROXY=on',argv)
                    for k in n.PROXY_KEYS:self.assertIn('--setenv='+k+'=',argv);self.assertFalse(os.environ.get(k))
                return SimpleNamespace(returncode=0)
            argv=['entry',str(ROOT/'configs/platform-xarray6992-host-current.json'),'--persistent-input','--local-no-proxy']
            with patch.dict(os.environ,{k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}),patch.object(outer,'ROOT',Path(tmp)),patch.object(sys,'argv',argv),\
                 patch.object(platform,'preflight'),patch.object(outer.subprocess,'check_output',return_value='systemd'),patch.object(outer.subprocess,'run',side_effect=run),\
                 patch.object(persistent,'run_phase',side_effect=phase),patch.object(n,'prepare_metadata',side_effect=metadata):self.assertEqual(outer.main(),0)
            self.assertEqual(seen,['metadata','prepare','group','release-source'])
        with patch.object(sys,'argv',argv+['--local-no-proxy-check']),patch.object(outer.subprocess,'check_output') as docker,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):outer.main()
            docker.assert_not_called()

    def test_native_uses_actual_cli_policy_and_task_identity_for_all_three(self):
        nodes=next(v for v in shared.tree('validate_isolated_agentenv.py').body if isinstance(v,ast.FunctionDef) and v.name=='main').body
        start=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='local_no_proxy')
        end=next(i for i in range(start,len(nodes)) if isinstance(nodes[i],ast.If) and ast.unparse(nodes[i].test)=='persistent_seed')
        for name,task_id in (('prettier6604',n.PRETTIER_TASK),('sphinx7590',n.SPHINX_TASK),('xarray6992',n.XARRAY_TASK)):
            cfg=platform.configuration(ROOT/f'configs/platform-{name}-host-current.json');args=shared.SphinxLocalTests().native_args(cfg)
            values=dict(os=os,args=args,persistent_seed=Path('/seed'),prettier6604=name=='prettier6604')
            with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'}):shared.execute(nodes[start:end],values)
            self.assertEqual(values['local_task_id'],task_id);self.assertEqual(persistent.native_option(args),Path('/seed'))
        good=shared.SphinxLocalTests().native_args(config())
        for change in ({'python_diagnostic_continuation':False},{'python_sphinx_pipeline_diagnostic':True},{'python_task_profile':'unknown'},{'local_no_proxy_check':True},{'python_checkpoint':True},{'python_monitor_mode':'off'}):
            values=dict(os=os,args=SimpleNamespace(**(vars(good)|change)),persistent_seed=Path('/seed'),prettier6604=False)
            with self.subTest(change=change),patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'}),self.assertRaises(ValueError):
                shared.execute(nodes[start:end],values);persistent.native_option(values['args'])

    def test_leaf_guard_allows_only_complete_approved_xarray_and_preserves_environment(self):
        nodes=next(v for v in shared.tree('run_xarray6992.py').body if isinstance(v,ast.FunctionDef) and v.name=='run').body
        start=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='local_no_proxy')
        good=dict(os=os,sphinx=False,light=True,monitoring=True,pipeline=False,pipeline_version=1,args=SimpleNamespace(task_profile='xarray6992',persistent_sampling=True,config_home='/cfg'),checkpoint=False,continuation=True,cache_control=False,ROOT=ROOT)
        original={k:'http://127.0.0.1:17891' for k in n.PROXY_KEYS}|dict(PATH='/original/bin')
        for enabled in (False,True):
            values=dict(good)
            with patch.dict(os.environ,dict(original,ASB_LOCAL_NO_PROXY='on' if enabled else 'off')):
                shared.execute(nodes[start:start+2],values)
                env=next(i for i,v in enumerate(nodes) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='env');shared.execute(nodes[env:env+2],values)
            for k in n.PROXY_KEYS:self.assertEqual(k not in values['env'],enabled)
        for change in ({'continuation':False},{'pipeline':True},{'checkpoint':True},{'light':False},{'cache_control':True},
                       {'args':SimpleNamespace(task_profile='unknown',persistent_sampling=True)},{'args':SimpleNamespace(task_profile='xarray6992',persistent_sampling=False)}):
            with self.subTest(change=change),patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'}),self.assertRaises(ValueError):shared.execute(nodes[start:start+2],dict(good,**change))

    def test_proxy_probe_precedes_task_and_failed_probe_keeps_guest_registered(self):
        nodes=next(v for v in shared.tree('run_xarray6992.py').body if isinstance(v,ast.FunctionDef) and v.name=='run').body
        body=next(v for v in nodes if isinstance(v,ast.Try)).body
        start=next(i for i,v in enumerate(body) if isinstance(v,ast.Assign) and ast.unparse(v.targets[0])=='sid')
        end=next(i for i in range(start,len(body)) if isinstance(body[i],ast.If) and ast.unparse(body[i].test)=='cache_control')
        values=dict(call=Mock(return_value='00000000-0000-0000-0000-000000000001'),guest=Mock(return_value='guest-no-proxy-ready'),stage='cold-start',sys=sys,ROOT=ROOT,
            args=SimpleNamespace(config_home=Path('/cfg'),service_url='http://127.0.0.1:12345'),cfg=dict(source_image=n.XARRAY_IMAGE),re=re,controller=SimpleNamespace(live={}),local_no_proxy=True,result={},shlex=__import__('shlex'))
        shared.execute(body[start:end],values);self.assertIn('--local-no-proxy',values['call'].call_args.args[1]);self.assertEqual(values['result']['local_no_proxy'],'PASS')
        self.assertIn('/opt/miniconda3/envs/testbed/bin/python',values['guest'].call_args.args[1])
        values['guest'].return_value='bad';values['result']={}
        with self.assertRaises(AssertionError):shared.execute(body[start:end],values)
        self.assertIn(values['sid'],values['controller'].live);self.assertNotIn('local_no_proxy',values['result'])


if __name__=='__main__':unittest.main()
